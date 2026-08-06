#!/usr/bin/env python3
"""Real tests for reconcile_dispatched_dead_zone.py (UMR-20260806-115538-1e55,
the original real deterministic-check ask, implemented per UMR-20260806-115605-854d's
direct correction: auto-remediate immediately, never just report).

Every real DB write this script performs is exercised here against a real,
isolated, temp-file SQLite database seeded with the real schema via
superboss-register.py's own _ensure_umr_table()/_ensure_ocid_artifact_links_table()/
_ensure_pm_decisions_pending_table() -- never the live production database,
never a raw hand-rolled partial schema. Real task-directory checks run
against a real (temp, disposable) filesystem tree, never mocked.

Covers the three real behaviors the parent UMRs require proof of:
  (a) a genuinely dead row gets auto-reset to 'queued' AND a real,
      informational (already-resolved) audit-log pm_decisions_pending row
      is written -- test_dead_row_auto_resets_and_writes_audit_log().
  (b) a row that dead-zones a SECOND time after already being auto-reset
      once escalates to a real, blocking (status='open') pm_decisions_pending
      row instead of silently re-resetting again --
      test_second_occurrence_escalates_instead_of_re_resetting().
  (c) a row carrying a real task directory, OR a real systemd unit_name, is
      correctly NOT touched (negative control) --
      test_row_with_real_task_dir_not_touched() /
      test_row_with_real_unit_name_not_touched().

Plus this script's own added real-evidence guard (ocid_artifact_links) and
threshold-boundary/dry-run/no-duplicate-escalation coverage.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_fresh_sbr():
    """Fresh superboss-register.py module load purely for schema-seeding --
    same convention as tests/test_resume_interrupted_workers_no_duplicate_row.py's
    own _seed_scratch_db()."""
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_dead_zone", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_db(path):
    sbr = _load_fresh_sbr()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_pm_decisions_pending_table(conn)
    conn.close()


def _insert_umr_row(path, umr_id, task_identity, ts_dispatched, status="dispatched",
                     unit_name=None, source_trigger="owner_dispatch_gateway",
                     task_kind="veridian_task_create", outputs_json="{}"):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, ts_dispatched, unit_name, outputs_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (umr_id, task_identity, ts_dispatched, 2, status, source_trigger, task_kind,
         ts_dispatched, unit_name, outputs_json),
    )
    conn.commit()
    conn.close()


def _insert_prior_audit_log(path, umr_id):
    """Simulates a real prior first-occurrence auto-reset audit-log row
    already existing for this umr_id, exactly the shape
    auto_reset_to_queued() itself writes (decision_type=
    'dead_zone_auto_remediation', already resolved)."""
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO pm_decisions_pending (opened_ts, title, detail, related_umr, status, decision_type) "
        "VALUES (?,?,?,?, 'resolved', 'dead_zone_auto_remediation')",
        (datetime.now(timezone.utc).isoformat(), f"prior auto-reset of {umr_id}", "prior", umr_id),
    )
    conn.commit()
    conn.close()


def _insert_ocid_artifact_link(path, umr_id):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO ocid_artifact_links (ocid_number, umr_id, repo, link_kind, created_at) "
        "VALUES (?,?,?,?,?)",
        ("OCID-999", umr_id, "compliance-tracker", "pr", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


class _Env:
    """Real env-var seam: SUPERBOSS_REGISTER_DB (read at superboss-register.py
    import time by resolve_superboss_db_path()) + VERIDIAN_TASKS_DIR
    (dispatch_core.py's own TASKS_DIR) + VERIDIAN_DEAD_ZONE_THRESHOLD_MINUTES
    (this script's own threshold override) all point at isolated, disposable
    temp paths -- the real, live production database and real live tasks
    directory are never touched by any test in this file."""

    def __init__(self, db_path, tasks_dir, threshold_minutes="15"):
        self.overrides = {
            "SUPERBOSS_REGISTER_DB": db_path,
            "VERIDIAN_TASKS_DIR": tasks_dir,
            "VERIDIAN_DEAD_ZONE_THRESHOLD_MINUTES": threshold_minutes,
        }
        self.old = {}

    def __enter__(self):
        for k, v in self.overrides.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_rdz():
    """Fresh reconcile_dispatched_dead_zone.py module load -- must happen
    AFTER the real env vars above are set, since run_sweep() itself loads
    superboss-register.py/dispatch_core.py fresh on every call (see that
    module's own run_sweep() docstring), so no stale module/DB-path state
    can leak between tests."""
    spec = importlib.util.spec_from_file_location(
        "rdz_test", os.path.join(SCRIPTS_DIR, "reconcile_dispatched_dead_zone.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT status, ts_dispatched, reason FROM umr_tasks WHERE umr_id=?", (umr_id,)
    ).fetchone()
    conn.close()
    return dict(r) if r else None


def _pm_decisions(path, related_umr=None):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if related_umr:
        rows = conn.execute(
            "SELECT * FROM pm_decisions_pending WHERE related_umr=? ORDER BY id", (related_umr,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM pm_decisions_pending ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# (a) genuinely dead row -> auto-reset + informational audit log
# ---------------------------------------------------------------------------
def test_dead_row_auto_resets_and_writes_audit_log():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-dead-a", "owner-task-dead-a", old_ts)

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(now=now)

        assert len(results) == 1
        assert results[0]["bucket"] == "DEAD_ZONE"
        assert results[0]["action"] == "auto_reset_to_queued"

        after = _row(db_path, "UMR-dead-a")
        assert after["status"] == "queued", f"expected real auto-reset to queued, got {after}"
        assert after["ts_dispatched"] is None, "ts_dispatched must be cleared on reset"

        decisions = _pm_decisions(db_path, related_umr="UMR-dead-a")
        assert len(decisions) == 1
        d = decisions[0]
        assert d["decision_type"] == "dead_zone_auto_remediation"
        assert d["status"] == "resolved", (
            "audit-log entry must be informational only, never a blocking open gate")
        print("PASS: test_dead_row_auto_resets_and_writes_audit_log")


# ---------------------------------------------------------------------------
# (b) second occurrence -> escalate, do NOT reset again
# ---------------------------------------------------------------------------
def test_second_occurrence_escalates_instead_of_re_resetting():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-repeat-a", "owner-task-repeat-a", old_ts)
        _insert_prior_audit_log(db_path, "UMR-repeat-a")

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(now=now)

        assert len(results) == 1
        assert results[0]["bucket"] == "DEAD_ZONE"
        assert results[0]["action"] == "escalated_blocking_decision"

        after = _row(db_path, "UMR-repeat-a")
        assert after["status"] == "dispatched", (
            f"a second-occurrence row must NOT be silently re-reset, got {after}")
        assert after["ts_dispatched"] == old_ts, "ts_dispatched must be untouched on escalation"

        decisions = _pm_decisions(db_path, related_umr="UMR-repeat-a")
        # the pre-seeded prior audit-log row + the new escalation row
        assert len(decisions) == 2
        escalation = [d for d in decisions if d["decision_type"] == "pm_decision"]
        assert len(escalation) == 1
        assert escalation[0]["status"] == "open", (
            "the escalation must be a real, blocking, open decision")
        assert escalation[0]["title"].startswith("DEAD-ZONE REPEAT")
        print("PASS: test_second_occurrence_escalates_instead_of_re_resetting")


def test_second_run_does_not_open_a_duplicate_escalation():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-repeat-b", "owner-task-repeat-b", old_ts)
        _insert_prior_audit_log(db_path, "UMR-repeat-b")

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            rdz.run_sweep(now=now)
            results2 = rdz.run_sweep(now=now)

        assert results2[0]["action"] == "already_escalated_skipped"
        decisions = _pm_decisions(db_path, related_umr="UMR-repeat-b")
        open_escalations = [d for d in decisions if d["status"] == "open"]
        assert len(open_escalations) == 1, (
            f"must never open a second escalation while one is already open, got {decisions}")
        print("PASS: test_second_run_does_not_open_a_duplicate_escalation")


# ---------------------------------------------------------------------------
# (c) negative controls -- real task dir / real systemd unit -> never touched
# ---------------------------------------------------------------------------
def test_row_with_real_task_dir_not_touched():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(os.path.join(tasks_dir, "owner-task-hasdir-a"))
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-hasdir-a", "owner-task-hasdir-a", old_ts)

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(now=now)

        assert results[0]["bucket"] == "NOT_DEAD_HAS_TASK_DIR"
        assert results[0]["action"] == "none"
        after = _row(db_path, "UMR-hasdir-a")
        assert after["status"] == "dispatched", "a row with a real task dir must never be reset"
        assert _pm_decisions(db_path, related_umr="UMR-hasdir-a") == []
        print("PASS: test_row_with_real_task_dir_not_touched")


def test_row_with_real_unit_name_not_touched():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-hasunit-a", "owner-task-hasunit-a", old_ts,
                         unit_name="veridian-worker@task-hasunit-a.service")

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(now=now)

        assert results[0]["bucket"] == "NOT_DEAD_HAS_SYSTEMD_UNIT"
        assert results[0]["action"] == "none"
        after = _row(db_path, "UMR-hasunit-a")
        assert after["status"] == "dispatched", "a row with a real recorded unit_name must never be reset"
        print("PASS: test_row_with_real_unit_name_not_touched")


def test_row_with_real_ocid_artifact_evidence_not_touched():
    """This script's own added, documented safety guard (see its module
    docstring's real d3b7/57a9 evidence discussion): real completed-work
    evidence in ocid_artifact_links must block an auto-reset even though
    neither a task dir nor a unit_name exists."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-evidence-a", "owner-task-evidence-a", old_ts)
        _insert_ocid_artifact_link(db_path, "UMR-evidence-a")

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(now=now)

        assert results[0]["bucket"] == "NOT_DEAD_HAS_COMPLETED_WORK_EVIDENCE"
        assert results[0]["action"] == "none"
        after = _row(db_path, "UMR-evidence-a")
        assert after["status"] == "dispatched"
        print("PASS: test_row_with_real_ocid_artifact_evidence_not_touched")


# ---------------------------------------------------------------------------
# Threshold boundary + dry-run
# ---------------------------------------------------------------------------
def test_row_younger_than_threshold_not_a_candidate_at_all():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=5)).isoformat()
        _insert_umr_row(db_path, "UMR-recent-a", "owner-task-recent-a", recent_ts)

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(now=now)

        assert results == [], f"a row only 5min old (<15min threshold) must never be a candidate, got {results}"
        after = _row(db_path, "UMR-recent-a")
        assert after["status"] == "dispatched"
        print("PASS: test_row_younger_than_threshold_not_a_candidate_at_all")


