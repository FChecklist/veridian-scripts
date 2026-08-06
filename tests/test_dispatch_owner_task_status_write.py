#!/usr/bin/env python3
"""UMR-20260806-085144-9c63 (prevention side of the owner_dispatch_gateway
stuck-at-'queued' finding; reconciliation of already-stale rows is PR #147 /
UMR-20260806-082646-3aba, out of scope here).

Covers:
  1. superboss-register.py's new mark-umr-dispatched / mark-umr-terminal CLI
     subcommands, against a real, isolated, temp-file SQLite DB seeded with
     the real schema -- never the live production database.
  2. dispatch-owner-task.sh's own relay-succeeds / relay-fails branching,
     exercised end-to-end against that same isolated scratch DB, with a
     fake `tmux` shim on PATH (deterministic, no real tmux server /
     real live session dependency in CI) simulating both branches.
"""
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_TMUX = """#!/usr/bin/env bash
# Deterministic stand-in for tmux, used only under tests/. Logs every
# invocation to $TMUX_FAKE_LOG. `has-session -t <name>` succeeds only for
# the session name(s) listed (newline-separated) in $TMUX_FAKE_LIVE_SESSIONS.
set -euo pipefail
echo "$@" >> "$TMUX_FAKE_LOG"
if [ "$1" = "has-session" ]; then
  shift
  target=""
  while [ $# -gt 0 ]; do
    if [ "$1" = "-t" ]; then target="$2"; fi
    shift
  done
  if [ -f "$TMUX_FAKE_LIVE_SESSIONS" ] && grep -qxF "$target" "$TMUX_FAKE_LIVE_SESSIONS"; then
    exit 0
  fi
  exit 1
fi
exit 0
"""


def _seed_full_schema(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_dispatch_status_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    sbr.DB_PATH = path
    sbr.init_db()
    return sbr


def _row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT umr_id, status, ts_dispatched, ts_completed, unit_name, reason "
        "FROM umr_tasks WHERE umr_id=?", (umr_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_full_schema(path)
        yield path


def _insert_queued_row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,datetime('now'),2,'queued','owner_dispatch_gateway','veridian_task_create',"
        "'{}','{}','{}')",
        (umr_id, umr_id + "-identity"),
    )
    conn.commit()
    conn.close()


def _run_sbr(args, scratch_db, extra_env=None):
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "superboss-register.py"] + args,
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True,
    )


# --- superboss-register.py CLI subcommands -----------------------------

def test_mark_umr_dispatched_writes_ts_dispatched_and_status(scratch_db):
    umr_id = "UMR-TEST-9c63-dispatched-0001"
    _insert_queued_row(scratch_db, umr_id)
    before = _row(scratch_db, umr_id)
    assert before["status"] == "queued"
    assert before["ts_dispatched"] is None

    out = _run_sbr(["mark-umr-dispatched", "--umr-id", umr_id,
                     "--unit-name", "veridian-worker@test.service"], scratch_db)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["status"] == "dispatched"
    assert payload["ts_dispatched"]

    after = _row(scratch_db, umr_id)
    assert after["status"] == "dispatched"
    assert after["ts_dispatched"] is not None
    assert after["unit_name"] == "veridian-worker@test.service"
    assert after["ts_completed"] is None


@pytest.mark.parametrize("status", ["completed", "failed", "killed"])
def test_mark_umr_terminal_writes_ts_completed_and_status(scratch_db, status):
    umr_id = f"UMR-TEST-9c63-terminal-{status}"
    _insert_queued_row(scratch_db, umr_id)

    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id,
                     "--status", status, "--reason", f"real {status} reason"], scratch_db)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["status"] == status
    assert payload["ts_completed"]

    after = _row(scratch_db, umr_id)
    assert after["status"] == status
    assert after["ts_completed"] is not None
    assert after["reason"] == f"real {status} reason"


def test_mark_umr_dispatched_then_terminal_preserves_ts_dispatched(scratch_db):
    """A row that was marked dispatched, then later marked terminal, must
    keep its original real ts_dispatched -- update_umr_task()'s partial
    UPDATE must never clobber a column it wasn't asked to touch."""
    umr_id = "UMR-TEST-9c63-preserve-ts-dispatched"
    _insert_queued_row(scratch_db, umr_id)
    _run_sbr(["mark-umr-dispatched", "--umr-id", umr_id], scratch_db)
    ts_dispatched = _row(scratch_db, umr_id)["ts_dispatched"]

    _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed"], scratch_db)
    after = _row(scratch_db, umr_id)
    assert after["ts_dispatched"] == ts_dispatched
    assert after["status"] == "completed"


def test_mark_umr_terminal_rejects_non_terminal_status(scratch_db):
    umr_id = "UMR-TEST-9c63-bad-status"
    _insert_queued_row(scratch_db, umr_id)
    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "queued"], scratch_db)
    assert out.returncode != 0
    assert "invalid choice" in out.stderr


# --- dispatch-owner-task.sh end-to-end, real code path, fake tmux ------

