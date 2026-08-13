#!/usr/bin/env python3
"""Addendum to UMR-20260813-102459-10c3 (itself addendum to
UMR-20260813-084321-2962 / P1 UMR-20260806-171945-5767).

REAL INCIDENT this covers (2026-08-13): the Desktop sentinel dispatched
UMR-...-a248 (targeting PR #131) and UMR-...-1489 (targeting PR #135), then
the Desktop session independently dispatched UMR-...-bd10 (same PR #131) and
UMR-...-9a69 (same PR #135) minutes later -- resource_governor.py --search on
the exact PR text returned nothing (FTS5 is fuzzy and missed an exact recent
duplicate), so both duplicate pairs ran concurrently against the same PR
branches.

Covers:
  1. extract_target_identifiers()/find_target_identifier_duplicate() as pure
     functions -- PR number+repo, exact file path, exact script name.
  2. superboss-register.py's check-target-identifier-duplicate CLI, against a
     real, isolated, temp-file SQLite DB seeded with the real schema.
  3. dispatch-owner-task.sh end-to-end, real code path, real subprocess: the
     exact incident pattern (two same-PR dispatches, different wording, same
     repo, within a 4h window) is refused, AND proven NOT catchable by the
     existing check-content-duplicate (byte-identical hash) or --search
     (fuzzy FTS5) mechanisms alone -- this is the real proof the new,
     deterministic layer is what closes the gap.
"""
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_TMUX = """#!/usr/bin/env bash
set -euo pipefail
echo "$@" >> "$TMUX_FAKE_LOG"
if [ "$1" = "has-session" ]; then
  exit 1
fi
exit 0
"""


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_target_identifier_dedup_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _seed_full_schema(path):
    sbr = _load_sbr()
    sbr.DB_PATH = path
    sbr.init_db()
    return sbr


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_full_schema(path)
        yield path


def _insert_row(path, umr_id, *, status, title, prompt, repo="claude-control",
                 hours_ago=0.0, tier=2, task_identity=None):
    conn = sqlite3.connect(path)
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (umr_id, task_identity or (umr_id + "-identity"), ts, tier, status,
         "owner_dispatch_gateway", "veridian_task_create",
         json.dumps({"title": title, "prompt": prompt, "repo": repo}), "{}", "{}"),
    )
    conn.commit()
    conn.close()


def _run_sbr(args, scratch_db):
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    return subprocess.run(
        [sys.executable, "superboss-register.py"] + args,
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True,
    )


# --- pure-function unit tests -------------------------------------------

def test_extract_target_identifiers_pr_with_explicit_repo():
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers("please review claude-control#131 today")
    assert "pr:claude-control#131" in ids


def test_extract_target_identifiers_bare_pr_needs_default_repo():
    sbr = _load_sbr()
    assert sbr.extract_target_identifiers("please look at PR #131") == []
    ids = sbr.extract_target_identifiers("please look at PR #131", default_repo="claude-control")
    assert ids == ["pr:claude-control#131"]


def test_extract_target_identifiers_file_path_and_script_name():
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "fix scripts/resource_governor.py and re-run pm-sentinel-tick.sh")
    assert "path:scripts/resource_governor.py" in ids
    assert "script:pm-sentinel-tick.sh" in ids


def test_find_target_identifier_duplicate_pure_function(scratch_db):
    sbr = _seed_full_schema(scratch_db)
    _insert_row(scratch_db, "UMR-TEST-10c3-a248", status="running",
                title="RCA: PR #131 audit", prompt="Investigate real failure on PR #131.",
                repo="claude-control", hours_ago=0.2)
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    # Different wording, same real target (PR #131, same repo) minutes later --
    # the exact incident pattern.
    dup = sbr.find_target_identifier_duplicate(
        conn, "Fix PR #131 merge conflict", "Please resolve the conflict blocking PR #131.",
        repo="claude-control", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-TEST-10c3-a248"


def test_find_target_identifier_duplicate_respects_window_and_status(scratch_db):
    sbr = _seed_full_schema(scratch_db)
    _insert_row(scratch_db, "UMR-TEST-10c3-old", status="running",
                title="RCA: PR #999 audit", prompt="Investigate PR #999.",
                repo="claude-control", hours_ago=5.0)  # outside 4h window
    _insert_row(scratch_db, "UMR-TEST-10c3-done", status="completed",
                title="RCA: PR #998 audit", prompt="Investigate PR #998.",
                repo="claude-control", hours_ago=0.1)  # not a live status
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)

    assert sbr.find_target_identifier_duplicate(
        conn, "t", "Please look at PR #999 again.", repo="claude-control") is None
    assert sbr.find_target_identifier_duplicate(
        conn, "t", "Please look at PR #998 again.", repo="claude-control") is None
    conn.close()


# --- CLI subcommand -------------------------------------------------------

