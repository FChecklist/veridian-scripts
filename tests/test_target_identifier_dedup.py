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

UMR-20260813-220216-2e2b real addendum: extract_target_identifiers() had no
UMR-id pattern at all, which is exactly what let two more real
duplicate-spend incidents slip past this same dedup layer on 2026-08-13 --
"RCA: UMR-20260807-151622-15cd killed" dispatched twice an hour apart
(UMR-...-4bcc, UMR-...-7615), and an RCA dispatched for
UMR-20260813-195852-aa85 (UMR-...-b0cc) after its real fix had already
merged as veridian-scripts#323. Both name their target purely by UMR id, no
PR/file/script involved. See the real-incident fixtures below (verbatim
title/prompt text from superboss-register.sqlite's umr_tasks rows).

Covers:
  1. extract_target_identifiers()/find_target_identifier_duplicate() as pure
     functions -- UMR id, PR number+repo, exact file path, exact script name.
  2. superboss-register.py's check-target-identifier-duplicate CLI, against a
     real, isolated, temp-file SQLite DB seeded with the real schema.
  3. dispatch-owner-task.sh end-to-end, real code path, real subprocess: the
     exact incident pattern (two same-PR dispatches, different wording, same
     repo, within a 4h window) is refused, AND proven NOT catchable by the
     existing check-content-duplicate (byte-identical hash) or --search
     (fuzzy FTS5) mechanisms alone -- this is the real proof the new,
     deterministic layer is what closes the gap.
  4. Real-incident regression fixtures for the two live UMR-id-only
     duplicates above (the 15cd duplicate-RCA pair, and the aa85
     RCA-against-an-already-merged-fix), plus a genuinely-new-target
     negative control.
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


def test_extract_target_identifiers_umr_id():
    """UMR-20260813-220216-2e2b real fix: a dispatch that names its target
    purely by UMR id (e.g. "RCA: UMR-X killed") must yield a target
    identifier -- the original version of this function had no UMR-id
    pattern at all, which is exactly what let the two real duplicate-spend
    incidents below (15cd, aa85) slip past this dedup layer."""
    sbr = _load_sbr()
    ids = sbr.extract_target_identifiers(
        "RCA: UMR-20260807-151622-15cd killed -- read the real reason first")
    assert ids == ["umr:UMR-20260807-151622-15cd"]
    # No default_repo needed -- unlike a bare PR number, a UMR id is globally
    # unambiguous on its own.
    assert sbr.extract_target_identifiers("no umr id in this text") == []


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


# --- real-incident fixtures (UMR-20260813-220216-2e2b) --------------------
#
# Real title/prompt text pulled verbatim from superboss-register.sqlite's
# umr_tasks rows for the two live duplicate-spend incidents that motivated
# adding UMR-id extraction (see extract_target_identifiers()'s own
# docstring). Both real duplicate dispatches actually happened -- these
# fixtures reproduce the exact pre-dispatch decision point (the earlier row
# still non-terminal) so the check can be proven to now refuse the second
# one.

REAL_15CD_FIRST_TITLE = "RCA: UMR-20260807-151622-15cd killed"
REAL_15CD_FIRST_PROMPT = (
    "GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). "
    "REAL GAP FOUND: resource_governor.py --query-umr --umr-id "
    "UMR-20260807-151622-15cd shows status=killed, real recorded reason: "
    "\"queued\" (unit_name=veridian-worker@task-20260807-152601-stop-work-"
    "order--batch-3--land-batch-2-s.service). This needs a real RCA: read "
    "the row's full real outputs_json/reason (query resource_governor.py "
    "--query-umr --umr-id UMR-20260807-151622-15cd yourself first, do not "
    "trust this summary alone), determine the real root cause, and either "
    "fix + redispatch the real remaining scope, or record a real, honest "
    "terminal outcome via superboss-register.py mark-umr-terminal citing "
    "real evidence. Do not fabricate completion."
)
# Real second dispatch, UMR-20260813-211758-7615, ~59 minutes later -- same
# target, deliberately different wording (a different "real recorded
# reason" quoted) so a byte-identical content hash can never catch it.
REAL_15CD_SECOND_TITLE = "RCA: UMR-20260807-151622-15cd killed"
REAL_15CD_SECOND_PROMPT = (
    "GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). "
    "REAL GAP FOUND: resource_governor.py --query-umr --umr-id "
    "UMR-20260807-151622-15cd shows status=killed, real recorded reason: "
    "\"real systemd state 'inactive', no PR was ever opened, real task.yaml "
    "status='blocked' -- no live process and no real deliverable; "
    "mechanically correctable to killed (orphaned dispatch, never produced "
    "a real artifact).\" (unit_name=veridian-worker@task-20260807-152601-"
    "stop-work-order--batch-3--land-batch-2-s.service). This needs a real "
    "RCA: read the row's full real outputs_json/reason (query "
    "resource_governor.py --query-umr --umr-id UMR-20260807-151622-15cd "
    "yourself first, do not trust this summary alone), determine the real "
    "root cause, and either fix + redispatch the real remaining scope, or "
    "record a real, honest terminal outcome via superboss-register.py "
    "mark-umr-terminal citing real evidence. Do not fabricate completion."
)

# Real UMR-20260813-205141-b6fa: dispatched to Tier-1-audit-then-merge the
# real fix for aa85 (PR #323). Still non-terminal is exactly the state it
# was actually in when the real duplicate below fired.
REAL_AA85_MERGE_TASK_TITLE = (
    "Tier-1 independent audit then merge veridian-scripts PR 323 at head "
    "84ca4b43 - real code, MERGEABLE CLEAN, zero audit comments"
)
REAL_AA85_MERGE_TASK_PROMPT = (
    "GOVERNING CHAIN: PM-desktop-sentinel tick 2026-08-13T20:44Z. Source "
    "UMR: UMR-20260813-195852-aa85 (status killed, but returncode=0 and it "
    "DID produce real work - PR 323 exists with real code, a false-kill "
    "matching the known supervisor self-block pattern). PR 323 in "
    "FChecklist/veridian-scripts, title fix(pm-sentinel-tick): Check 0, "
    "live deploy drift self-check."
)
# Real UMR-20260813-211747-b0cc: an RCA dispatched for aa85 even though
# aa85's real fix had already merged as PR #323 (i.e. the row above already
# covers this exact target).
REAL_AA85_RCA_TITLE = "RCA: UMR-20260813-195852-aa85 killed"
REAL_AA85_RCA_PROMPT = (
    "GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel tick). "
    "REAL GAP FOUND: resource_governor.py --query-umr --umr-id "
    "UMR-20260813-195852-aa85 shows status=killed, real recorded reason: "
    "\"real systemd state 'inactive', no PR was ever opened, real "
    "task.yaml status='pending_review' -- no live process and no real "
    "deliverable; mechanically correctable to killed (orphaned dispatch, "
    "never produced a real artifact).\" (unit_name=veridian-worker@task-"
    "20260813-195921-real-pm-sentinel-tick-sh-key-based-syste.service). "
    "This needs a real RCA: read the row's full real outputs_json/reason "
    "first, determine the real root cause, and either fix + redispatch the "
    "real remaining scope, or record a real, honest terminal outcome via "
    "superboss-register.py mark-umr-terminal citing real evidence."
)


def test_real_incident_duplicate_15cd_rca_blocked(scratch_db):
    """Real incident: "RCA: UMR-20260807-151622-15cd killed" was dispatched
    TWICE, an hour apart (UMR-20260813-201823-4bcc, then
    UMR-20260813-211758-7615), each spawning a full paid AI session and a
    no-op branch. First, prove the existing exact-hash content-duplicate
    check would NOT have caught it (the "real recorded reason" quoted
    differs between the two, so the normalized text hashes differ) --
    then prove the new UMR-id-aware target-identifier check does."""
    sbr = _seed_full_schema(scratch_db)
    assert (sbr._content_hash_for_text(REAL_15CD_FIRST_PROMPT)
            != sbr._content_hash_for_text(REAL_15CD_SECOND_PROMPT))

    _insert_row(scratch_db, "UMR-20260813-201823-4bcc", status="running",
                title=REAL_15CD_FIRST_TITLE, prompt=REAL_15CD_FIRST_PROMPT,
                repo="veridian-scripts", hours_ago=59 / 60)

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, REAL_15CD_SECOND_TITLE, REAL_15CD_SECOND_PROMPT,
        repo="veridian-scripts", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-20260813-201823-4bcc"


def test_real_incident_aa85_rca_against_already_merged_fix_blocked(scratch_db):
    """Real incident: an RCA was dispatched for UMR-20260813-195852-aa85
    (UMR-20260813-211747-b0cc) even though aa85's real fix had already
    merged as veridian-scripts#323 -- a still-non-terminal Tier-1-audit-and-
    merge row for that exact same UMR id (UMR-20260813-205141-b6fa) already
    covered it. The RCA never needed to happen."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(scratch_db, "UMR-20260813-205141-b6fa", status="running",
                title=REAL_AA85_MERGE_TASK_TITLE, prompt=REAL_AA85_MERGE_TASK_PROMPT,
                repo="claude-control", hours_ago=26 / 60)

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, REAL_AA85_RCA_TITLE, REAL_AA85_RCA_PROMPT,
        repo="claude-control", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-20260813-205141-b6fa"


def test_real_incident_genuinely_new_target_not_blocked(scratch_db):
    """Negative control: neither real fixture row above shares a target
    identifier with a genuinely new, unrelated UMR -- a real new target must
    still pass."""
    sbr = _seed_full_schema(scratch_db)
    _insert_row(scratch_db, "UMR-20260813-201823-4bcc", status="running",
                title=REAL_15CD_FIRST_TITLE, prompt=REAL_15CD_FIRST_PROMPT,
                repo="veridian-scripts", hours_ago=0.5)
    _insert_row(scratch_db, "UMR-20260813-205141-b6fa", status="running",
                title=REAL_AA85_MERGE_TASK_TITLE, prompt=REAL_AA85_MERGE_TASK_PROMPT,
                repo="claude-control", hours_ago=0.5)

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, "RCA: UMR-20260813-220512-9f01 killed",
        "GOVERNING CHAIN: unrelated real gap, a genuinely new target that "
        "has never been dispatched before.",
        repo="claude-control", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is None


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
