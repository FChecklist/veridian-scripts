#!/usr/bin/env python3
"""Real tests for triage_owner_umr_24h.py (UMR-20260806-091345-d90c, child of
UMR-20260806-071025-1d28). Every test uses a real, isolated, temp-file
SQLite database seeded with the real umr_tasks schema -- never the live
production database. git/gh/task.yaml collaborators are mocked explicitly
via dependency injection (gather_evidence's keyword args) rather than by
patching subprocess globally, so each test's real intent stays visible in
its own arguments.
"""
import importlib.util
import json
import os
import sqlite3
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
TRIAGE_PATH = os.path.join(SCRIPTS_DIR, "triage_owner_umr_24h.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_and_load_sbr(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bootstrap = _load("sbr_bootstrap_triage", SBR_PATH)
    bootstrap._ensure_umr_table(conn)
    conn.commit()
    conn.close()
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    try:
        return _load("sbr_scratch_triage", SBR_PATH)
    finally:
        del os.environ["SUPERBOSS_REGISTER_DB"]


def _insert_row(conn, **overrides):
    row = {
        "umr_id": "UMR-20260805-000000-aaaa",
        "task_identity": "owner-task-test",
        "ts_submitted": "2026-08-05T12:00:00+00:00",
        "tier": 1,
        "status": "failed",
        "source_trigger": "owner_dispatch_gateway",
        "unit_name": "veridian-worker@task-20260805-120000-example.service",
        "reason": "",
        "metadata_json": "{}",
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, unit_name, reason, metadata_json) VALUES "
        "(:umr_id, :task_identity, :ts_submitted, :tier, :status, :source_trigger, "
        ":unit_name, :reason, :metadata_json)",
        row,
    )


def _scratch_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    sbr = _seed_and_load_sbr(tmp.name)
    conn = sbr._connect()
    return sbr, conn, tmp.name


# --------------------------- classify() unit tests --------------------------

def _base_evidence(**overrides):
    ev = {
        "merge_commit_sha": None, "merge_commit_is_ancestor_of_main": None,
        "superseding_umr": None, "credit_accountant_rejected": False,
        "missing_reviewer_identity": False, "owner_decision_required": False,
        "merge_conflict": False, "quality_gate_failed": False,
        "no_task_yaml_ever_written": False, "pr_number": None,
    }
    ev.update(overrides)
    return ev


def test_classify_already_done_requires_real_ancestor_confirmation():
    triage = _load("triage_classify1", TRIAGE_PATH)
    ev = _base_evidence(merge_commit_sha="abc1234", merge_commit_is_ancestor_of_main=True)
    bucket, reason = triage.classify(ev)
    assert bucket == "already_done"
    assert "abc1234" in reason


def test_classify_merge_commit_present_but_not_confirmed_ancestor_does_not_bucket1():
    """A merge_commit_sha alone (e.g. GitHub mergedAt without a real local
    ancestor check succeeding) must NOT be enough for bucket 1 -- the spec
    requires the actual git merge-base confirmation."""
    triage = _load("triage_classify2", TRIAGE_PATH)
    ev = _base_evidence(merge_commit_sha="abc1234", merge_commit_is_ancestor_of_main=False)
    bucket, _ = triage.classify(ev)
    assert bucket != "already_done"
    ev2 = _base_evidence(merge_commit_sha="abc1234", merge_commit_is_ancestor_of_main=None)
    bucket2, _ = triage.classify(ev2)
    assert bucket2 != "already_done"


def test_classify_superseded():
    triage = _load("triage_classify3", TRIAGE_PATH)
    ev = _base_evidence(superseding_umr="UMR-20260806-000000-bbbb", superseding_umr_status="completed")
    bucket, reason = triage.classify(ev)
    assert bucket == "superseded"
    assert "UMR-20260806-000000-bbbb" in reason


def test_classify_blocked_credit_accountant():
    triage = _load("triage_classify4", TRIAGE_PATH)
    ev = _base_evidence(credit_accountant_rejected=True)
    bucket, reason = triage.classify(ev)
    assert bucket == "blocked"
    assert "credit-accountant" in reason


