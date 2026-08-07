#!/usr/bin/env python3
"""Real tests for batch-import-conversation-log.py.

Every test uses a real, throwaway temp-file SQLite database seeded with the
REAL instructions/actions schema (obtained by actually running
superboss-register.py's own init_db() against the temp file -- not a
simplified guess at the schema) and a real temp NDJSON fixture file. Never
touches the live DB at /opt/veridian/ai-os/memory/superboss-register.sqlite.

batch-import-conversation-log.py reads
`DB_PATH = os.environ.get("SUPERBOSS_REGISTER_DB", ...)` at MODULE IMPORT
TIME, so every helper here sets/restores that env var tightly around the
import of a *fresh* module instance so each test gets its own DB_PATH
binding without leaking into other tests.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import uuid

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "batch-import-conversation-log.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _with_env(var, value, fn):
    """Run fn() with os.environ[var] temporarily set to value, always
    restoring the prior value (or absence) afterward -- so DB_PATH-binding
    imports never leak state across tests."""
    prev = os.environ.get(var)
    os.environ[var] = value
    try:
        return fn()
    finally:
        if prev is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = prev


def _make_scratch_db_with_real_schema():
    """Create a real throwaway sqlite file with a REAL umr_tasks table
    (built by the real, unmodified _ensure_umr_table(), same convention as
    test_apply_owner_dispatch_status_corrections.py -- not a hand-guessed
    stub, because superboss-register.py's own fast-path schema check in
    _ensure_umr_table() rejects any pre-existing umr_tasks table that
    doesn't already match the full real column set), which satisfies
    resolve_superboss_db_path()'s existence/header/schema checks. Then runs
    the REAL init_db() (loaded fresh, DB_PATH bound to this temp file)
    against it to get the real instructions/actions/work_items schema
    including the FTS5 shadow tables + triggers.

    The first bootstrap module load below happens with SUPERBOSS_REGISTER_DB
    unset, so its own module-level DB_PATH resolves against the real,
    live default path purely as an import-time formality (a read-only
    resolve_superboss_db_path() check) -- _ensure_umr_table(conn) is called
    with an EXPLICIT conn argument pointed at our temp file, so nothing is
    ever written to the live DB."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()

    bootstrap = _load(f"sbr_bootstrap_{uuid.uuid4().hex}", SBR_PATH)
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    bootstrap._ensure_umr_table(conn)
    conn.commit()
    conn.close()

    def _bootstrap_full_schema():
        sbr = _load(f"sbr_full_schema_{uuid.uuid4().hex}", SBR_PATH)
        sbr.init_db()

    _with_env("SUPERBOSS_REGISTER_DB", tmp.name, _bootstrap_full_schema)
    return tmp.name


def _load_batch_import_bound_to(db_path):
    """Load a fresh instance of the module-under-test with its module-level
    DB_PATH bound to db_path (real behavior: DB_PATH is read from the env
    var at import time)."""
    return _with_env(
        "SUPERBOSS_REGISTER_DB", db_path,
        lambda: _load(f"sut_batch_import_{uuid.uuid4().hex}", SUT_PATH),
    )


def _write_ndjson(lines):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ndjson", delete=False, encoding="utf-8"
    )
    tmp.write("\n".join(lines) + "\n")
    tmp.close()
    return tmp.name


