#!/usr/bin/env python3
"""Real tests for context_engine.py's remember-turn / recall-context /
list-sessions subcommands. Every test uses a real, isolated, temp-file
SQLite database (the script's own conversation_memory table, created by the
script's own _ensure_tables()) -- never the live production database at
/opt/veridian/ai-os/memory/superboss-register.sqlite.

context_engine.py hardcodes DB_PATH as a plain module-level constant (no
env-var override), so per the established convention we importlib-load the
module normally and then monkeypatch the loaded module's DB_PATH global
directly to a temp file before calling any of its functions. Every function
under test (_connect, _now_iso, cmd_remember_turn, cmd_recall_context,
cmd_list_sessions) reads DB_PATH as a module global at call time, so this
monkeypatch is picked up correctly.
"""
import importlib.util
import json
import os
import sqlite3

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "context_engine.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ctx_mod(tmp_path):
    mod = _load("context_engine_sut", SUT_PATH)
    mod.DB_PATH = str(tmp_path / "context_engine_test.sqlite")
    return mod


def run_cmd(mod, argv):
    """Real CLI entry point: build_parser().parse_args() + args.func(args),
    exactly what __main__ does."""
    args = mod.build_parser().parse_args(argv)
    args.func(args)


def _db_conn(mod):
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# remember-turn
# ---------------------------------------------------------------------------

def test_remember_turn_creates_new_session_row_and_first_edge(ctx_mod, capsys):
    run_cmd(ctx_mod, [
        "remember-turn",
        "--session-id", "sess-001",
        "--org-id", "org-alpha",
        "--actor-ref", "user:rajat",
        "--entity-type", "capability",
        "--entity-id", "CAP-20260807-000001-aaaa",
        "--relationship-type", "mentioned",
        "--evidence", "user asked about this capability",
    ])
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "sess-001"
    assert out["remembered"] is True
    assert out["turn_count"] == 1
    assert len(out["entity_relationships"]) == 1
    edge = out["entity_relationships"][0]
    assert edge["related_entity_type"] == "capability"
    assert edge["related_entity_id"] == "CAP-20260807-000001-aaaa"
    assert edge["relationship_type"] == "mentioned"
    assert edge["evidence"] == "user asked about this capability"
    assert "turn_ts" in edge

    # Verify the real row written to the real temp DB, not just the printed JSON.
    conn = _db_conn(ctx_mod)
    row = conn.execute("SELECT * FROM conversation_memory WHERE session_id=?", ("sess-001",)).fetchone()
    conn.close()
    assert row is not None
    assert row["org_id"] == "org-alpha"
    assert row["actor_ref"] == "user:rajat"
    assert row["turn_count"] == 1
    assert row["created_ts"] == row["last_active_ts"]
    rels = json.loads(row["entity_relationships"])
    assert len(rels) == 1
    assert rels[0]["related_entity_id"] == "CAP-20260807-000001-aaaa"


def test_remember_turn_appends_second_turn_and_preserves_order(ctx_mod, capsys):
    run_cmd(ctx_mod, [
        "remember-turn", "--session-id", "sess-002", "--org-id", "org-beta",
        "--entity-type", "capability", "--entity-id", "CAP-1",
        "--relationship-type", "mentioned", "--summary", "initial summary",
    ])
    capsys.readouterr()  # discard first output

    run_cmd(ctx_mod, [
        "remember-turn", "--session-id", "sess-002", "--org-id", "org-beta",
        "--entity-type", "dynamic_chain", "--entity-id", "DC-2",
        "--relationship-type", "acted_on",
    ])
    out = json.loads(capsys.readouterr().out)
    assert out["turn_count"] == 2
    assert len(out["entity_relationships"]) == 2
    # Real ordering: first edge (capability, CAP-1) must precede second (dynamic_chain, DC-2).
    assert out["entity_relationships"][0]["related_entity_id"] == "CAP-1"
    assert out["entity_relationships"][1]["related_entity_id"] == "DC-2"

    conn = _db_conn(ctx_mod)
    row = conn.execute("SELECT * FROM conversation_memory WHERE session_id=?", ("sess-002",)).fetchone()
    conn.close()
    assert row["turn_count"] == 2
    # Summary was not passed on the second call -- real code must preserve the
    # first call's summary rather than clobbering it with None.
    assert row["summary"] == "initial summary"


def test_remember_turn_summary_update_only_when_provided(ctx_mod, capsys):
    run_cmd(ctx_mod, [
        "remember-turn", "--session-id", "sess-003", "--org-id", "org-gamma",
        "--entity-type", "document", "--entity-id", "DOC-1",
        "--relationship-type", "mentioned", "--summary", "v1",
    ])
    capsys.readouterr()
    run_cmd(ctx_mod, [
        "remember-turn", "--session-id", "sess-003", "--org-id", "org-gamma",
        "--entity-type", "document", "--entity-id", "DOC-2",
        "--relationship-type", "mentioned", "--summary", "v2 -- updated",
    ])
    capsys.readouterr()

    conn = _db_conn(ctx_mod)
    row = conn.execute("SELECT summary FROM conversation_memory WHERE session_id=?", ("sess-003",)).fetchone()
    conn.close()
    assert row["summary"] == "v2 -- updated"