def test_classify_blocked_missing_reviewer_identity_cites_ocid070():
    triage = _load("triage_classify5", TRIAGE_PATH)
    ev = _base_evidence(missing_reviewer_identity=True)
    bucket, reason = triage.classify(ev)
    assert bucket == "blocked"
    assert "OCID-070" in reason
    assert "UMR-20260805-034917-33a9" in reason


def test_classify_retryable_merge_conflict():
    triage = _load("triage_classify6", TRIAGE_PATH)
    ev = _base_evidence(merge_conflict=True)
    bucket, reason = triage.classify(ev)
    assert bucket == "retryable"
    assert "merge conflict" in reason


def test_classify_retryable_no_task_yaml_no_pr():
    triage = _load("triage_classify7", TRIAGE_PATH)
    ev = _base_evidence(no_task_yaml_ever_written=True, pr_number=None)
    bucket, _ = triage.classify(ev)
    assert bucket == "retryable"


def test_classify_no_task_yaml_but_pr_exists_is_not_auto_retryable():
    """If a PR number IS known, 'no task.yaml ever written' alone should not
    win -- a real PR existing means real work happened, so the fallback
    (blocked / needs-Owner-look) is the safe default, not an assumed
    transient-death retry."""
    triage = _load("triage_classify8", TRIAGE_PATH)
    ev = _base_evidence(no_task_yaml_ever_written=True, pr_number=42)
    bucket, _ = triage.classify(ev)
    assert bucket == "blocked"


def test_classify_deterministic_fallback_is_blocked_never_a_fifth_bucket():
    triage = _load("triage_classify9", TRIAGE_PATH)
    ev = _base_evidence()
    bucket, reason = triage.classify(ev)
    assert bucket in triage.BUCKETS
    assert bucket == "blocked"
    assert "deterministic fallback" in reason


def test_classify_priority_order_already_done_beats_everything_else():
    """Even if other blocker signals are also (incorrectly) present, a
    confirmed real merge to main always wins -- priority order is fixed."""
    triage = _load("triage_classify10", TRIAGE_PATH)
    ev = _base_evidence(
        merge_commit_sha="deadbee", merge_commit_is_ancestor_of_main=True,
        credit_accountant_rejected=True, merge_conflict=True,
    )
    bucket, _ = triage.classify(ev)
    assert bucket == "already_done"


def test_classify_is_reproducible_across_repeated_calls():
    triage = _load("triage_classify11", TRIAGE_PATH)
    ev = _base_evidence(merge_conflict=True)
    results = {triage.classify(dict(ev)) for _ in range(5)}
    assert len(results) == 1


# --------------------------- gather_evidence tests ---------------------------

