#!/usr/bin/env python3
"""Real tests for the root-cause fix to backfill_null_heartbeats()'s
systemd-dispatched branch (UMR-20260806-082352-7b1b, child of Owner
directive UMR-20260806-081403-ebd3).

Before this fix, ANY NULL-heartbeat row whose systemd unit was confirmed
inactive was unconditionally marked 'failed', even when that task's own
real task.yaml already recorded real forward progress (a referenced PR/
commit) or genuine completion (a merged PR). Live-confirmed this cycle by
manual inspection of 9 real rows: 6 were genuinely 'blocked with real
forward progress' mislabeled 'failed'; 1 was genuinely 'completed' (merged
PR) mislabeled 'failed'; 2 were correctly 'failed' -- their own task.yaml's
claimed progress did NOT hold up under an independent cross-check (a
referenced branch's real tip commit predated the task's own real start
time).

Every test below drives the real `backfill_null_heartbeats()` function
against a real, isolated scratch SQLite DB (never the live production
database) and a real temp TASKS_DIR with real task.yaml files on disk --
only the three real *external* ground-truth calls (`systemctl --user
is-active`, `gh pr view`, `gh api .../commits/<branch>`) are mocked, via a
single fake `_run` dispatcher keyed on the real command shape, exactly the
same "mock the I/O boundary, exercise the real logic" approach
test_rule5_real_stall_detection.py already established for this module.
"""
import datetime
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_bnh", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    return sbr, conn


def _load_governor(env=None):
    for stale in ("resource_governor", "dispatch_core_governor", "dispatch_core"):
        sys.modules.pop(stale, None)
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location("rg_bnh", os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)