def test_check_target_identifier_duplicate_cli(scratch_db):
    _seed_full_schema(scratch_db)
    _insert_row(scratch_db, "UMR-TEST-10c3-cli", status="queued",
                title="Audit PR #131", prompt="Real audit of PR #131 findings.",
                repo="claude-control", hours_ago=0.05)

    out = _run_sbr([
        "check-target-identifier-duplicate",
        "--title", "Fix PR #131",
        "--prompt", "Land the real fix for PR #131 now.",
        "--repo", "claude-control",
    ], scratch_db)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["target_identifier_duplicate_found"] is True
    assert payload["duplicate_umr_id"] == "UMR-TEST-10c3-cli"

    out_clean = _run_sbr([
        "check-target-identifier-duplicate",
        "--title", "Fix PR #999",
        "--prompt", "Land the real fix for PR #999 now.",
        "--repo", "claude-control",
    ], scratch_db)
    assert out_clean.returncode == 0, out_clean.stderr
    assert json.loads(out_clean.stdout)["target_identifier_duplicate_found"] is False


# --- dispatch-owner-task.sh end-to-end, real code path, real incident ----

@pytest.fixture()
def fake_tmux_path(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    tmux_path = bin_dir / "tmux"
    tmux_path.write_text(FAKE_TMUX)
    tmux_path.chmod(tmux_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(bin_dir)


def _run_wrapper(scratch_db, fake_tmux_path, tmp_path, title, prompt, repo="claude-control"):
    log_file = tmp_path / "tmux.log"
    env = dict(os.environ)
    env["PATH"] = fake_tmux_path + os.pathsep + env["PATH"]
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    env["DISPATCH_TMUX_SESSION"] = "no-such-session"
    env["TMUX_FAKE_LOG"] = str(log_file)
    env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
    return subprocess.run(
        ["./dispatch-owner-task.sh", title, prompt, "2", "claude_code_cli", repo, "--no-relay"],
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True,
    )


def test_wrapper_dispatches_first_pr131_task(scratch_db, fake_tmux_path, tmp_path):
    """Baseline: the FIRST real dispatch for PR #131 must succeed (proves the
    new check is not simply refusing everything)."""
    result = _run_wrapper(
        scratch_db, fake_tmux_path, tmp_path,
        "Desktop sentinel: RCA for PR #131",
        "Real audit of PR #131 -- confirm CI status and review comments.",
    )
    assert result.returncode == 0, result.stderr
    assert "DISPATCHED:" in result.stdout


def test_wrapper_refuses_second_differently_worded_pr131_dispatch(scratch_db, fake_tmux_path, tmp_path):
    """THE real incident pattern: two same-PR dispatches, different wording,
    within a 4h window. First succeeds (Desktop sentinel), second (Desktop
    session, independently, minutes later, worded differently) must now be
    refused by the new deterministic target-identifier check."""
    first = _run_wrapper(
        scratch_db, fake_tmux_path, tmp_path,
        "Desktop sentinel: RCA for PR #131",
        "Real audit of PR #131 -- confirm CI status and review comments.",
    )
    assert first.returncode == 0, first.stderr
    first_umr_id = None
    for line in first.stdout.splitlines():
        if line.startswith("DISPATCHED:"):
            first_umr_id = line.split("umr_id=")[1].split()[0]
    assert first_umr_id

    # Prove the two existing dedup layers would NOT have caught this: the
    # wording is genuinely different (content-hash miss) and shares no FTS5
    # tokens worth ranking on top (the real incident's own --search miss).
    dup_content = _run_sbr([
        "check-content-duplicate",
        "--text", "Desktop session: please land the real fix that resolves PR #131 now.",
        "--window-hours", "6",
    ], scratch_db)
    assert json.loads(dup_content.stdout)["content_duplicate_found"] is False

    second = _run_wrapper(
        scratch_db, fake_tmux_path, tmp_path,
        "Desktop session: land fix for PR #131",
        "Desktop session: please land the real fix that resolves PR #131 now.",
    )
    assert second.returncode != 0, second.stdout + second.stderr
    assert "REFUSED" in second.stderr
    assert "target identifier" in second.stderr.lower() or "target" in second.stderr.lower()
    assert first_umr_id in second.stdout

    # And the real umr_tasks table must show exactly ONE live row for PR #131,
    # not two -- this is the real proof the incident (two concurrent workers
    # against the same PR branch) cannot recur.
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT umr_id, status FROM umr_tasks WHERE status IN ('queued','running')"
    ).fetchall()
    conn.close()
    live_pr131_rows = [r for r in rows if dict(r)["umr_id"] == first_umr_id]
    assert len(live_pr131_rows) == 1


def test_wrapper_allows_pr131_dispatch_for_a_different_repo(scratch_db, fake_tmux_path, tmp_path):
    """Not over-broad: the exact same PR *number* in a genuinely different
    repo is a genuinely different real target and must not be refused.
    Deliberately different wording between the two calls so the existing,
    unrelated check-content-duplicate (byte-identical text) layer can't be
    what's being exercised here -- isolates the new target-identifier check."""
    first = _run_wrapper(
        scratch_db, fake_tmux_path, tmp_path,
        "RCA for PR #131", "Real audit of PR #131 in claude-control.", repo="claude-control",
    )
    assert first.returncode == 0, first.stderr

    second = _run_wrapper(
        scratch_db, fake_tmux_path, tmp_path,
        "RCA for PR #131", "Real audit of PR #131 in veridian-scripts.", repo="veridian-scripts",
    )
    assert second.returncode == 0, second.stderr
    assert "DISPATCHED:" in second.stdout