def test_dry_run_makes_no_writes():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        tasks_dir = os.path.join(tmp, "tasks")
        os.makedirs(tasks_dir)
        _seed_db(db_path)

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=20)).isoformat()
        _insert_umr_row(db_path, "UMR-dry-a", "owner-task-dry-a", old_ts)

        with _Env(db_path, tasks_dir):
            rdz = _load_rdz()
            results = rdz.run_sweep(dry_run=True, now=now)

        assert results[0]["bucket"] == "DEAD_ZONE"
        assert results[0]["action"] == "would_act (dry-run)"
        after = _row(db_path, "UMR-dry-a")
        assert after["status"] == "dispatched", "dry-run must never write"
        assert _pm_decisions(db_path) == []
        print("PASS: test_dry_run_makes_no_writes")


# ---------------------------------------------------------------------------
# Direct coverage of the new superboss-register.py canonical functions this
# script depends on.
# ---------------------------------------------------------------------------
def test_reset_umr_task_to_queued_only_touches_status_and_ts_dispatched_and_reason():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        _seed_db(db_path)
        now_iso = datetime.now(timezone.utc).isoformat()
        _insert_umr_row(db_path, "UMR-directfn-a", "owner-task-directfn-a", now_iso,
                         unit_name=None)

        sbr = _load_fresh_sbr()
        # sbr._connect() reads DB_PATH computed at THIS module's own import
        # time -- point SUPERBOSS_REGISTER_DB at our real seeded temp db
        # before importing, same convention as _Env()/_load_rdz() above.
        with _Env(db_path, tempfile.mkdtemp()):
            sbr2_spec = importlib.util.spec_from_file_location(
                "sbr_direct_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
            sbr2 = importlib.util.module_from_spec(sbr2_spec)
            sbr2_spec.loader.exec_module(sbr2)
            conn = sbr2._connect()
            with sbr2._write_lock():
                sbr2.reset_umr_task_to_queued(conn, "UMR-directfn-a", reason="test reason")
                conn.commit()
            conn.close()

        after = _row(db_path, "UMR-directfn-a")
        assert after["status"] == "queued"
        assert after["ts_dispatched"] is None
        assert after["reason"] == "test reason"
        print("PASS: test_reset_umr_task_to_queued_only_touches_status_and_ts_dispatched_and_reason")


def test_insert_pm_decision_pending_decision_type_default_unchanged():
    """Backward-compat guard: every real pre-existing caller of
    insert_pm_decision_pending() (cmd_insert_pm_decision_pending, i.e. every
    real pm_decision row ever written before this UMR) must keep getting
    decision_type='pm_decision' when it doesn't pass the new kwarg at all."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.sqlite")
        _seed_db(db_path)
        with _Env(db_path, tempfile.mkdtemp()):
            sbr = _load_fresh_sbr()
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            decision_id = sbr.insert_pm_decision_pending(
                conn, "a real title", "a real detail", related_umr="UMR-x")
            conn.commit()
            row = conn.execute(
                "SELECT decision_type, status FROM pm_decisions_pending WHERE id=?", (decision_id,)
            ).fetchone()
            conn.close()
        assert row["decision_type"] == "pm_decision"
        assert row["status"] == "open"
        print("PASS: test_insert_pm_decision_pending_decision_type_default_unchanged")


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