def _write_task_yaml(tasks_dir, task_id, doc_text):
    task_dir = os.path.join(tasks_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        f.write(doc_text)
    return task_dir


def _iso(dt):
    return dt.isoformat()


def _fake_run_factory(gh_pr_state=None, gh_commit_date=None, systemctl_active=False, external_ai_ok=False):
    """Builds a fake `_run(cmd, **kw)` that recognizes exactly the real
    command shapes backfill_null_heartbeats()'s systemd branch issues:
    `systemctl --user is-active`, `gh pr view ... --json ...`, and
    `gh api repos/.../commits/<branch> --jq ...`. Anything else (e.g. the
    unconditional external_ai_state_machine.py list-sessions call) returns a
    real non-zero exit, matching '_external_ai_list_sessions' -> None
    behavior, so the (empty, in these tests) external-rows path is a no-op."""
    def _run(cmd, **kw):
        result = types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if cmd[:2] == ["systemctl", "--user"]:
            result.returncode = 0 if systemctl_active else 1
            return result
        if cmd[:3] == ["gh", "pr", "view"]:
            if gh_pr_state is None:
                result.returncode = 1
                result.stderr = "gh: pr not found"
            else:
                result.returncode = 0
                result.stdout = json.dumps(gh_pr_state)
            return result
        if cmd[:2] == ["gh", "api"]:
            if gh_commit_date is None:
                result.returncode = 1
                result.stderr = "gh: no commit found"
            else:
                result.returncode = 0
                result.stdout = gh_commit_date
            return result
        if external_ai_ok:
            result.returncode = 0
            result.stdout = json.dumps({"sessions": []})
        return result
    return _run


def _run_backfill(tmpdir, task_yaml_text, task_id="task-x", fake_run=None, execute=True, extra_row_fields=None):
    scratch_db = os.path.join(tmpdir, "scratch.sqlite")
    sbr, conn = _seed_scratch_db(scratch_db)
    row = {
        "task_identity": task_id, "tier": 2, "status": "running",
        "source_trigger": "unit_test", "task_kind": "systemctl_action",
        "unit_name": f"veridian-worker@{task_id}.service", "reason": "seed",
    }
    row.update(extra_row_fields or {})
    umr_id = sbr.upsert_umr_task(conn, row)
    conn.execute("UPDATE umr_tasks SET last_heartbeat=NULL WHERE umr_id=?", (umr_id,))
    conn.commit()
    conn.close()

    tasks_dir = os.path.join(tmpdir, "tasks")
    os.makedirs(tasks_dir, exist_ok=True)
    if task_yaml_text is not None:
        _write_task_yaml(tasks_dir, task_id, task_yaml_text)

    env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_TASKS_DIR": tasks_dir}
    gov = _load_governor(env=env)
    if fake_run is not None:
        gov._run = fake_run

    # _superboss_register()/_dispatch_core() are lazily loaded on first real
    # call INSIDE backfill_null_heartbeats() -- not at module-exec time above
    # -- so the env vars must be live again right here, same real pattern
    # test_rule5_real_stall_detection.py's own end-to-end test already
    # established for this exact "lazy env-gated importlib load" shape.
    old_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        report = gov.backfill_null_heartbeats(execute=execute)
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    conn2 = sqlite3.connect(scratch_db)
    conn2.row_factory = sqlite3.Row
    final_row = dict(conn2.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone())
    conn2.close()
    return report, final_row


def test_active_unit_left_alone_untouched_regression():
    """Pre-existing behavior, unchanged by this fix: a genuinely active unit
    is left alone, no task.yaml is even needed."""
    with tempfile.TemporaryDirectory() as d:
        fake_run = _fake_run_factory(systemctl_active=True)
        report, final_row = _run_backfill(d, task_yaml_text=None, fake_run=fake_run)
        assert report["counts"]["systemd_left_active"] == 1, report
        assert final_row["status"] == "running", final_row
        assert final_row["last_heartbeat"] is None, final_row
        print("PASS: test_active_unit_left_alone_untouched_regression")


def test_no_task_yaml_falls_back_to_original_failed_behavior():
    """No task.yaml on disk at all (e.g. a row from before TASKS_DIR
    retention, or a real filesystem hiccup) -- must fail toward the exact
    original unconditional-'failed' behavior, not silently skip the row."""
    with tempfile.TemporaryDirectory() as d:
        fake_run = _fake_run_factory(systemctl_active=False)
        report, final_row = _run_backfill(d, task_yaml_text=None, fake_run=fake_run)
        assert report["counts"]["systemd_marked_failed"] == 1, report
        assert final_row["status"] == "failed", final_row
        entry = report["examined"][0]
        assert "no task.yaml" in entry["evidence"]["cross_check"], entry
        print("PASS: test_no_task_yaml_falls_back_to_original_failed_behavior")


def test_task_yaml_already_terminal_failed_status_no_override():
    """task.yaml itself already recorded a genuinely terminal-negative
    outcome -- agrees with the original default, no override needed."""
    with tempfile.TemporaryDirectory() as d:
        doc = "status: failed\nrepo: veridian-scripts\n"
        fake_run = _fake_run_factory(systemctl_active=False)
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert final_row["status"] == "failed", final_row
        print("PASS: test_task_yaml_already_terminal_failed_status_no_override")


def test_referenced_pr_merged_marks_completed():
    """Real, live-confirmed case #2 (1-of-9): a referenced PR that `gh pr
    view` confirms is MERGED must map to 'completed', not 'failed'."""
    with tempfile.TemporaryDirectory() as d:
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/veridian-scripts/pull/55\n"
        )
        fake_run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "MERGED", "mergedAt": "2026-08-05T12:00:00Z", "mergeCommit": {"oid": "abc123"}, "url": "x"},
        )
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert report["counts"]["systemd_marked_completed"] == 1, report
        assert final_row["status"] == "completed", final_row
        assert final_row["ts_completed"] is not None, final_row
        print("PASS: test_referenced_pr_merged_marks_completed")