def test_gather_evidence_extracts_pushed_merge_commit_and_confirms_ancestor():
    triage = _load("triage_gather1", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        row = {
            "umr_id": "UMR-1", "task_identity": "t1", "status": "failed",
            "ts_submitted": "2026-08-05T00:00:00+00:00",
            "unit_name": "veridian-worker@task-x.service",
            "logs_ref": None,
            "reason": "real pushed merge commit cc5dea73 to origin/main (veridian-scripts)",
            "metadata_json": "{}",
        }
        ancestor_calls = []

        def fake_ancestor(repo, sha):
            ancestor_calls.append((repo, sha))
            return True

        evidence = triage.gather_evidence(
            conn, row, task_yaml_reader=lambda tid: None,
            ancestor_checker=fake_ancestor,
            pr_viewer=lambda repo, num: None,
            superseding_finder=lambda c, r: None,
            branch_pr_finder=lambda repo, tid: None,
        )
        assert evidence["merge_commit_sha"] == "cc5dea73"
        assert evidence["merge_commit_is_ancestor_of_main"] is True
    finally:
        conn.close()
        os.unlink(path)


def test_gather_evidence_finds_pr_by_branch_when_no_pr_number_recorded():
    """New real evidence source (2026-08-06 hardening): the spec requires
    real PR state for EVERY failed/killed row, not only ones that already
    happen to have a PR number recorded in their own text. Proves a row
    whose task.yaml names a real repo but whose own reason/metadata never
    mentions a PR number still gets a real `worker/<task_id>` branch lookup,
    and that a real merged result from it can drive already_done."""
    triage = _load("triage_gather1c", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        row = {
            "umr_id": "UMR-1c", "task_identity": "t1c", "status": "failed",
            "ts_submitted": "2026-08-05T00:00:00+00:00",
            "unit_name": "veridian-worker@task-z.service",
            "logs_ref": None, "reason": "queued", "metadata_json": "{}",
        }
        branch_calls = []

        def fake_branch_pr_finder(repo, task_id):
            branch_calls.append((repo, task_id))
            return {"number": 999, "state": "MERGED", "mergedAt": "2026-08-06T00:00:00Z",
                    "mergeCommit": {"oid": "cafef00d"}}

        evidence = triage.gather_evidence(
            conn, row, task_yaml_reader=lambda tid: {"repo": "veridian-scripts"},
            ancestor_checker=lambda repo, sha: True,
            pr_viewer=lambda repo, num: None,
            superseding_finder=lambda c, r: None,
            branch_pr_finder=fake_branch_pr_finder,
        )
        assert branch_calls == [("veridian-scripts", "task-z")]
        assert evidence["pr_number"] == 999
        assert evidence["merge_commit_sha"] == "cafef00d"
        bucket, _ = triage.classify(evidence)
        assert bucket == "already_done"
    finally:
        conn.close()
        os.unlink(path)


def test_gather_evidence_branch_pr_finder_never_called_without_a_known_repo():
    """The branch-PR lookup must never fire on a guessed repo -- only when
    task.yaml or the row's own text already names one."""
    triage = _load("triage_gather1d", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        row = {
            "umr_id": "UMR-1d", "task_identity": "t1d", "status": "failed",
            "ts_submitted": "2026-08-05T00:00:00+00:00",
            "unit_name": "veridian-worker@task-w.service",
            "logs_ref": None, "reason": "queued", "metadata_json": "{}",
        }
        branch_calls = []
        evidence = triage.gather_evidence(
            conn, row, task_yaml_reader=lambda tid: None,
            ancestor_checker=lambda repo, sha: None,
            pr_viewer=lambda repo, num: None,
            superseding_finder=lambda c, r: None,
            branch_pr_finder=lambda repo, tid: branch_calls.append((repo, tid)),
        )
        assert branch_calls == []
        assert evidence["repo"] is None
        assert evidence["pr_number"] is None
    finally:
        conn.close()
        os.unlink(path)


def test_gather_evidence_ignores_pr_numbers_buried_in_reuse_check_result():
    """Real bug found + fixed 2026-08-06: reuse_check_result is a background
    'similar prior work?' search dump the dispatch pipeline attaches to
    virtually every row, confirmed live to run 1-4MB and reference dozens of
    real but UNRELATED PRs from across the codebase. A PR number/repo hint
    buried in there must never be treated as evidence about THIS row's own
    outcome, even if that PR later genuinely merges -- reproduces the exact
    shape found live (UMR-20260805-002929-5560: an OCID-047/OCID-050 stall
    recovery row misclassified already_done via an unrelated compliance-
    tracker PR #562 mention deep inside reuse_check_result)."""
    triage = _load("triage_gather1b", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        row = {
            "umr_id": "UMR-1b", "task_identity": "t1b", "status": "failed",
            "ts_submitted": "2026-08-05T00:00:00+00:00",
            "unit_name": "veridian-worker@task-y.service",
            "logs_ref": None,
            "reason": "queued",
            "metadata_json": json.dumps({
                "reuse_check_result": {
                    "intent_text": "unrelated prior search",
                    "findings": "compliance-tracker PR #562 (phase 4) confirmed still open",
                },
            }),
        }
        pr_viewer_calls = []

        def fake_pr_viewer(repo, num):
            pr_viewer_calls.append((repo, num))
            return {"number": num, "state": "MERGED", "mergedAt": "2026-08-06T00:00:00Z",
                    "mergeCommit": {"oid": "deadbeef"}}

        evidence = triage.gather_evidence(
            conn, row, task_yaml_reader=lambda tid: None,
            ancestor_checker=lambda repo, sha: True,
            pr_viewer=fake_pr_viewer,
            superseding_finder=lambda c, r: None,
        )
        assert evidence["pr_number"] is None
        assert evidence["repo"] is None
        assert pr_viewer_calls == []
        bucket, _ = triage.classify(evidence)
        assert bucket != "already_done"
    finally:
        conn.close()
        os.unlink(path)


def test_gather_evidence_credit_accountant_signal_from_metadata():
    triage = _load("triage_gather2", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        row = {
            "umr_id": "UMR-2", "task_identity": "t2", "status": "failed",
            "ts_submitted": "2026-08-05T00:00:00+00:00",
            "unit_name": None, "logs_ref": None, "reason": "",
            "metadata_json": json.dumps({"evidence": "credit accountant rejected auto-fix attempt 1"}),
        }
        evidence = triage.gather_evidence(
            conn, row, task_yaml_reader=lambda tid: None,
            ancestor_checker=lambda repo, sha: None,
            pr_viewer=lambda repo, num: None,
            superseding_finder=lambda c, r: None,
        )
        assert evidence["credit_accountant_rejected"] is True
        bucket, _ = triage.classify(evidence)
        assert bucket == "blocked"
    finally:
        conn.close()
        os.unlink(path)


def test_gather_evidence_finds_superseding_row_via_real_db_query():
    triage = _load("triage_gather3", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        _insert_row(conn, umr_id="UMR-old", task_identity="shared-goal",
                    ts_submitted="2026-08-05T00:00:00+00:00", status="failed")
        _insert_row(conn, umr_id="UMR-new", task_identity="shared-goal",
                    ts_submitted="2026-08-05T06:00:00+00:00", status="completed")
        conn.commit()
        row = dict(conn.execute("SELECT * FROM umr_tasks WHERE umr_id='UMR-old'").fetchone())
        evidence = triage.gather_evidence(
            conn, row, task_yaml_reader=lambda tid: None,
            ancestor_checker=lambda repo, sha: None,
            pr_viewer=lambda repo, num: None,
        )
        assert evidence["superseding_umr"] == "UMR-new"
        bucket, reason = triage.classify(evidence)
        assert bucket == "superseded"
        assert "UMR-new" in reason
    finally:
        conn.close()
        os.unlink(path)


def test_find_superseding_row_ignores_earlier_and_terminal_failure_rows():
    triage = _load("triage_gather4", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        _insert_row(conn, umr_id="UMR-mid", task_identity="goal-b",
                    ts_submitted="2026-08-05T06:00:00+00:00", status="failed")
        _insert_row(conn, umr_id="UMR-earlier", task_identity="goal-b",
                    ts_submitted="2026-08-05T00:00:00+00:00", status="completed")
        _insert_row(conn, umr_id="UMR-later-but-failed", task_identity="goal-b",
                    ts_submitted="2026-08-05T09:00:00+00:00", status="killed")
        conn.commit()
        row = dict(conn.execute("SELECT * FROM umr_tasks WHERE umr_id='UMR-mid'").fetchone())
        result = triage.find_superseding_row(conn, row)
        assert result is None
    finally:
        conn.close()
        os.unlink(path)


def test_find_superseding_row_deterministic_tie_break_picks_max_ts_submitted():
    triage = _load("triage_gather5", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        _insert_row(conn, umr_id="UMR-src", task_identity="goal-c",
                    ts_submitted="2026-08-05T00:00:00+00:00", status="failed")
        _insert_row(conn, umr_id="UMR-cand1", task_identity="goal-c",
                    ts_submitted="2026-08-05T05:00:00+00:00", status="completed")
        _insert_row(conn, umr_id="UMR-cand2", task_identity="goal-c",
                    ts_submitted="2026-08-05T08:00:00+00:00", status="running")
        conn.commit()
        row = dict(conn.execute("SELECT * FROM umr_tasks WHERE umr_id='UMR-src'").fetchone())
        result = triage.find_superseding_row(conn, row)
        assert result["umr_id"] == "UMR-cand2"
    finally:
        conn.close()
        os.unlink(path)


# --------------------------- write-path tests ---------------------------

def test_apply_classification_writes_only_via_update_umr_task():
    """Same convention as reconcile_owner_dispatch_status.py's own test:
    a conn stub that raises on any raw .execute() call other than the one
    real read of existing metadata_json must never be hit for the write
    itself -- the write must go through update_umr_task()."""
    triage = _load("triage_write1", TRIAGE_PATH)
    calls = []

    class FakeCursor:
        def fetchone(self):
            return {"metadata_json": "{}"}

    class FakeConn:
        def execute(self, sql, params=None):
            calls.append(sql)
            if sql.strip().upper().startswith("SELECT"):
                return FakeCursor()
            raise AssertionError(f"raw write attempted: {sql}")

    calls_to_update = []
    real_update = triage._sbr.update_umr_task

    def spy_update(conn, umr_id, **fields):
        calls_to_update.append((umr_id, fields))

    triage._sbr.update_umr_task = spy_update
    try:
        evidence = {"umr_id": "UMR-write-test"}
        triage.apply_classification(FakeConn(), evidence, "retryable", "test reason")
    finally:
        triage._sbr.update_umr_task = real_update

    assert len(calls_to_update) == 1
    umr_id, fields = calls_to_update[0]
    assert umr_id == "UMR-write-test"
    assert "metadata" in fields
    assert fields["metadata"][f"triage_{triage.THIS_UMR}"]["bucket"] == "retryable"


def test_apply_classification_preserves_existing_metadata_keys():
    triage = _load("triage_write2", TRIAGE_PATH)

    class FakeCursor:
        def fetchone(self):
            return {"metadata_json": json.dumps({"pre_existing_key": "must_survive"})}

    class FakeConn:
        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("SELECT"):
                return FakeCursor()
            raise AssertionError("raw write attempted")

    captured = {}

    def spy_update(conn, umr_id, **fields):
        captured.update(fields)

    real_update = triage._sbr.update_umr_task
    triage._sbr.update_umr_task = spy_update
    try:
        triage.apply_classification(FakeConn(), {"umr_id": "UMR-x"}, "blocked", "reason")
    finally:
        triage._sbr.update_umr_task = real_update

    assert captured["metadata"]["pre_existing_key"] == "must_survive"
    assert f"triage_{triage.THIS_UMR}" in captured["metadata"]


def test_load_rows_only_returns_failed_and_killed_owner_dispatch_gateway_rows():
    triage = _load("triage_load1", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        _insert_row(conn, umr_id="UMR-f", status="failed", source_trigger="owner_dispatch_gateway")
        _insert_row(conn, umr_id="UMR-k", status="killed", source_trigger="owner_dispatch_gateway")
        _insert_row(conn, umr_id="UMR-c", status="completed", source_trigger="owner_dispatch_gateway")
        _insert_row(conn, umr_id="UMR-other-trigger", status="failed", source_trigger="something_else")
        conn.commit()
        rows = triage.load_rows(conn)
        ids = {r["umr_id"] for r in rows}
        assert ids == {"UMR-f", "UMR-k"}
    finally:
        conn.close()
        os.unlink(path)


def test_load_rows_respects_umr_id_filter():
    triage = _load("triage_load2", TRIAGE_PATH)
    sbr, conn, path = _scratch_conn()
    try:
        _insert_row(conn, umr_id="UMR-f", status="failed", source_trigger="owner_dispatch_gateway")
        _insert_row(conn, umr_id="UMR-k", status="killed", source_trigger="owner_dispatch_gateway")
        conn.commit()
        rows = triage.load_rows(conn, umr_id="UMR-f")
        assert len(rows) == 1
        assert rows[0]["umr_id"] == "UMR-f"
    finally:
        conn.close()
        os.unlink(path)


# --------------------------- helper function tests ---------------------------

def test_task_dir_from_unit():
    triage = _load("triage_helpers1", TRIAGE_PATH)
    assert triage.task_dir_from_unit("veridian-worker@task-20260805-000000-x.service") == "task-20260805-000000-x"
    assert triage.task_dir_from_unit(None) is None
    assert triage.task_dir_from_unit("not-a-unit-name") is None


def test_local_repo_path_whitelist_only():
    triage = _load("triage_helpers2", TRIAGE_PATH)
    assert triage.local_repo_path("veridian-scripts") == triage.SCRIPT_DIR
    assert triage.local_repo_path("some-random-repo") is None


def test_is_commit_on_main_returns_none_for_unresolvable_repo():
    triage = _load("triage_helpers3", TRIAGE_PATH)
    result = triage.is_commit_on_main("unknown-repo", "abc123", repo_path_resolver=lambda repo: None)
    assert result is None


def test_is_commit_on_main_false_when_merge_base_fails():
    triage = _load("triage_helpers4", TRIAGE_PATH)

    def fake_git(cmd, cwd=None, timeout=30):
        if cmd[:2] == ["git", "merge-base"]:
            return 1, "", "not an ancestor"
        return 0, "", ""

    result = triage.is_commit_on_main(
        "veridian-scripts", "abc123", git_runner=fake_git, repo_path_resolver=lambda repo: "/fake/path"
    )
    assert result is False


def _load_triage_with_scratch_db(db_path):
    """Like _seed_and_load_sbr, but loads triage_owner_umr_24h.py itself
    fresh so its internal `_sbr` submodule (loaded via importlib inside
    triage_owner_umr_24h.py, not shared with this test file's own `_sbr`)
    binds its DB_PATH to the scratch DB too."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bootstrap = _load("sbr_bootstrap_triage_main", SBR_PATH)
    bootstrap._ensure_umr_table(conn)
    conn.commit()
    conn.close()
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    try:
        return _load("triage_main_scratch", TRIAGE_PATH)
    finally:
        del os.environ["SUPERBOSS_REGISTER_DB"]


def test_main_apply_file_proposals_releases_write_lock_before_filing_proposals():
    """Regression test for the real deadlock found by independent review on
    this PR: main() used to hold _write_lock() across the entire per-row
    loop, including file_proposal()'s subprocess call -- which itself
    acquires that SAME flock in a child process (cmd_insert_owner_proposal's
    own `with _write_lock():`), guaranteeing a self-deadlock (broken only by
    the subprocess timeout) on every real `--apply --file-proposals` run.
    This test proves the fix: the write lock must be fully released (its
    context manager exited) before any file_proposal() call is made."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    triage = _load_triage_with_scratch_db(tmp.name)
    try:
        conn = triage._sbr._connect()
        _insert_row(conn, umr_id="UMR-deadlock-test", status="failed",
                    source_trigger="owner_dispatch_gateway",
                    reason="real mechanical merge conflict, no remaining blocker")
        conn.commit()
        conn.close()

        lock_state = {"held": False, "max_concurrent_holds": 0}
        call_order = []

        import contextlib

        @contextlib.contextmanager
        def fake_write_lock():
            assert not lock_state["held"], "write lock acquired while already held -- would deadlock for real"
            lock_state["held"] = True
            call_order.append("lock_acquired")
            try:
                yield
            finally:
                lock_state["held"] = False
                call_order.append("lock_released")

        def fake_file_proposal(evidence, bucket, reason):
            # The real bug: this used to be called while main()'s own
            # _write_lock() was still held, which would deadlock against
            # insert-owner-proposal's own internal _write_lock() acquisition
            # in a real run. Here we just assert the lock is NOT held.
            assert not lock_state["held"], "file_proposal() called while write lock still held -- this is the real deadlock bug"
            call_order.append("file_proposal_called")
            return {"id": 1, "child_umr": "UMR-fake-child"}

        triage._sbr._write_lock = fake_write_lock
        triage.file_proposal = fake_file_proposal

        import sys as _sys
        old_argv = _sys.argv
        _sys.argv = ["triage_owner_umr_24h.py", "--apply", "--file-proposals"]
        try:
            triage.main()
        finally:
            _sys.argv = old_argv

        assert "lock_acquired" in call_order
        assert "lock_released" in call_order
        assert call_order.index("lock_released") < call_order.index("file_proposal_called"), (
            "write lock must be released before file_proposal() is ever called"
        )
    finally:
        os.unlink(tmp.name)


def test_bucket_sum_equals_total_triaged_property():
    """Property test: for any classify() result, the bucket must be one of
    exactly the 4 declared buckets -- guards against a silent 5th bucket
    being introduced."""
    triage = _load("triage_helpers5", TRIAGE_PATH)
    sample_evidences = [
        _base_evidence(),
        _base_evidence(merge_commit_sha="a", merge_commit_is_ancestor_of_main=True),
        _base_evidence(superseding_umr="UMR-x"),
        _base_evidence(credit_accountant_rejected=True),
        _base_evidence(missing_reviewer_identity=True),
        _base_evidence(owner_decision_required=True),
        _base_evidence(merge_conflict=True),
        _base_evidence(quality_gate_failed=True),
        _base_evidence(no_task_yaml_ever_written=True),
    ]
    for ev in sample_evidences:
        bucket, _ = triage.classify(ev)
        assert bucket in triage.BUCKETS