# ---------------------------------------------------------------------------
# recall-context
# ---------------------------------------------------------------------------

def test_recall_context_found_returns_real_accumulated_state(ctx_mod, capsys):
    run_cmd(ctx_mod, [
        "remember-turn", "--session-id", "sess-recall", "--org-id", "org-x",
        "--actor-ref", "svc:worker-9", "--entity-type", "capability",
        "--entity-id", "CAP-9", "--relationship-type", "resolved_to",
        "--evidence", "matched by exact id", "--summary", "the running summary",
    ])
    capsys.readouterr()

    run_cmd(ctx_mod, ["recall-context", "--session-id", "sess-recall"])
    out = json.loads(capsys.readouterr().out)
    assert out["found"] is True
    assert out["session_id"] == "sess-recall"
    assert out["org_id"] == "org-x"
    assert out["actor_ref"] == "svc:worker-9"
    assert out["turn_count"] == 1
    assert out["summary"] == "the running summary"
    assert len(out["entity_relationships"]) == 1
    assert out["entity_relationships"][0]["related_entity_id"] == "CAP-9"
    assert out["entity_relationships"][0]["evidence"] == "matched by exact id"


def test_recall_context_not_found_for_unknown_session(ctx_mod, capsys):
    run_cmd(ctx_mod, ["recall-context", "--session-id", "sess-does-not-exist"])
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "session_id": "sess-does-not-exist",
        "found": False,
        "entity_relationships": [],
        "summary": None,
    }


# ---------------------------------------------------------------------------
# list-sessions
# ---------------------------------------------------------------------------

def _seed_session(mod, session_id, org_id, last_active_ts, turn_count=1):
    """Deterministic raw-SQL fixture seeding into the disposable temp DB
    (this repo's established test convention) -- avoids relying on real-time
    sqlite 'now' resolution to control ordering."""
    conn = sqlite3.connect(mod.DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_memory (
        session_id TEXT PRIMARY KEY, org_id TEXT NOT NULL, actor_ref TEXT,
        created_ts TEXT NOT NULL, last_active_ts TEXT NOT NULL,
        turn_count INTEGER NOT NULL DEFAULT 0,
        entity_relationships TEXT NOT NULL DEFAULT '[]', summary TEXT)""")
    conn.execute(
        "INSERT INTO conversation_memory (session_id, org_id, actor_ref, created_ts, "
        "last_active_ts, turn_count, entity_relationships, summary) VALUES (?,?,?,?,?,?,?,?)",
        (session_id, org_id, None, last_active_ts, last_active_ts, turn_count, "[]", None),
    )
    conn.commit()
    conn.close()


def test_list_sessions_orders_by_last_active_desc_and_respects_limit(ctx_mod, capsys):
    _seed_session(ctx_mod, "s-old", "org-1", "2026-08-01T00:00:00.000000Z")
    _seed_session(ctx_mod, "s-mid", "org-1", "2026-08-05T00:00:00.000000Z")
    _seed_session(ctx_mod, "s-new", "org-1", "2026-08-07T00:00:00.000000Z")

    run_cmd(ctx_mod, ["list-sessions"])
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 3
    ids_in_order = [s["session_id"] for s in out["sessions"]]
    assert ids_in_order == ["s-new", "s-mid", "s-old"]

    run_cmd(ctx_mod, ["list-sessions", "--limit", "2"])
    out2 = json.loads(capsys.readouterr().out)
    assert out2["count"] == 2
    assert [s["session_id"] for s in out2["sessions"]] == ["s-new", "s-mid"]


def test_list_sessions_filters_by_org_id(ctx_mod, capsys):
    _seed_session(ctx_mod, "s-a1", "org-a", "2026-08-01T00:00:00.000000Z")
    _seed_session(ctx_mod, "s-a2", "org-a", "2026-08-02T00:00:00.000000Z")
    _seed_session(ctx_mod, "s-b1", "org-b", "2026-08-03T00:00:00.000000Z")

    run_cmd(ctx_mod, ["list-sessions", "--org-id", "org-b"])
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["sessions"][0]["session_id"] == "s-b1"
    assert out["sessions"][0]["org_id"] == "org-b"


def test_list_sessions_empty_db_returns_empty_list(ctx_mod, capsys):
    run_cmd(ctx_mod, ["list-sessions"])
    out = json.loads(capsys.readouterr().out)
    assert out == {"count": 0, "sessions": []}