def test_referenced_pr_open_marks_running():
    """Real, live-confirmed case #1 (6-of-9 shape): a referenced PR that
    `gh pr view` confirms is still OPEN (blocked by a gate, not dead) must
    map to 'running', not 'failed'."""
    with tempfile.TemporaryDirectory() as d:
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "checkpoints:\n"
            "  - note: 'opened https://github.com/FChecklist/veridian-scripts/pull/117, blocked on tier2 signoff'\n"
        )
        fake_run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "OPEN", "mergedAt": None, "mergeCommit": None, "url": "x"},
        )
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert report["counts"]["systemd_marked_running"] == 1, report
        assert final_row["status"] == "running", final_row
        assert final_row["last_heartbeat"] is not None, final_row
        print("PASS: test_referenced_pr_open_marks_running")


def test_referenced_pr_closed_unmerged_stays_failed():
    """A referenced PR that `gh pr view` confirms is genuinely CLOSED
    (rejected, not merged) must stay 'failed' -- an override must be
    evidence-based, not automatic just because SOME PR exists."""
    with tempfile.TemporaryDirectory() as d:
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/veridian-scripts/pull/9\n"
        )
        fake_run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "CLOSED", "mergedAt": None, "mergeCommit": None, "url": "x"},
        )
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert report["counts"]["systemd_marked_failed"] == 1, report
        assert final_row["status"] == "failed", final_row
        print("PASS: test_referenced_pr_closed_unmerged_stays_failed")


def test_unverifiable_pr_falls_back_to_failed():
    """`gh pr view` itself fails/errors (auth hiccup, rate limit, network) --
    must fail toward the original default, never guess 'running'/'completed'
    from an unverifiable claim."""
    with tempfile.TemporaryDirectory() as d:
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/veridian-scripts/pull/9\n"
        )
        fake_run = _fake_run_factory(systemctl_active=False, gh_pr_state=None)
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert final_row["status"] == "failed", final_row
        entry = report["examined"][0]
        assert "unverifiable" in entry["evidence"]["cross_check"], entry
        print("PASS: test_unverifiable_pr_falls_back_to_failed")


def test_blocked_status_with_branch_tip_postdating_creation_marks_running():
    """Real, live-confirmed case #1 shape, no-PR-yet variant: 'blocked'
    status, a real referenced branch, and that branch's own real tip commit
    genuinely postdates the task's own real created_at -- real forward
    progress, must map to 'running'."""
    with tempfile.TemporaryDirectory() as d:
        created_at = datetime.datetime(2026, 8, 6, 7, 0, 0, tzinfo=datetime.timezone.utc)
        tip_commit_date = created_at + datetime.timedelta(minutes=30)
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "branch: worker/task-x\n"
            f"created_at: '{_iso(created_at)}'\n"
        )
        fake_run = _fake_run_factory(systemctl_active=False, gh_commit_date=_iso(tip_commit_date))
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert report["counts"]["systemd_marked_running"] == 1, report
        assert final_row["status"] == "running", final_row
        print("PASS: test_blocked_status_with_branch_tip_postdating_creation_marks_running")


def test_blocked_status_branch_evidence_missing_created_at_stays_failed():
    """Independent-review blocking finding, now fixed: a real, verifiable
    branch tip commit is NOT enough on its own -- if this task's own
    created_at is missing/unparseable, there is nothing to postdate-compare
    it against, so the claim is unverifiable and must stay 'failed', not
    silently fall through to 'running'."""
    with tempfile.TemporaryDirectory() as d:
        tip_commit_date = datetime.datetime(2026, 8, 6, 7, 30, 0, tzinfo=datetime.timezone.utc)
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "branch: worker/task-x\n"
            # deliberately no created_at field at all
        )
        fake_run = _fake_run_factory(systemctl_active=False, gh_commit_date=_iso(tip_commit_date))
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert final_row["status"] == "failed", final_row
        entry = report["examined"][0]
        assert "created_at missing" in entry["evidence"]["cross_check"], entry
        print("PASS: test_blocked_status_branch_evidence_missing_created_at_stays_failed")


