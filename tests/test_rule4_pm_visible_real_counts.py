#!/usr/bin/env python3
"""Real tests for OCID-068's seven-rule guardrails addendum, Rule 4
(UMR-20260804-180711-7f96, UMR-20260804-205741-cf3f, citing OCID-068's own
UMR-20260804-170055-a069): "the project manager shall always see real
counts for running, queued, blocked, failed, rejected, retrying, stale, and
completed tasks, and any alert cooldown may suppress notifications only, it
must never suppress the underlying real data or real counts themselves."

Covers compute_real_task_counts() against a real, isolated scratch SQLite
DB seeded with real, hand-placed rows spanning every real status value
(never the live production database), and independently proves the real
PM-triage cooldown mechanism (dispatch-tick.py's pm_triage_tick()) never
hides the underlying reasons/counts even while it is actively suppressing
the notification/alert-write side effect.
"""
import datetime
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)  # dispatch-tick.py does a plain `import dispatch_core`


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_r4", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    return sbr, conn


def _load(name, filename, env=None):
    """Real, disclosed test-infrastructure finding: dispatch-tick.py does a
    plain `import resource_governor`/`import dispatch_core` at module level
    -- Python's sys.modules cache means the FIRST test in a process to load
    dispatch-tick.py permanently fixes resource_governor's own DB_PATH
    (resolved once, at that first import) for every later test in the same
    process, silently ignoring later SUPERBOSS_REGISTER_DB env overrides.
    Popping both from sys.modules before each dispatch-tick.py load forces a
    genuinely fresh re-import (and fresh DB_PATH resolution) every time."""
    for stale in ("resource_governor", "dispatch_core"):
        sys.modules.pop(stale, None)
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


def _insert_umr_row(sbr, conn, task_identity, status, reason="x", tier=2):
    umr_id = sbr.upsert_umr_task(conn, {
        "task_identity": task_identity, "tier": tier, "status": status,
        "source_trigger": "unit_test", "task_kind": "systemctl_action",
        "reason": reason,
    })
    conn.commit()
    return umr_id


