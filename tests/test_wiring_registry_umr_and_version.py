#!/usr/bin/env python3
"""Real tests for wiring_registry's originating_umr/script_version columns
(UMR-20260806-035541, Owner directive "real PM cycle script registry" --
item 1's script-registry extension + item 2's UMR backfill). Every test uses
a real, isolated, temp-file SQLite database -- never the live production
database.
"""
import importlib.util
import os
import sqlite3
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_wiring_umr", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _seed_scratch_db(path, sbr):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_wiring_registry_table(conn)
    conn.close()


def test_fresh_db_has_originating_umr_and_script_version_columns():
    sbr = _load_sbr()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(path, sbr)
        conn = sqlite3.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
        conn.close()
        assert "originating_umr" in cols
        assert "script_version" in cols
        print("PASS: test_fresh_db_has_originating_umr_and_script_version_columns")


def test_register_entity_round_trips_umr_and_version():
    sbr = _load_sbr()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(path, sbr)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        entity = {
            "entity_id": "script-testfoo.py",
            "entity_type": "script",
            "source_system": "server",
            "path": "/opt/veridian/scripts/testfoo.py",
            "relationships": [],
            "last_verified_ts": "2026-08-06T00:00:00+00:00",
            "verification_status": "VERIFIED_MATCH",
            "source_ref": ["software_catalog"],
            "originating_umr": "UMR-20260806-035541-abcd",
            "script_version": "v2",
        }
        sbr.register_entity_row(conn, entity)
        conn.commit()
        row = conn.execute("SELECT * FROM wiring_registry WHERE entity_id = ?", ("script-testfoo.py",)).fetchone()
        conn.close()
        assert row["originating_umr"] == "UMR-20260806-035541-abcd"
        assert row["script_version"] == "v2"
        print("PASS: test_register_entity_round_trips_umr_and_version")


def test_register_entity_defaults_umr_and_version_to_null_when_absent():
    """Real requirement: never invented -- an entity dict that never mentions
    originating_umr/script_version must store real NULL, not a fabricated
    value, and must not raise (both fields are optional, same convention as
    content_hash)."""
    sbr = _load_sbr()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(path, sbr)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        entity = {
            "entity_id": "engine-01",
            "entity_type": "engine",
            "source_system": "server",
            "path": None,
            "relationships": [],
            "last_verified_ts": "2026-08-06T00:00:00+00:00",
            "verification_status": "UNVERIFIED",
            "source_ref": [],
        }
        sbr.register_entity_row(conn, entity)
        conn.commit()
        row = conn.execute("SELECT * FROM wiring_registry WHERE entity_id = ?", ("engine-01",)).fetchone()
        conn.close()
        assert row["originating_umr"] is None
        assert row["script_version"] is None
        print("PASS: test_register_entity_defaults_umr_and_version_to_null_when_absent")


def test_migration_adds_columns_to_pre_existing_table_missing_them():
    """Real requirement: a live DB created before this task's own migration
    (i.e. missing originating_umr/script_version) must pick up both columns
    via ALTER TABLE, not require a fresh DB."""
    sbr = _load_sbr()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        # Real pre-existing schema (content_hash already added, this task's
        # two new columns not yet applied) -- mirrors the exact shape
        # _migrate_wiring_registry_content_hash's own tests already use.
        conn.execute("""CREATE TABLE wiring_registry (
            entity_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            source_system TEXT NOT NULL,
            path TEXT,
            relationships TEXT NOT NULL DEFAULT '[]',
            last_verified_ts TEXT NOT NULL,
            verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
            source_ref TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            content_hash TEXT
        )""")
        conn.commit()
        cols_before = {r[1] for r in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
        assert "originating_umr" not in cols_before

        sbr._migrate_wiring_registry_umr_and_version(conn)
        cols_after = {r[1] for r in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
        conn.close()
        assert "originating_umr" in cols_after
        assert "script_version" in cols_after
        print("PASS: test_migration_adds_columns_to_pre_existing_table_missing_them")


def test_migration_is_idempotent_and_safe_on_missing_table():
    sbr = _load_sbr()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        # No wiring_registry table at all yet -- must be a safe no-op.
        sbr._migrate_wiring_registry_umr_and_version(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='wiring_registry'"
        ).fetchone()
        assert row is None

        sbr._ensure_wiring_registry_table(conn)
        # Calling again on an already-migrated table must not raise (no
        # duplicate-column error).
        sbr._migrate_wiring_registry_umr_and_version(conn)
        sbr._migrate_wiring_registry_umr_and_version(conn)
        conn.close()
        print("PASS: test_migration_is_idempotent_and_safe_on_missing_table")


if __name__ == "__main__":
    test_fresh_db_has_originating_umr_and_script_version_columns()
    test_register_entity_round_trips_umr_and_version()
    test_register_entity_defaults_umr_and_version_to_null_when_absent()
    test_migration_adds_columns_to_pre_existing_table_missing_them()
    test_migration_is_idempotent_and_safe_on_missing_table()