def test_referenced_pr_in_different_repo_is_not_trusted():
    """A PR URL naming a DIFFERENT repo than this task's own top-level
    `repo` field must not be looked up/trusted -- treated as no real
    reference at all, same as if no PR were referenced."""
    with tempfile.TemporaryDirectory() as d:
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/some-other-repo/pull/55\n"
        )
        fake_run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "MERGED", "mergedAt": "2026-08-05T12:00:00Z", "mergeCommit": {"oid": "x"}, "url": "x"},
        )
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert final_row["status"] == "failed", final_row
        entry = report["examined"][0]
        assert "pr_number" not in entry["evidence"], entry
        print("PASS: test_referenced_pr_in_different_repo_is_not_trusted")


def test_blocked_status_with_branch_tip_predating_creation_stays_failed():
    """Real, live-confirmed case #3 (2-of-9): the exact real failure mode
    independent review caught -- a claimed 'blocked, real progress' branch
    whose own real tip commit PREDATES the task's own real created_at (stale/
    unrelated branch reference). Must NOT be trusted -- stays 'failed'."""
    with tempfile.TemporaryDirectory() as d:
        created_at = datetime.datetime(2026, 8, 6, 7, 0, 0, tzinfo=datetime.timezone.utc)
        stale_tip_commit_date = created_at - datetime.timedelta(days=3)
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "branch: worker/task-x\n"
            f"created_at: '{_iso(created_at)}'\n"
        )
        fake_run = _fake_run_factory(systemctl_active=False, gh_commit_date=_iso(stale_tip_commit_date))
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert report["counts"]["systemd_marked_failed"] == 1, report
        assert final_row["status"] == "failed", final_row
        entry = report["examined"][0]
        assert "predates" in entry["evidence"]["cross_check"], entry
        print("PASS: test_blocked_status_with_branch_tip_predating_creation_stays_failed")


def test_blocked_status_no_branch_or_pr_evidence_stays_failed():
    """'blocked' status alone, with no real referenced PR or branch at all --
    no evidence to cross-check, stays 'failed' (original default)."""
    with tempfile.TemporaryDirectory() as d:
        doc = "status: blocked\nrepo: veridian-scripts\n"
        fake_run = _fake_run_factory(systemctl_active=False)
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run)
        assert final_row["status"] == "failed", final_row
        print("PASS: test_blocked_status_no_branch_or_pr_evidence_stays_failed")