@pytest.fixture()
def fake_tmux_path(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    tmux_path = bin_dir / "tmux"
    tmux_path.write_text(FAKE_TMUX)
    tmux_path.chmod(tmux_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(bin_dir)


def _run_wrapper(scratch_db, fake_tmux_path, tmux_session, live_sessions, tmp_path, extra_args=()):
    live_file = tmp_path / "live_sessions.txt"
    live_file.write_text("\n".join(live_sessions) + ("\n" if live_sessions else ""))
    log_file = tmp_path / "tmux.log"
    env = dict(os.environ)
    env["PATH"] = fake_tmux_path + os.pathsep + env["PATH"]
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    env["DISPATCH_TMUX_SESSION"] = tmux_session
    env["TMUX_FAKE_LIVE_SESSIONS"] = str(live_file)
    env["TMUX_FAKE_LOG"] = str(log_file)
    result = subprocess.run(
        ["./dispatch-owner-task.sh", "unit-test dispatch title",
         "unit-test dispatch prompt body, unique-" + tmux_session, "2",
         "claude_code_cli", "compliance-tracker", *extra_args],
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True,
    )
    tmux_log = log_file.read_text() if log_file.exists() else ""
    return result, tmux_log


def test_wrapper_relay_succeeds_marks_dispatched(scratch_db, fake_tmux_path, tmp_path):
    result, tmux_log = _run_wrapper(
        scratch_db, fake_tmux_path, "fake-claude-session",
        live_sessions=["fake-claude-session"], tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "RELAYED into tmux session 'fake-claude-session'" in result.stdout
    assert "MARKED DISPATCHED" in result.stdout
    assert "send-keys -t fake-claude-session" in tmux_log

    umr_id = None
    for line in result.stdout.splitlines():
        if line.startswith("DISPATCHED:"):
            umr_id = line.split("umr_id=")[1].split()[0]
    assert umr_id
    row = _row(scratch_db, umr_id)
    assert row["status"] == "dispatched"
    assert row["ts_dispatched"] is not None
    assert row["ts_completed"] is None


def test_wrapper_relay_fails_marks_failed_with_reason(scratch_db, fake_tmux_path, tmp_path):
    result, tmux_log = _run_wrapper(
        scratch_db, fake_tmux_path, "fake-session-that-does-not-exist",
        live_sessions=[], tmp_path=tmp_path,
    )
    assert "WARNING: tmux session 'fake-session-that-does-not-exist' not found" in result.stderr
    assert "MARKED FAILED" in result.stderr

    umr_id = None
    for line in result.stdout.splitlines():
        if line.startswith("DISPATCHED:"):
            umr_id = line.split("umr_id=")[1].split()[0]
    assert umr_id
    row = _row(scratch_db, umr_id)
    assert row["status"] == "failed"
    assert row["ts_completed"] is not None
    assert "fake-session-that-does-not-exist" in row["reason"]
    assert row["ts_dispatched"] is None


def test_wrapper_relay_includes_mandatory_completion_instruction(scratch_db, fake_tmux_path, tmp_path):
    """UMR-20260806-112013-088f: the real gap this covers is that
    mark-umr-terminal existed and was documented, but nothing structural
    ever told the session doing the relayed work to call it. The fix makes
    the relayed prompt text itself carry a mandatory, UMR-id-specific final
    instruction naming the exact real command -- assert it actually reaches
    the tmux send-keys call (via the fake tmux's real invocation log), not
    just that the script exits 0."""
    result, tmux_log = _run_wrapper(
        scratch_db, fake_tmux_path, "fake-claude-session",
        live_sessions=["fake-claude-session"], tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    umr_id = None
    for line in result.stdout.splitlines():
        if line.startswith("DISPATCHED:"):
            umr_id = line.split("umr_id=")[1].split()[0]
    assert umr_id

    assert "MANDATORY FINAL STEP for " + umr_id in tmux_log
    assert f"mark-umr-terminal --umr-id {umr_id} --status completed" in tmux_log
    assert "never record a genuine failure as a success" in tmux_log
    # The original prompt body must still be present, unaltered, ahead of
    # the appended instruction -- this is additive, not a replacement.
    assert "unit-test dispatch prompt body, unique-fake-claude-session" in tmux_log


def test_wrapper_no_relay_leaves_row_queued_untouched(scratch_db, fake_tmux_path, tmp_path):
    """--no-relay is the pure background-worker path (out of scope for this
    UMR's dispatched/failed bookkeeping -- that row's real lifecycle is
    driven by its own systemd worker, not this tmux relay branch), so it
    must NOT be marked dispatched or failed by this script."""
    result, tmux_log = _run_wrapper(
        scratch_db, fake_tmux_path, "irrelevant-session",
        live_sessions=["irrelevant-session"], tmp_path=tmp_path,
        extra_args=("--no-relay",),
    )
    assert result.returncode == 0, result.stderr
    assert tmux_log == ""

    umr_id = None
    for line in result.stdout.splitlines():
        if line.startswith("DISPATCHED:"):
            umr_id = line.split("umr_id=")[1].split()[0]
    assert umr_id
    row = _row(scratch_db, umr_id)
    assert row["status"] == "queued"
    assert row["ts_dispatched"] is None
    assert row["ts_completed"] is None
