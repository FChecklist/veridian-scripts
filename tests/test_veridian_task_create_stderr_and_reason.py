#!/usr/bin/env python3
"""Real regression test for UMR-20260813-165729 (addendum to P1
UMR-20260806-171945-5767, sibling of UMR-20260813-165620-aac7).

Real incident this backstops: three real veridian_task_create rows
(UMR-20260813-145511-5aca, UMR-20260813-145710-860b, UMR-20260813-161723-6c7d)
reached a terminal status=failed today with outputs_json shaped
{'new_task_id': None, 'returncode': 1, 'stderr': ''} and reason left as the
literal string 'queued' -- self-describing as evidence-bearing
(evidence_keys=['new_task_id','returncode','stderr']) while carrying zero
real evidence.

Real, live-reproduced root cause (see this task's PR body for the full
reproduction): veridian-task.py's cmd_create() writes its own real error
text via plain `print(...)` -- STDOUT, not stderr -- before every one of
its `sys.exit(1)` calls (e.g. "ERROR: repo not found at ..."). The specific
trigger for these 3 rows: /opt/veridian/repos/veridian-scripts (the base
git clone `git worktree add` needs for repo='veridian-scripts') was missing
from disk while 5 of its own worktrees still referenced it -- confirmed live
via `git worktree list` inside one of those worktrees returning
"fatal: not a git repository". _perform_spawn()'s veridian_task_create
branch only ever captured r.stderr[:500], discarding the one real
diagnosable stream entirely, and dispatch_one()'s own call site always
passed reason=None into update_umr_task() -- which only touches columns
actually passed as kwargs -- so a row's reason column was left at its
submit-time value ('queued') forever, even once terminal.

This test's `_run()` mock reproduces that exact real shape (returncode=1,
stdout carries the real error text, stderr empty) and asserts BOTH real
fixes: a non-empty captured tail from at least one real stream, and a
reason that is never the literal string 'queued' once the row is terminal.
It FAILS against the pre-fix code (which drops the mocked stdout entirely
and never touches the reason column here).

Every real `gh`/subprocess call is mocked at the `_run()` subprocess
boundary (same convention as tests/test_target_pr_dispatch_time_recheck.py).
Every DB touch uses a real, isolated, temp-file SQLite database (never the
live production one), same convention as this repo's other resource_governor
end-to-end tests.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from unittest import mock

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _schema_helpers():
    spec = importlib.util.spec_from_file_location(
        "sbr_helpers_taskcreate_stderr", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _new_conn(scratch_db):
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_scratch_db(scratch_db, sbr):
    conn = _new_conn(scratch_db)
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()


def _load_rg(name, env):
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# The real, live-reproduced shape: veridian-task.py's cmd_create() print()s
# its ERROR line to stdout, never stderr, before sys.exit(1).
REAL_CMD_CREATE_STDOUT = "ERROR: repo not found at /opt/veridian/repos/veridian-scripts\n"


# ---------------------------------------------------------------------------
# 1. _perform_spawn() unit-level: the real subprocess boundary, mocked.
# ---------------------------------------------------------------------------

def test_perform_spawn_captures_stdout_when_stderr_is_empty():
    """Reproduces the exact real shape (returncode=1, stdout carries the
    real error, stderr='') and asserts the captured outputs are never
    self-describing-but-empty, and a real reason string is returned
    alongside the failed status."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        rg = _load_rg("rg_taskcreate_unit", {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})

        row = {
            "task_kind": "veridian_task_create",
            "unit_name": None,
            "inputs_json": json.dumps({
                "title": "test title", "prompt": "test prompt", "repo": "veridian-scripts",
            }),
        }

        with mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(
                returncode=1, stdout=REAL_CMD_CREATE_STDOUT, stderr="")):
            result = rg._perform_spawn(row)

        assert result["status"] == "failed", result
        outputs = result["outputs"]

        # Real fix 1: at least one of the two real captured streams must be
        # non-empty -- the pre-fix code only ever persisted r.stderr[:500],
        # which is '' in this real reproduction, leaving the row with
        # new_task_id=None/returncode=1/stderr='' and zero real evidence.
        stdout_captured = outputs.get("stdout") or ""
        stderr_captured = outputs.get("stderr") or ""
        assert stdout_captured or stderr_captured, (
            f"outputs carries no evidence on either stream: {outputs!r}")
        assert "repo not found" in stdout_captured, (
            f"the real error text must survive into outputs.stdout, got: {outputs!r}")

        # Real fix 2: _perform_spawn() must hand back a real, non-'queued'
        # reason string for the caller to persist.
        assert result.get("reason"), f"no reason returned for a failed spawn: {result!r}"
        assert result["reason"] != "queued"
        assert "repo not found" in result["reason"]


def test_perform_spawn_bounds_captured_tails():
    """The real streams must be persisted BOUNDED (last N chars), never
    unbounded -- outputs_json is read back whole on every umr_tasks query."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        rg = _load_rg("rg_taskcreate_bounds", {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})

        row = {
            "task_kind": "veridian_task_create",
            "unit_name": None,
            "inputs_json": json.dumps({"title": "t", "prompt": "p", "repo": "veridian-scripts"}),
        }
        huge_stderr = "E" * 50_000

        with mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(
                returncode=1, stdout="", stderr=huge_stderr)):
            result = rg._perform_spawn(row)

        assert len(result["outputs"]["stderr"]) <= rg._SPAWN_OUTPUT_TAIL_CHARS


# ---------------------------------------------------------------------------
# 2. dispatch_one() end-to-end: the real terminal umr_tasks row must carry
#    real evidence and a real reason, never 'queued'.
# ---------------------------------------------------------------------------

def test_dispatch_one_end_to_end_failed_task_create_gets_real_reason_and_stderr():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_taskcreate_e2e", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-taskcreate-empty-stderr", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "veridian-scripts", "title": "does not name a PR number",
                       "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        dc_mod = rg._dispatch_core()

        def fake_run(cmd, **kwargs):
            # gh calls (Stage 4/5/6 duplicate-PR guard) -- fail open, no match.
            if cmd and cmd[0] == "gh":
                return _FakeCompletedProcess(0, json.dumps([]))
            # The real spawn: veridian-task.py create, real failure shape.
            if "veridian-task.py" in " ".join(cmd):
                return _FakeCompletedProcess(returncode=1, stdout=REAL_CMD_CREATE_STDOUT, stderr="")
            return _FakeCompletedProcess(0, "")

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(rg, "_run", side_effect=fake_run):
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "dispatched", result
        assert result["result"]["status"] == "failed", result

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT status, reason, outputs_json FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()

        assert row["status"] == "failed", row["status"]

        # This is the real regression: pre-fix, `reason` stayed 'queued'
        # forever on a terminal failed row.
        assert row["reason"] != "queued", (
            f"reason was never overwritten on a terminal failed row: {row['reason']!r}")
        assert row["reason"], "reason must not be empty/None on a terminal failed row"

        outputs = json.loads(row["outputs_json"])
        stdout_evidence = outputs.get("stdout") or ""
        stderr_evidence = outputs.get("stderr") or ""
        assert stdout_evidence or stderr_evidence, (
            f"row is self-describing as evidence-bearing but carries none: {outputs!r}")
        assert outputs.get("new_task_id") is None
        assert outputs.get("returncode") == 1


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    if failed:
        print(f"{failed}/{len(tests)} FAILED")
        sys.exit(1)
    print(f"ALL {len(tests)} PASSED")