def test_owner_dispatch_gateway_row_correlates_via_adopted_reconcile_task():
    """Real gap found during this fix's own live re-verification against the
    current 24h owner_dispatch_gateway set: such a row's task_identity is a
    synthetic 'owner-task-<ts>-<pid>' string that is never itself a TASKS_DIR
    directory name -- the real remediation/progress record, when one exists,
    lives in a SEPARATE 'adopted-reconcile-umr-<umr_id>-...' task instead
    (exactly matching the real live task-20260806-072312-adopted-reconcile-
    umr-20260806-042531-be9c--pr11 task.yaml this fix's own re-verification
    found). _task_yaml_for_umr_row()'s fallback path must find that task and
    apply the same real evidence-based decision to it."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        row = {
            "task_identity": "owner-task-20260806-042530-1307251", "tier": 2, "status": "running",
            "source_trigger": "owner_dispatch_gateway", "task_kind": "systemctl_action",
            "unit_name": "veridian-worker@owner-task-20260806-042530-1307251.service", "reason": "seed",
        }
        umr_id = sbr.upsert_umr_task(conn, row)
        conn.execute("UPDATE umr_tasks SET last_heartbeat=NULL WHERE umr_id=?", (umr_id,))
        conn.commit()
        conn.close()

        umr_suffix = umr_id[4:]  # strip 'UMR-' prefix, matches real dir-naming convention
        tasks_dir = os.path.join(d, "tasks")
        reconcile_task_id = f"task-20260806-072312-adopted-reconcile-umr-{umr_suffix}--pr117"
        doc_text = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/veridian-scripts/pull/117\n"
        )
        _write_task_yaml(tasks_dir, reconcile_task_id, doc_text)

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_TASKS_DIR": tasks_dir}
        gov = _load_governor(env=env)
        gov._run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "OPEN", "mergedAt": None, "mergeCommit": None, "url": "x"},
        )
        old_env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            report = gov.backfill_null_heartbeats(execute=True)
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert report["counts"]["systemd_marked_running"] == 1, report
        conn2 = sqlite3.connect(scratch_db)
        conn2.row_factory = sqlite3.Row
        final_row = dict(conn2.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone())
        conn2.close()
        assert final_row["status"] == "running", final_row
        print("PASS: test_owner_dispatch_gateway_row_correlates_via_adopted_reconcile_task")


def test_owner_dispatch_gateway_row_correlates_via_unit_name_derived_child_task():
    """Real gap found by UMR-20260807-112306-4e60 (reconciling 5 real
    owner_dispatch_gateway rows -- UMR-20260807-061238-ae93/070110-5ea7/
    070904-736a/035145-aa45/040704-992a): a systemd-dispatched row's real
    child task.yaml does not require a separately-created
    'adopted-reconcile-umr-...' task to be found (path 3) -- its own real
    `unit_name` column already encodes the exact real TASKS_DIR directory
    name (path 2, new). All 5 real rows this fix was built against hit
    exactly this shape: synthetic 'owner-task-<ts>-<pid>' task_identity
    (path 1 misses), no 'reconcile-umr-' anywhere in the real child
    directory name (path 3 misses), but unit_name='veridian-worker@<real
    child task id>.service' resolves directly (path 2 catches it)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        child_task_id = "task-20260807-081903-mandatory-execute-the-rebuild--do-not-in"
        row = {
            "task_identity": "owner-task-20260807-061237-3886557", "tier": 0, "status": "running",
            "source_trigger": "owner_dispatch_gateway", "task_kind": "veridian_task_create",
            "unit_name": f"veridian-worker@{child_task_id}.service", "reason": "seed",
        }
        umr_id = sbr.upsert_umr_task(conn, row)
        conn.execute("UPDATE umr_tasks SET last_heartbeat=NULL WHERE umr_id=?", (umr_id,))
        conn.commit()
        conn.close()

        tasks_dir = os.path.join(d, "tasks")
        doc_text = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/veridian-scripts/pull/254\n"
        )
        _write_task_yaml(tasks_dir, child_task_id, doc_text)

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_TASKS_DIR": tasks_dir}
        gov = _load_governor(env=env)
        gov._run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "OPEN", "mergedAt": None, "mergeCommit": None, "url": "x"},
        )
        old_env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            report = gov.backfill_null_heartbeats(execute=True)
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert report["counts"]["systemd_marked_running"] == 1, report
        conn2 = sqlite3.connect(scratch_db)
        conn2.row_factory = sqlite3.Row
        final_row = dict(conn2.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone())
        conn2.close()
        assert final_row["status"] == "running", final_row
        assert final_row["last_heartbeat"] is not None, final_row
        print("PASS: test_owner_dispatch_gateway_row_correlates_via_unit_name_derived_child_task")


def test_dry_run_never_writes_even_when_evidence_says_completed():
    """execute=False (the default) must compute and report the identical
    decision without writing anything -- 'would_mark_completed', DB row left
    exactly as seeded."""
    with tempfile.TemporaryDirectory() as d:
        doc = (
            "status: blocked\n"
            "repo: veridian-scripts\n"
            "adopted_pr_url: https://github.com/FChecklist/veridian-scripts/pull/55\n"
        )
        fake_run = _fake_run_factory(
            systemctl_active=False,
            gh_pr_state={"state": "MERGED", "mergedAt": "2026-08-05T12:00:00Z", "mergeCommit": {"oid": "abc"}, "url": "x"},
        )
        report, final_row = _run_backfill(d, task_yaml_text=doc, fake_run=fake_run, execute=False)
        assert report["examined"][0]["decision"] == "would_mark_completed", report
        assert report["counts"]["systemd_marked_completed"] == 0, report
        assert final_row["status"] == "running", final_row  # untouched, still the seeded value
        assert final_row["last_heartbeat"] is None, final_row
        print("PASS: test_dry_run_never_writes_even_when_evidence_says_completed")


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
