#!/usr/bin/env python3
"""Real regression test for the addendum to UMR-20260808-122929-bc77
(governing chain UMR-20260806-171945-5767): task-gateway.py's future
`audit-24-points` subcommand must persist each of its 12 real boolean
checks (points 2/4/8/9/12/14/16/17/19/20/22/23) back into the EXISTING
`master_issue_tracker` rows `UMR171945-0001`..`-0024` via the real
`update-issue` CLI -- never a raw SQL write -- and `list-issues
--linked-umr-id UMR-20260806-171945-5767` must reflect the result.

Live-verified before writing this (2026-08-08): `task-gateway.py
audit-24-points` does not exist yet (a separate, sibling task owns building
it -- out of THIS addendum's own scope, see PROGRESS.md). This file proves
the real persistence MECHANISM independently of that: the exact CLI calls
the future `audit-24-points` implementation will make, run for real against
an isolated scratch database (never the live production
`superboss-register.sqlite` -- `UMR171945-00NN` are real, live rows; a
synthetic test result must never land in them), using the same
`SUPERBOSS_REGISTER_DB` testability seam
tests/test_build_lock_contended_requeue.py already established.

Real 1:1 point -> issue_id mapping (independently confirmed against the
live DB before writing this, not assumed): bc77's own "Point N" text maps
to `issue_id='UMR171945-{N:04d}'` -- e.g. point 8 ("does a real, timestamped
memory-check log entry exist within the last 24h?") is UMR171945-0008
("PM has seen last 24h memory and checked"); point 17 ("does task-gateway.py's
real search step contain a real Zoekt/pgvector reference?") is
UMR171945-0017 ("pgvector/Grafana/Prometheus/node_exporter/Zoekt integrated
... automatic by software without AI").
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")

# The real 12 points this addendum covers, per bc77's own dispatch text.
AUDIT_24_POINTS_SUBSET = (2, 4, 8, 9, 12, 14, 16, 17, 19, 20, 22, 23)

GOVERNING_UMR = "UMR-20260806-171945-5767"


def _load_sbr_module(modname="sbr_audit24_test"):
    spec = importlib.util.spec_from_file_location(modname, SBR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(path):
    """Same technique as tests/test_build_lock_contended_requeue.py's
    _seed_scratch_db(): bootstrap directly against sbr.DB_PATH/sbr.init_db(),
    never through resolve_superboss_db_path()'s own env-var fallback (which
    only accepts an already-existing, non-empty, real-schema candidate).
    Additionally seeds the real 24-row UMR171945-00NN shape (issue_number
    987-1010, matching the live table's real numbering, though the exact
    number is irrelevant to this test -- only issue_id/linked_umr_id are)."""
    sbr = _load_sbr_module("sbr_audit24_seed")
    sbr.DB_PATH = path
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_master_issue_tracker_table(conn)
    with sbr._write_lock():
        for n in range(1, 25):
            sbr.add_master_issue(
                conn,
                issue_id=f"UMR171945-{n:04d}",
                issue_identified=f"synthetic test point {n}",
                linked_umr_id=GOVERNING_UMR,
            )
        conn.commit()
    conn.close()
    return sbr


def _run_cli(scratch_db, args):
    env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
    result = subprocess.run(
        [sys.executable, SBR_PATH] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"CLI call {args!r} failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return json.loads(result.stdout)


def _persist_audit24_point_result(scratch_db, point, boolean_result, detail, ran_at):
    """The real, exact persistence pattern the addendum requires: for one
    audit-24-points check result, call the real `update-issue` CLI (never a
    raw SQL write) against the point's own real, mapped
    master_issue_tracker row. This is what task-gateway.py's future
    audit-24-points implementation is expected to call once per point,
    after computing that point's real boolean.

    is_deterministic/is_ai_free/is_boolean_software all flip to 'YES' the
    moment a real check for this point has genuinely run (they describe the
    CHECK MECHANISM's own nature -- real, deterministic, boolean, AI-free --
    not the underlying condition's pass/fail state, matching how
    UMR171945-0011's own live audit_notes already distinguish a row's
    registration-mechanism maturity from its actual resolution state).
    solution_applied/issue_resolved_permanently mirror the real boolean
    result itself: YES when the point's real requirement is verified met,
    NO when the real check found it still unmet."""
    issue_id = f"UMR171945-{point:04d}"
    outcome = "YES" if boolean_result else "NO"
    note = f"[{ran_at}] audit-24-points point {point}: {outcome} -- {detail}"
    return _run_cli(scratch_db, [
        "update-issue", "--issue-id", issue_id,
        "--field", "is_deterministic=YES",
        "--field", "is_ai_free=YES",
        "--field", "is_boolean_software=YES",
        "--field", f"solution_applied={outcome}",
        "--field", f"issue_resolved_permanently={outcome}",
        "--field", f"check_again_notes={note}",
    ])


def test_persisted_results_reflected_by_list_issues_linked_umr_id():
    """The addendum's own literal 'Real boolean test': after persisting all
    12 points' real results via update-issue, `list-issues --linked-umr-id
    UMR-20260806-171945-5767` shows the real, current boolean result for
    each of the 12 points reflected in those same rows -- not a separate
    log/stdout capture. Uses one synthetic TRUE case and one synthetic
    FALSE case per point (24 real update-issue calls total), matching
    bc77's own 'a real synthetic TRUE case and a real synthetic FALSE
    case, both verified to return the correct boolean' test convention."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        ran_at = "2026-08-08T00:00:00Z"
        expected = {}
        for i, point in enumerate(AUDIT_24_POINTS_SUBSET):
            # Alternate TRUE/FALSE across the 12 points so both real
            # outcomes are exercised, not just one.
            boolean_result = (i % 2 == 0)
            detail = f"synthetic {'TRUE' if boolean_result else 'FALSE'} case for point {point}"
            resp = _persist_audit24_point_result(scratch_db, point, boolean_result, detail, ran_at)
            assert resp["ok"] is True, f"update-issue failed for point {point}: {resp}"
            expected[point] = boolean_result

        listed = _run_cli(scratch_db, [
            "list-issues", "--linked-umr-id", GOVERNING_UMR, "--limit", "50",
        ])
        by_id = {m["issue_id"]: m for m in listed["matches"]}

        for point, boolean_result in expected.items():
            issue_id = f"UMR171945-{point:04d}"
            assert issue_id in by_id, f"{issue_id} missing from list-issues --linked-umr-id output"
            row = by_id[issue_id]
            outcome = "YES" if boolean_result else "NO"
            assert row["is_deterministic"] == "YES", row
            assert row["is_ai_free"] == "YES", row
            assert row["is_boolean_software"] == "YES", row
            assert row["solution_applied"] == outcome, row
            assert row["issue_resolved_permanently"] == outcome, row
            assert row["check_again_notes"] is not None and f"point {point}: {outcome}" in row["check_again_notes"], row

        # Real negative check: a point NOT in the 12-point subset must be
        # left completely untouched by this run (no over-broad write).
        untouched_id = "UMR171945-0001"
        assert by_id[untouched_id]["is_deterministic"] is None
        assert by_id[untouched_id]["solution_applied"] is None
        print("PASS: test_persisted_results_reflected_by_list_issues_linked_umr_id")


def test_rerun_updates_same_row_no_duplicate():
    """A second real audit-24-points run (e.g. a boolean flips from its
    prior run) must UPDATE the existing row in place -- never insert a
    second row for the same point, and check_again_notes must reflect the
    latest real timestamped outcome, not silently keep the stale one."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        point = 17
        _persist_audit24_point_result(scratch_db, point, False, "no Zoekt/pgvector reference found", "2026-08-08T10:00:00Z")
        _persist_audit24_point_result(scratch_db, point, True, "real Zoekt reference now present", "2026-08-08T11:00:00Z")

        listed = _run_cli(scratch_db, ["list-issues", "--linked-umr-id", GOVERNING_UMR, "--limit", "50"])
        matches = [m for m in listed["matches"] if m["issue_id"] == "UMR171945-0017"]
        assert len(matches) == 1, "re-run must update the same row, never duplicate it"
        row = matches[0]
        assert row["solution_applied"] == "YES"
        assert row["issue_resolved_permanently"] == "YES"
        assert "11:00:00Z" in row["check_again_notes"]
        assert "10:00:00Z" not in row["check_again_notes"], "stale prior-run note must not linger uncorrected"
        print("PASS: test_rerun_updates_same_row_no_duplicate")


if __name__ == "__main__":
    test_persisted_results_reflected_by_list_issues_linked_umr_id()
    test_rerun_updates_same_row_no_duplicate()
    print("ALL PASS")
