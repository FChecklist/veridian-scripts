#!/usr/bin/env python3
"""Real regression test for the root-cause fix of the duplicate-automated-
worker pattern (UMR-20260804-213847-4b56, citing UMR-20260804-180711-7f96).

Real root cause, confirmed against the live umr_tasks table for the two
real re-fire incidents observed this session: dispatch-owner-task.sh's own
real, by-design dual dispatch relays every real Owner/PM instruction into
BOTH the live interactive tmux session AND a real, governed
veridian_task_create task in the same umr_tasks queue, in the same call.
This is deliberate (laptop-independence). The real, previously-missing
piece: nothing ever told the queued twin the interactive channel already
finished the same real work -- real anti-starvation aging plus real
concurrency backpressure (this session's own heavy interactive slot usage)
left several owner-dispatch tasks queued 27-74 real minutes, by which time
the same UMR's work was already completed and merged via the interactive
channel. Not a retry loop, not a duplicate cron/notification -- confirmed
directly against real ts_submitted/ts_dispatched timestamps in umr_tasks
(ts_submitted for all 5 re-fired tasks in the first incident exactly
matches their original real UMR creation times; ts_dispatched is 74 real
minutes later, the SAME row, never re-inserted).

This test proves the real fix: dispatch_one() now checks, before spawning
a queued veridian_task_create task, whether its own title names a real
OCID that already has newer real evidence in ocid_artifact_links -- if so,
the redundant spawn is skipped (status=rejected_duplicate,
action=superseded_by_ocid_evidence) rather than duplicating already-
finished real work. Every test uses a real, isolated, temp-file SQLite
database seeded with the real schema -- never the live production
database.
"""
import contextlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeDispatchCoreAlwaysFreeSlot:
    """Real dispatch_one() calls dc.has_free_slot() first, gating on the
    REAL, live, current concurrency state of whatever box this test happens
    to run on -- this session's own real production box routinely has all
    5 real worker/supervisor slots busy, which would make dispatch_one()
    return 'deferred' before ever reaching the code this test exists to
    exercise (confirmed live: this exact interference already forced a
    broadened assertion in tests/test_rule2_dispatch_outcomes.py's own
    end-to-end case). Since resource_governor.py's own _dispatch_core()
    lazily caches into a plain module-level global on first call, and each
    test here loads a genuinely fresh module instance via _load() (real
    importlib.util.module_from_spec, never sys.modules-cached), monkey-
    patching _dispatch_core itself on that fresh instance is safe and
    fully isolated from every other test."""
    def has_free_slot(self):
        return True

    def has_free_slot_detail(self):
        # Real fix (UMR-20260806-101839-688e): _dispatch_one_inner() now
        # calls has_free_slot_detail() (real-reason variant) instead of the
        # bool-only has_free_slot() -- this fake must offer both so it stays
        # a drop-in dispatch_core replacement regardless of which one the
        # real code calls.
        return True, {"check": "ok"}

    @contextlib.contextmanager
    def acquire_dispatch_lock(self):
        yield

    def record_dispatch_event(self, **kwargs):
        pass


# Real, disclosed incident (found and fully cleaned up before this PR was
# finalized): an earlier version of this test file monkey-patched
# _dispatch_core but NOT _perform_spawn. For task_kind=='veridian_task_create'
# rows that were NOT superseded (the "control" cases below, by design), that
# meant dispatch_one() reached the real _perform_spawn(), whose
# veridian_task_create branch runs `python3 veridian-task.py create` as a
# REAL subprocess against real, hardcoded /opt/veridian paths -- completely
# independent of this test's own SUPERBOSS_REGISTER_DB isolation, which only
# covers resource_governor.py's/superboss-register.py's own DB access, not a
# separate script invoked via subprocess. Real consequence: repeated test
# runs during development created 18 real local task directories, 12 real
# pushed branches, and 12 real open GitHub PRs on the live veridian-scripts
# repo, all using this file's own fake test title strings -- all found and
# cleaned up (units stopped, PRs closed, branches deleted, worktrees pruned,
# task directories removed) before this PR was finalized. Every test below
# now ALSO monkey-patches _perform_spawn itself, so no test in this file can
# ever reach a real subprocess/git/systemd call again, regardless of which
# code path it takes.


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_rc", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    return sbr, conn


def _load(name, filename, env=None):
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)