def _query_all(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _new_id() monotonic uniqueness (this was a real, confirmed, fixed bug per
# the function's own docstring: 395 rows in one process hit a real
# birthday-paradox UNIQUE-constraint collision before the counter was added)
# ---------------------------------------------------------------------------

def test_new_id_monotonically_unique_across_many_calls_same_process():
    db_path = tempfile.mktemp(suffix=".sqlite")  # never opened by this test
    mod = _load_batch_import_bound_to(db_path)

    n = 5000
    ids = [mod._new_id("INS") for _ in range(n)]

    assert len(set(ids)) == n, "duplicate IDs generated -- real collision regression"
    # every id carries the real prefix and a zero-padded, strictly
    # increasing 6-digit counter suffix regardless of ts/rand collisions
    counters = [int(i.rsplit("-", 1)[1]) for i in ids]
    assert counters == sorted(counters) == list(range(1, n + 1))
    assert all(i.startswith("INS-") for i in ids)


# ---------------------------------------------------------------------------
# main() happy path: real NDJSON in, real DB rows + real printed counts out
# ---------------------------------------------------------------------------

def test_main_happy_path_real_inserts_and_real_printed_counts(capsys, monkeypatch):
    db_path = _make_scratch_db_with_real_schema()
    mod = _load_batch_import_bound_to(db_path)

    ndjson_path = _write_ndjson([
        json.dumps({
            "type": "user_prompt", "session_id": "sess-abc",
            "ts": "2026-08-07T09:00:00+00:00",
            "text": "How do I deploy?", "prompt_id": "p1",
        }),
        json.dumps({
            "type": "tool_use", "session_id": "sess-abc",
            "ts": "2026-08-07T09:00:05+00:00",
            "tool_name": "Bash", "command": "ls -la /opt/veridian",
            "output_excerpt": "total 24\ndrwxr-xr-x", "prompt_id": "p1",
        }),
        json.dumps({
            "type": "turn_end", "session_id": "sess-abc",
            "ts": "2026-08-07T09:00:10+00:00",
            "assistant_message_excerpt": "Ran ls, found directories.",
            "prompt_id": "p1",
        }),
        '{"type": "user_prompt", "text": "broken',  # malformed JSON
        json.dumps({
            "type": "heartbeat", "session_id": "sess-abc",
            "ts": "2026-08-07T09:00:20+00:00",
        }),  # unknown/unhandled type -> skipped
        "",  # blank line, silently ignored (not counted anywhere)
        json.dumps({
            "type": "user_prompt", "session_id": "sess-xyz",
            "backfill": True, "text": "Old backfilled prompt",
            "prompt_id": "p2",
        }),
    ])

    capsys.readouterr()  # drain init_db()'s own "{"ok": true, ...}" print from schema setup above
    monkeypatch.setattr(sys, "argv", ["batch-import-conversation-log.py", ndjson_path])
    mod.main()

    printed = json.loads(capsys.readouterr().out.strip())
    assert printed == {
        "instructions": 2, "actions": 2, "skipped": 1, "malformed_lines": 1,
    }, printed

    instructions = _query_all(
        db_path, "SELECT * FROM instructions ORDER BY ts"
    )
    assert len(instructions) == 2
    p1_row = next(r for r in instructions if json.loads(r["metadata_json"])["prompt_id"] == "p1")
    assert p1_row["raw_text"] == "How do I deploy?"
    assert p1_row["instruction_id"].startswith("INS-")
    assert p1_row["utm_source"] == "owner"
    assert p1_row["utm_medium"] == "claude_code_cli_hook"
    assert p1_row["utm_campaign"] == "conversation-log-auto"
    assert p1_row["utm_content"] == "user_prompt"
    assert p1_row["utm_term"] == "hook_auto,user_prompt"

    p2_row = next(r for r in instructions if json.loads(r["metadata_json"])["prompt_id"] == "p2")
    assert p2_row["raw_text"] == "Old backfilled prompt"
    assert p2_row["utm_campaign"] == "conversation-log-backfill"
    assert p2_row["utm_term"] == "backfill,user_prompt"

    actions = _query_all(db_path, "SELECT * FROM actions ORDER BY ts")
    assert len(actions) == 2
    tool_row = next(r for r in actions if "tool:Bash" in r["utm_content"])
    assert tool_row["action_id"].startswith("ACT-")
    assert tool_row["utm_content"] == "tool:Bash cmd:ls -la /opt/veridian"
    assert tool_row["result"] == "total 24\ndrwxr-xr-x"
    assert tool_row["utm_term"] == "hook_auto,tool_use,bash"
    meta = json.loads(tool_row["metadata_json"])
    assert meta == {"prompt_id": "p1", "tool_name": "Bash"}

    turn_row = next(r for r in actions if r["utm_content"] == "assistant_response")
    assert turn_row["result"] == "Ran ls, found directories."
    assert turn_row["utm_term"] == "hook_auto,turn_end,response"

    # real FTS5 shadow tables were actually populated by the real triggers
    # from the real schema (not a simplified guess)
    fts_count = _query_all(db_path, "SELECT COUNT(*) AS c FROM instructions_fts")[0]["c"]
    assert fts_count == 2
    match = _query_all(
        db_path,
        "SELECT instruction_id FROM instructions_fts WHERE instructions_fts MATCH ?",
        ("deploy",),
    )
    assert len(match) == 1
    assert match[0]["instruction_id"] == p1_row["instruction_id"]


def test_main_malformed_lines_increment_counter_without_crashing_batch(monkeypatch):
    db_path = _make_scratch_db_with_real_schema()
    mod = _load_batch_import_bound_to(db_path)

    ndjson_path = _write_ndjson([
        "{not valid json at all",
        json.dumps({"type": "user_prompt", "text": "first valid", "prompt_id": "a"}),
        "]]] garbage [[[",
        json.dumps({"type": "user_prompt", "text": "second valid", "prompt_id": "b"}),
        "{\"unterminated\": ",
    ])

    monkeypatch.setattr(sys, "argv", ["prog", ndjson_path])
    mod.main()  # must not raise despite 3 malformed lines

    instructions = _query_all(db_path, "SELECT raw_text FROM instructions ORDER BY raw_text")
    assert [r["raw_text"] for r in instructions] == ["first valid", "second valid"]


def test_main_valid_json_non_object_line_crashes_whole_batch_uncaught(monkeypatch):
    """Documents a genuine bug (not fixed here, per instructions): main()
    only catches json.JSONDecodeError around json.loads(line). A line that
    IS valid JSON but is not an object (e.g. a bare number, string, or
    array -- all legal NDJSON) parses fine, then `ev.get("type")` is called
    on a non-dict and raises an uncaught AttributeError, which propagates
    out of main() and aborts the ENTIRE batch (including any valid rows
    already staged in the still-open, now-never-committed transaction),
    instead of being counted in malformed_lines/skipped like a merely
    malformed line would be. See batch-import-conversation-log.py lines
    96-101."""
    db_path = _make_scratch_db_with_real_schema()
    mod = _load_batch_import_bound_to(db_path)

    ndjson_path = _write_ndjson([
        json.dumps({"type": "user_prompt", "text": "would have been valid", "prompt_id": "z"}),
        "42",  # valid JSON, not an object -> ev.get(...) raises AttributeError
    ])

    monkeypatch.setattr(sys, "argv", ["prog", ndjson_path])
    import pytest
    with pytest.raises(AttributeError):
        mod.main()

    # and because the whole transaction never commits, even the earlier
    # valid row is lost -- confirms this is a real, user-visible failure
    # mode, not merely a cosmetic exception.
    instructions = _query_all(db_path, "SELECT raw_text FROM instructions")
    assert instructions == []


def test_main_missing_argv_prints_usage_error_and_exits_1(capsys, monkeypatch):
    db_path = tempfile.mktemp(suffix=".sqlite")
    mod = _load_batch_import_bound_to(db_path)

    monkeypatch.setattr(sys, "argv", ["batch-import-conversation-log.py"])
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert "usage" in out["error"]


def test_main_nonexistent_ndjson_path_prints_error_and_exits_1(capsys, monkeypatch):
    db_path = tempfile.mktemp(suffix=".sqlite")
    mod = _load_batch_import_bound_to(db_path)

    missing_path = "/tmp/does-not-exist-" + uuid.uuid4().hex + ".ndjson"
    monkeypatch.setattr(sys, "argv", ["prog", missing_path])
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["error"] == f"file not found: {missing_path}"


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-v"]))
