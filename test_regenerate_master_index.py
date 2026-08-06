#!/usr/bin/env python3
"""Real tests for regenerate_master_index.py's sweep_wiring_registry_coverage()
-- the "unregistered mentions sweep against wiring_registry" absorbed item
(owner absolute stop-work order, task-20260806-165921). Every test uses a
real, isolated, temp-file SQLite database -- never the live production
database. Read-only function under test: never mutates unregistered_mentions
or wiring_registry.
"""
import importlib.util
import os
import sqlite3
import sys

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "regenerate_master_index_under_test", os.path.join(SCRIPT_DIR, "regenerate_master_index.py"))
rmi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rmi)

# A file guaranteed to exist under rmi.KNOWN_ROOTS[0] ("/opt/veridian/") in
# any real checkout of this repo, without monkeypatching KNOWN_ROOTS (which
# is a fixed module-level constant real callers rely on, not meant to be
# overridden) -- this script's own real live source file.
REAL_RELATIVE_PATH = "scripts/superboss-register.py"


def _make_db(tmp_path, mention_rows, wiring_rows):
    """mention_rows: list of (mentioned_path, status).
    wiring_rows: list of (entity_id, path)."""
    db_path = str(tmp_path / "sweep_test.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE unregistered_mentions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT, software_task_id TEXT, mentioned_path TEXT, status TEXT)"
    )
    for path, status in mention_rows:
        conn.execute(
            "INSERT INTO unregistered_mentions (ts, software_task_id, mentioned_path, status) "
            "VALUES (?,?,?,?)",
            ("2026-08-06T12:00:00+00:00", "task-x", path, status),
        )
    conn.execute("CREATE TABLE wiring_registry (entity_id TEXT PRIMARY KEY, path TEXT)")
    for entity_id, path in wiring_rows:
        conn.execute("INSERT INTO wiring_registry (entity_id, path) VALUES (?, ?)", (entity_id, path))
    conn.commit()
    conn.close()
    return db_path


def test_flags_real_disk_resolved_path_missing_from_wiring_registry(tmp_path):
    """A real, disk-resolvable path already RESOLVED_AUTO_REGISTERED into
    system_index (by resolve_unregistered_mentions()) but with zero row in
    wiring_registry must show up as a real, honest coverage gap -- the exact
    scenario this sweep exists to catch."""
    db_path = _make_db(
        tmp_path,
        mention_rows=[(REAL_RELATIVE_PATH, "RESOLVED_AUTO_REGISTERED:IDX-auto-x")],
        wiring_rows=[],
    )
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    result = rmi.sweep_wiring_registry_coverage(cur)
    conn.close()

    assert result["total_resolvable_mentions_checked"] == 1
    assert result["covered_in_wiring_registry"] == 0
    assert result["not_covered_in_wiring_registry"] == 1
    assert result["not_covered"][0]["mentioned_as"] == REAL_RELATIVE_PATH
    assert result["not_covered"][0]["path"].endswith(REAL_RELATIVE_PATH)


def test_does_not_flag_a_path_that_is_genuinely_covered(tmp_path):
    real_abs = os.path.join(rmi.KNOWN_ROOTS[0], REAL_RELATIVE_PATH)
    db_path = _make_db(
        tmp_path,
        mention_rows=[(REAL_RELATIVE_PATH, "RESOLVED_AUTO_REGISTERED:IDX-auto-x")],
        wiring_rows=[("script-superboss-register-py", real_abs)],
    )
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    result = rmi.sweep_wiring_registry_coverage(cur)
    conn.close()

    assert result["covered_in_wiring_registry"] == 1
    assert result["not_covered_in_wiring_registry"] == 0
    assert result["not_covered"] == []


def test_ignores_paths_that_do_not_resolve_on_disk(tmp_path):
    """A path that resolve_unregistered_mentions() itself could never resolve
    (genuinely missing on disk under every known root) must not be double-
    counted as a wiring_registry gap -- that's a separate, already-surfaced
    failure mode (still_flagged), not this sweep's concern."""
    db_path = _make_db(
        tmp_path,
        mention_rows=[("scripts/this_file_genuinely_does_not_exist_anywhere.py", "NEEDS_REGISTRATION")],
        wiring_rows=[],
    )
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    result = rmi.sweep_wiring_registry_coverage(cur)
    conn.close()

    assert result["total_resolvable_mentions_checked"] == 0
    assert result["not_covered"] == []


def test_dedupes_repeated_mentions_of_the_same_path(tmp_path):
    db_path = _make_db(
        tmp_path,
        mention_rows=[
            (REAL_RELATIVE_PATH, "NEEDS_REGISTRATION"),
            (REAL_RELATIVE_PATH, "RESOLVED_AUTO_REGISTERED:IDX-auto-x"),
        ],
        wiring_rows=[],
    )
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    result = rmi.sweep_wiring_registry_coverage(cur)
    conn.close()

    assert result["total_resolvable_mentions_checked"] == 1
    assert result["not_covered_in_wiring_registry"] == 1


def test_empty_backlog_honest_zero_counts(tmp_path):
    db_path = _make_db(tmp_path, mention_rows=[], wiring_rows=[])
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    result = rmi.sweep_wiring_registry_coverage(cur)
    conn.close()

    assert result["total_resolvable_mentions_checked"] == 0
    assert result["covered_in_wiring_registry"] == 0
    assert result["not_covered_in_wiring_registry"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