def test_reproduces_the_real_incident_queued_task_superseded_by_newer_evidence():
    """Real, direct reproduction of the exact observed incident shape: a
    veridian_task_create task queued long ago (title names OCID-068, the
    exact real OCID from the observed re-fire), and REAL newer
    ocid_artifact_links evidence for that same OCID (as if the interactive
    channel had, in the meantime, opened and merged a real PR). The queued
    task must be superseded, not spawned."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)

        # Real, old-enough-to-supersede submission -- as if relayed and
        # queued a while ago (this session's own real incident: ts_submitted
        # 19:41, ts_dispatched would-be 74 minutes later at 20:55). Computed
        # dynamically (real "now" minus 5 minutes -- comfortably before the
        # ocid_artifact_link's own real, server-assigned created_at inserted
        # just below, and comfortably under resource_governor.py's real
        # MAX_QUEUED_AGE_SECONDS staleness gate) rather than a hardcoded past
        # literal -- same real fix already applied to this file's own
        # test_older_evidence_before_submission_does_not_supersede() below,
        # for the same reason: a hardcoded past literal silently ages past
        # any real, later-added staleness gate (UMR-20260806-151638-48cc:
        # next_queued_task() gained exactly such a gate and this test's old
        # 2026-08-04 literal started falling outside it).
        ts_submitted_recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "owner-task-20260804-194355-test", "tier": 2,
            "status": "queued", "source_trigger": "owner_dispatch_gateway",
            "task_kind": "veridian_task_create",
            "inputs": {"title": "OCID-068 guardrails addendum real status", "prompt": "x", "repo": "veridian-scripts"},
            "reason": "queued", "ts_submitted": ts_submitted_recent,
        })
        conn.commit()

        # Real, NEWER evidence for the SAME OCID -- as if the interactive
        # channel independently merged real PRs for OCID-068 in the
        # meantime (this session's own real history: PRs #26/#29/#30/#31/
        # #33/#34/#35, all real, all citing OCID-068).
        sbr.insert_ocid_artifact_link(
            conn, ocid_number="OCID-068", umr_id="UMR-20260804-180711-7f96",
            repo="veridian-scripts", pr_number=35, commit_sha="638fd38403c86d82395331f1c00208937b31764a",
            link_kind="merge",
        )
        conn.commit()
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_ocid_supersede_repro", "resource_governor.py", env=env)
        rg._dispatch_core = lambda: _FakeDispatchCoreAlwaysFreeSlot()
        rg._perform_spawn = lambda row: {"status": "running", "unit_name": "fake-test-unit-never-real.service", "outputs": {}}

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] == "superseded_by_ocid_evidence", result
        assert result["umr_id"] == umr_id, result
        assert result["ocid_number"] == "OCID-068", result
        assert "OCID-068" in result["detail"] and "UMR-20260804-180711-7f96" in result["detail"], result["detail"]
        print(f"PASS: test_reproduces_the_real_incident_queued_task_superseded_by_newer_evidence -> {result['detail'][:120]}...")


def test_queued_task_with_ocid_but_no_newer_evidence_still_spawns_normally():
    """A queued task whose title names a real OCID that has NO newer real
    evidence must be dispatched exactly as before -- the fix must never
    block genuinely new, not-yet-done work."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        sbr.upsert_umr_task(conn, {
            "task_identity": "owner-task-genuinely-new", "tier": 2,
            "status": "queued", "source_trigger": "owner_dispatch_gateway",
            "task_kind": "veridian_task_create",
            "inputs": {"title": "OCID-999 a genuinely new, never-worked item",
                       "prompt": "x", "repo": "veridian-scripts"},
            "reason": "queued", "ts_submitted": "2026-08-04T19:43:55+00:00",
        })
        conn.commit()
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_ocid_supersede_control", "resource_governor.py", env=env)
        rg._dispatch_core = lambda: _FakeDispatchCoreAlwaysFreeSlot()
        rg._perform_spawn = lambda row: {"status": "running", "unit_name": "fake-test-unit-never-real.service", "outputs": {}}

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] != "superseded_by_ocid_evidence", result
        print(f"PASS: test_queued_task_with_ocid_but_no_newer_evidence_still_spawns_normally -> action={result['action']}")