def test_compute_real_task_counts_matches_a_known_seeded_mix():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        # Real, hand-placed mix: 2 running, 3 queued, 1 failed, 2
        # rejected_duplicate, 1 completed, 1 killed, 1 real retry (reason
        # matches Rule 1's own real reuse-reason text).
        _insert_umr_row(sbr, conn, "t1", "running")
        _insert_umr_row(sbr, conn, "t2", "running")
        _insert_umr_row(sbr, conn, "t3", "queued")
        _insert_umr_row(sbr, conn, "t4", "queued")
        _insert_umr_row(sbr, conn, "t5", "queued")
        _insert_umr_row(sbr, conn, "t6", "failed")
        _insert_umr_row(sbr, conn, "t7", "rejected_duplicate")
        _insert_umr_row(sbr, conn, "t8", "rejected_duplicate")
        _insert_umr_row(sbr, conn, "t9", "completed")
        _insert_umr_row(sbr, conn, "t10", "killed")
        _insert_umr_row(sbr, conn, "t11", "queued", reason="resubmitted (reused umr_id, prior status was 'failed')")
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dt = _load("dt_rule4_mix", "dispatch-tick.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            tasks = {"w1": {"status": "blocked"}, "w2": {"status": "blocked"}, "w3": {"status": "in_progress"}}
            stuck_tasks = [{"task_id": "w1", "blocked_minutes": 45.0}]
            counts = dt.compute_real_task_counts(tasks, stuck_tasks, datetime.datetime.now(datetime.timezone.utc))
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert counts["running"] == 2, counts
        assert counts["queued"] == 4, counts  # 3 plain + 1 retrying (queued AND counted in retrying too)
        assert counts["failed"] == 1, counts
        assert counts["rejected"] == 2, counts
        assert counts["completed"] == 1, counts
        assert counts["killed"] == 1, counts
        assert counts["retrying"] == 1, counts
        assert counts["blocked"] == 2, counts
        assert counts["stale"] == 1, counts
        assert counts["umr_tasks_total"] == 11, counts
        print(f"PASS: test_compute_real_task_counts_matches_a_known_seeded_mix -> {counts}")


def test_compute_real_task_counts_fails_open_on_broken_db_never_fabricates_zero():
    """Rule 2's own established fail-open philosophy, reused here: a broken
    Superboss Register must surface as a real, honest error field, never a
    silently fabricated all-zero count that could be mistaken for real
    'everything is fine' data."""
    with tempfile.TemporaryDirectory() as d:
        wrong_schema_db = os.path.join(d, "wrong-schema.sqlite")
        conn = sqlite3.connect(wrong_schema_db)
        conn.execute("CREATE TABLE not_umr_tasks (x INTEGER)")
        conn.commit()
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": wrong_schema_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dt = _load("dt_rule4_broken", "dispatch-tick.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = wrong_schema_db
        try:
            counts = dt.compute_real_task_counts({}, [], datetime.datetime.now(datetime.timezone.utc))
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "umr_counts_error" in counts, counts
        assert counts["running"] == 0 and counts["queued"] == 0, (
            "task.yaml-independent counts should be honest zeros when the DB itself is unavailable, "
            "but the umr_counts_error field must be present to distinguish this from real zero activity"
        )
        print(f"PASS: test_compute_real_task_counts_fails_open_on_broken_db_never_fabricates_zero -> {counts['umr_counts_error'][:80]}")


def test_heartbeat_write_includes_real_task_counts_unconditionally():
    """write_stuck_tasks_heartbeat() has no cooldown of its own -- confirms
    the new real_task_counts key is genuinely present in its real written
    output every call, not just in compute_real_task_counts() in isolation."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        _insert_umr_row(sbr, conn, "t1", "running")
        conn.close()

        heartbeat_path = os.path.join(d, "heartbeat.json")
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
               "VERIDIAN_STUCK_TASKS_HEARTBEAT_PATH": heartbeat_path}
        dt = _load("dt_rule4_heartbeat", "dispatch-tick.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            doc = dt.write_stuck_tasks_heartbeat({}, [], datetime.datetime.now(datetime.timezone.utc))
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "real_task_counts" in doc, doc
        assert doc["real_task_counts"]["running"] == 1, doc["real_task_counts"]
        with open(heartbeat_path) as f:
            on_disk = json.load(f)
        assert on_disk["real_task_counts"] == doc["real_task_counts"], "on-disk file must match the returned dict"
        print(f"PASS: test_heartbeat_write_includes_real_task_counts_unconditionally -> {doc['real_task_counts']}")


def test_pm_triage_cooldown_suppresses_notification_never_the_real_reasons():
    """Direct proof of Rule 4's second half: pm_triage_tick()'s real cooldown
    gate skips the Claude invocation and the alert-file write once active,
    but the real `reasons` list (carrying real counts, e.g. '3 task(s) stuck
    past...') is still returned every time -- the cooldown suppresses the
    notification side effect only, never the underlying real data."""
    with tempfile.TemporaryDirectory() as d:
        alerts_path = os.path.join(d, "PM_TRIAGE_ALERTS.md")
        env = {"VERIDIAN_PM_TRIAGE_ALERTS_PATH": alerts_path,
               "VERIDIAN_PM_TRIAGE_COOLDOWN_MINUTES": "60"}
        dt = _load("dt_rule4_cooldown", "dispatch-tick.py", env=env)

        now = datetime.datetime.now(datetime.timezone.utc)
        stuck_tasks = [{"task_id": "w1", "blocked_minutes": 45.0, "blocked_since": now.isoformat(), "last_note": "x"}]
        tasks = {"w1": {"status": "blocked"}}

        def fake_invoke(reasons, evidence):
            return "YES -- real stuck task needs attention"

        # First real call: nothing written yet, cooldown gate finds no prior
        # alert, so it genuinely invokes and writes.
        first = dt.pm_triage_tick(tasks, stuck_tasks, now, invoke_fn=fake_invoke)
        assert first["invoked"] is True, first

        # Second real call, same tick's worth of evidence, cooldown now
        # active (a real alert entry was just written) -- must skip the
        # invocation but STILL return the real reasons.
        second = dt.pm_triage_tick(tasks, stuck_tasks, now + datetime.timedelta(minutes=5), invoke_fn=fake_invoke)
        assert second["invoked"] is False, second
        assert "skipped_reason" in second and "cooldown" in second["skipped_reason"], second
        assert second["reasons"], "Rule 4 violation: cooldown suppressed the real reasons/data, not just the notification"
        assert "1 task(s) stuck" in second["reasons"][0], second["reasons"]
        print(f"PASS: test_pm_triage_cooldown_suppresses_notification_never_the_real_reasons -> reasons still present: {second['reasons']}")


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