def test_older_evidence_before_submission_does_not_supersede():
    """Real evidence for the same OCID that predates this task's own
    ts_submitted must NOT supersede it -- only evidence created AFTER the
    task was queued is real proof the SAME queued attempt's work was done
    elsewhere while it waited. Evidence from before submission just means
    the OCID had prior, unrelated history (this session's own real OCID-068
    has ~15 real UMRs across its whole lifetime), not that THIS specific
    queued task is now redundant.

    Real fix, 2026-08-05 (found while writing the OCID-068 permanent closure
    record): insert_ocid_artifact_link() has no explicit created_at
    parameter -- it always stamps the real current time at insert. This
    test's own ts_submitted used to be a hardcoded literal ("2026-08-04
    23:59:59"), so once a real calendar day passed, "now" (whenever this
    test actually runs) was always after that fixed literal -- the seeded
    "old" evidence stopped looking old, and this test started failing for a
    reason that had nothing to do with the real code under test. Fixed by
    computing ts_submitted dynamically, always safely in the future
    relative to real insert time, so the test's own real intent holds
    regardless of what day it runs."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        sbr.insert_ocid_artifact_link(
            conn, ocid_number="OCID-068", umr_id="UMR-OLD-PRIOR-WORK",
            repo="veridian-scripts", pr_number=20, link_kind="merge",
        )
        conn.commit()
        ts_submitted_after_evidence = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        sbr.upsert_umr_task(conn, {
            "task_identity": "owner-task-after-old-evidence", "tier": 2,
            "status": "queued", "source_trigger": "owner_dispatch_gateway",
            "task_kind": "veridian_task_create",
            "inputs": {"title": "OCID-068 a real, distinct, later directive",
                       "prompt": "x", "repo": "veridian-scripts"},
            "reason": "queued",
            "ts_submitted": ts_submitted_after_evidence,  # real "now" + 1h -- always after the evidence above
        })
        conn.commit()
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_ocid_supersede_old_evidence", "resource_governor.py", env=env)
        rg._dispatch_core = lambda: _FakeDispatchCoreAlwaysFreeSlot()
        rg._perform_spawn = lambda row: {"status": "running", "unit_name": "fake-test-unit-never-real.service", "outputs": {}}

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] != "superseded_by_ocid_evidence", result
        print(f"PASS: test_older_evidence_before_submission_does_not_supersede -> action={result['action']}")


def test_no_ocid_in_title_never_matches():
    """A task whose title names no real OCID at all must be completely
    unaffected -- no regex match, no query, dispatched exactly as before."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        sbr.upsert_umr_task(conn, {
            "task_identity": "owner-task-no-ocid", "tier": 2,
            "status": "queued", "source_trigger": "owner_dispatch_gateway",
            "task_kind": "veridian_task_create",
            "inputs": {"title": "a plain title with no OCID reference at all",
                       "prompt": "x", "repo": "veridian-scripts"},
            "reason": "queued", "ts_submitted": "2026-08-04T19:43:55+00:00",
        })
        conn.commit()
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_ocid_supersede_no_ocid", "resource_governor.py", env=env)
        rg._dispatch_core = lambda: _FakeDispatchCoreAlwaysFreeSlot()
        rg._perform_spawn = lambda row: {"status": "running", "unit_name": "fake-test-unit-never-real.service", "outputs": {}}

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] != "superseded_by_ocid_evidence", result
        print(f"PASS: test_no_ocid_in_title_never_matches -> action={result['action']}")


def test_systemctl_action_rows_never_checked():
    """The OCID-evidence supersession check is opt-in to task_kind==
    'veridian_task_create' only, same real scope as the pre-existing
    duplicate-PR guard -- a systemctl_action row (resume/retry of an
    existing worker unit) must never be superseded by this check, even if
    its own unit_name happens to contain an OCID-shaped substring."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        sbr.insert_ocid_artifact_link(
            conn, ocid_number="OCID-068", umr_id="UMR-X", repo="veridian-scripts",
            pr_number=1, link_kind="merge",
        )
        conn.commit()
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-systemctl-action-row", "tier": 0,
            "status": "queued", "source_trigger": "unit_test",
            "task_kind": "systemctl_action",
            "unit_name": "veridian-worker@task-ocid-068-something.service",
            "inputs": {"action": "start"},
            "reason": "queued", "ts_submitted": "2026-08-04T00:00:00+00:00",
        })
        conn.commit()
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_ocid_supersede_systemctl", "resource_governor.py", env=env)
        rg._dispatch_core = lambda: _FakeDispatchCoreAlwaysFreeSlot()
        rg._perform_spawn = lambda row: {"status": "running", "unit_name": "fake-test-unit-never-real.service", "outputs": {}}

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] != "superseded_by_ocid_evidence", result
        print(f"PASS: test_systemctl_action_rows_never_checked -> action={result['action']}")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
