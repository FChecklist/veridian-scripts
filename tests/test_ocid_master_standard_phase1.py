#!/usr/bin/env python3
"""Real tests for OCID Master Standard v6 Phase 1 (UMR-20260805-042152-e559,
Owner directive; parent references UMR-20260804-170055-a069, canonical
OCID-068 UMR, real status completed, and UMR-20260805-032731-b412, OCID-068
permanent closure record, real status completed, PR #52 merge commit
c46da9b777e2a8a60e15230dacd72f2329e885af). Every test uses a real, isolated,
temp-file SQLite database seeded with the real schema -- never the live
production database -- same convention as tests/test_ocid_canonical_registry.py.

Covers the three real Phase 1 functions plus the minimal real append-only
audit log:
  - resolve_ocid_canonical(): multi-UMR-found (report all + canonical
    choice), honest not-found reporting
  - reconcile_umr_status_against_pr(): stale-status detection + proposed
    correction (with real audit event), non-stale-status no-op
  - refuse_certification_if_merged_without_required_checks(): refusal
    against PR #932/#933's real historical facts (failing check + 0
    reviews), plus a certification-allowed case
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_ocid_master_standard_phase1", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_master_standard_audit_log_table(conn)
    conn.close()
    return sbr


def _insert_umr_task(conn, umr_id, task_identity, status, ts_completed=None, tier=1):
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, ts_completed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (umr_id, task_identity, "2026-08-04T00:00:00+00:00", tier, status, "owner", ts_completed),
    )


class _FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_gh_pr_list_runner(prs_by_repo_and_ocid, git_log_by_repo=None):
    """Fake _runner: dispatches on whether the cmd is a `gh pr list` or a
    `git log` invocation, returning canned real-shaped output -- never
    spawns a real subprocess."""
    git_log_by_repo = git_log_by_repo or {}

    def _runner(cmd, cwd=None):
        if cmd[:3] == ["gh", "pr", "list"]:
            repo = cmd[cmd.index("--repo") + 1].split("/", 1)[1]
            search = cmd[cmd.index("--search") + 1]
            ocid = search.split(" ")[0]
            prs = prs_by_repo_and_ocid.get((repo, ocid), [])
            return _FakeCompletedProcess(stdout=json.dumps(prs), returncode=0)
        if cmd[:2] == ["git", "log"]:
            repo = os.path.basename(cwd) if cwd else None
            lines = git_log_by_repo.get(repo, [])
            return _FakeCompletedProcess(stdout="\n".join(lines), returncode=0)
        if cmd[:1] == ["grep"]:
            return _FakeCompletedProcess(stdout="", returncode=1)  # honest zero matches
        raise AssertionError(f"unexpected real subprocess call in test: {cmd}")

    return _runner


# ---------------------------------------------------------------------------
# resolve_ocid_canonical()
# ---------------------------------------------------------------------------

def test_resolve_ocid_canonical_multi_umr_found_reports_all_with_canonical_choice():
    """Real requirement: if more than one distinct UMR ID is found for the
    same OCID, return ALL of them plus an explicit canonical_umr_id choice
    and a duplicate_reason -- never silently pick one."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        # (a)/(b): one real UMR findable directly in umr_tasks
        _insert_umr_task(conn, "UMR-20260804-090000-aaaa", "owner-task-ocid-038-real-work", "completed",
                          ts_completed="2026-08-04T10:00:00+00:00")
        conn.commit()

        # (c): gh pr search surfaces a SECOND, distinct UMR ID in a PR body
        runner = _make_gh_pr_list_runner({
            ("compliance-tracker", "OCID-038"): [
                {"number": 886, "title": "OCID-038 real gap closure", "state": "MERGED",
                 "body": "Closes OCID-038. Dispatch instruction: UMR-20260804-080000-bbbb.",
                 "mergedAt": "2026-08-04T09:00:00Z"},
            ],
            ("veridian-scripts", "OCID-038"): [],
            ("projexa", "OCID-038"): [],
        })

        result = sbr.resolve_ocid_canonical("OCID-038", conn, _runner=runner)
        conn.close()

        assert result["not_found"] is False
        assert len(result["all_umr_ids"]) == 2
        assert "UMR-20260804-090000-aaaa" in result["all_umr_ids"]
        assert "UMR-20260804-080000-bbbb" in result["all_umr_ids"]
        # chronologically-earliest by UMR-ID timestamp ordering
        assert result["canonical_umr_id"] == "UMR-20260804-080000-bbbb"
        assert result["status"] == "multiple_umr_ids_found_needs_review"
        assert result["duplicate_reason"] is not None
        assert "2 distinct real UMR IDs" in result["duplicate_reason"]
        print("PASS: test_resolve_ocid_canonical_multi_umr_found_reports_all_with_canonical_choice")


def test_resolve_ocid_canonical_not_found_honest_reporting():
    """Real requirement: if truly nothing is found after all methods,
    return not_found=True with per-method evidence of the empty search,
    never leave fields blank or guess."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        runner = _make_gh_pr_list_runner({})  # zero PRs everywhere, zero git log lines

        result = sbr.resolve_ocid_canonical("OCID-999-NEVER-DISPATCHED", conn, _runner=runner)
        conn.close()

        assert result["not_found"] is True
        assert result["canonical_umr_id"] is None
        assert result["all_umr_ids"] == []
        assert result["duplicate_reason"] is None
        # every real method's evidence is present and honest, never blank/omitted
        assert result["evidence"]["umr_tasks_task_identity_substring"] == "zero rows"
        assert result["evidence"]["umr_tasks_full_dump_grep"] == "zero rows"
        for repo in ("compliance-tracker", "veridian-scripts", "projexa"):
            assert result["evidence"][f"gh_pr_search_{repo}"]["ok"] is True
            assert result["evidence"][f"gh_pr_search_{repo}"]["prs"] == []
        assert result["evidence"]["umr_ids_extracted_from_pr_bodies"] == "zero matches"
        # (f) is the real last resort, only consulted because (a)-(e) found nothing
        assert result["evidence"]["master_tracker_and_active_claims_grep"] != (
            "skipped -- real last resort only, methods (a)-(e) already found a real match"
        )
        print("PASS: test_resolve_ocid_canonical_not_found_honest_reporting")


def test_resolve_ocid_canonical_single_match_skips_last_resort():
    """Real requirement: method (f) is only a last resort -- it must be
    skipped, not silently run anyway, once (a)-(e) already found a real
    match."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _insert_umr_task(conn, "UMR-20260804-090000-cccc", "owner-task-ocid-001-real-work", "completed")
        conn.commit()

        def _runner(cmd, cwd=None):
            if cmd[:1] == ["grep"]:
                raise AssertionError("method (f) must be skipped when (a)-(e) already found a match")
            if cmd[:3] == ["gh", "pr", "list"]:
                return _FakeCompletedProcess(stdout="[]", returncode=0)
            if cmd[:2] == ["git", "log"]:
                return _FakeCompletedProcess(stdout="", returncode=0)
            raise AssertionError(f"unexpected call: {cmd}")

        result = sbr.resolve_ocid_canonical("OCID-001", conn, _runner=_runner)
        conn.close()
        assert result["canonical_umr_id"] == "UMR-20260804-090000-cccc"
        assert result["evidence"]["master_tracker_and_active_claims_grep"] == (
            "skipped -- real last resort only, methods (a)-(e) already found a real match"
        )
        print("PASS: test_resolve_ocid_canonical_single_match_skips_last_resort")


# ---------------------------------------------------------------------------
# reconcile_umr_status_against_pr()
# ---------------------------------------------------------------------------

def test_reconcile_umr_status_detects_stale_status_and_proposes_correction():
    """Real requirement: directly targets the exact real bug class just
    found and fixed for UMR-20260805-032731-b412 -- a real UMR stuck at
    'running'/ts_completed null despite a real linked PR being confirmed
    merged. Must NOT silently auto-apply -- only report the proposal, plus
    record a real 'status_reconciliation' audit event."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _insert_umr_task(conn, "UMR-20260804-170055-a069", "owner-task-ocid-068-guardrails", "running")
        conn.commit()

        pr_evidence = [
            {"repo": "veridian-scripts", "number": 52, "state": "MERGED",
             "mergedAt": "2026-08-05T03:54:10Z", "title": "docs: OCID-068 permanent closure record"},
        ]

        result = sbr.reconcile_umr_status_against_pr(conn, "UMR-20260804-170055-a069", pr_evidence=pr_evidence)

        assert result["is_stale"] is True
        assert result["current_status"] == "running"
        assert result["proposed_status"] == "completed"
        assert result["proposed_ts_completed"] == "2026-08-05T03:54:10Z"
        assert result["evidence"]["completing_pr"]["number"] == 52

        # real, permanent audit trail -- not just a transient return value
        audit_rows = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE umr_id=? AND event_type='status_reconciliation'",
            ("UMR-20260804-170055-a069",),
        ).fetchall()
        assert len(audit_rows) == 1
        detail = json.loads(audit_rows[0]["detail_json"])
        assert detail["proposed_status"] == "completed"

        # not auto-applied -- the real umr_tasks row is unchanged until the
        # caller explicitly calls update_umr_task() itself
        still_row = conn.execute(
            "SELECT status FROM umr_tasks WHERE umr_id=?", ("UMR-20260804-170055-a069",)
        ).fetchone()
        assert still_row["status"] == "running"
        conn.close()
        print("PASS: test_reconcile_umr_status_detects_stale_status_and_proposes_correction")


def test_reconcile_umr_status_non_stale_is_a_real_no_op():
    """Real requirement: a UMR whose status is already correct (e.g.
    'completed' with a real matching merged PR) must report is_stale=False
    and must NOT write an audit event."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _insert_umr_task(conn, "UMR-20260804-170055-a069", "owner-task-ocid-068-guardrails",
                          "completed", ts_completed="2026-08-05T02:45:07Z")
        conn.commit()

        pr_evidence = [
            {"repo": "veridian-scripts", "number": 52, "state": "MERGED",
             "mergedAt": "2026-08-05T03:54:10Z"},
        ]
        result = sbr.reconcile_umr_status_against_pr(conn, "UMR-20260804-170055-a069", pr_evidence=pr_evidence)

        assert result["is_stale"] is False
        assert result["current_status"] == "completed"
        assert result["proposed_status"] == "completed"  # unchanged, echoed back
        assert result["proposed_ts_completed"] == "2026-08-05T02:45:07Z"  # original, untouched

        audit_rows = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE umr_id=?",
            ("UMR-20260804-170055-a069",),
        ).fetchall()
        assert len(audit_rows) == 0
        conn.close()
        print("PASS: test_reconcile_umr_status_non_stale_is_a_real_no_op")


def test_reconcile_umr_status_no_merged_pr_evidence_is_a_real_no_op():
    """A queued/running UMR with NO real merged-PR evidence at all must not
    be flagged stale -- absence of evidence is not evidence of staleness."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _insert_umr_task(conn, "UMR-20260805-000000-dddd", "owner-task-genuinely-in-progress", "running")
        conn.commit()

        result = sbr.reconcile_umr_status_against_pr(conn, "UMR-20260805-000000-dddd", pr_evidence=[])
        conn.close()
        assert result["is_stale"] is False
        assert result["current_status"] == "running"
        print("PASS: test_reconcile_umr_status_no_merged_pr_evidence_is_a_real_no_op")


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_certification_phase1", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


# ---------------------------------------------------------------------------
# refuse_certification_if_merged_without_required_checks()
# ---------------------------------------------------------------------------

def test_certification_refused_for_pr_932_real_historical_facts():
    """Real test case required by UMR-20260805-042152-e559: refuse
    certification against compliance-tracker PR #932's real historical
    facts (merged with a failing 'Metadata Index Coverage Check' and zero
    real reviews), even though GitHub itself let it merge."""
    sbr = _load_sbr()
    pr_932 = {
        "repo": "compliance-tracker", "pr_number": 932,
        "merged_at": "2026-08-05T03:20:24Z",
        "required_status_checks": [
            {"name": "Metadata Index Coverage Check", "conclusion": "failure"},
        ],
        "approving_reviews_count": 0,
        "required_approving_review_count": 1,
    }
    verdict, reason = sbr.refuse_certification_if_merged_without_required_checks(pr_932)
    assert verdict is False
    assert "Metadata Index Coverage Check" in reason
    assert "approving review count (0)" in reason
    print("PASS: test_certification_refused_for_pr_932_real_historical_facts")


def test_certification_refused_for_pr_933_real_historical_facts():
    """Same real incident, PR #933 -- 'the same' per the Owner directive:
    merged with a failing required status check and zero real reviews."""
    sbr = _load_sbr()
    pr_933 = {
        "repo": "compliance-tracker", "pr_number": 933,
        "merged_at": "2026-08-05T03:24:31Z",
        "required_status_checks": [
            {"name": "Metadata Index Coverage Check", "conclusion": "failure"},
        ],
        "approving_reviews_count": 0,
        "required_approving_review_count": 1,
    }
    verdict, reason = sbr.refuse_certification_if_merged_without_required_checks(pr_933)
    assert verdict is False
    assert "compliance-tracker#933" in reason
    print("PASS: test_certification_refused_for_pr_933_real_historical_facts")


def test_certification_allowed_when_checks_pass_and_reviews_met():
    """Proves the function is not just always-refuse: a real PR with all
    required status checks passing and the required approving review count
    met must be certified (verdict True)."""
    sbr = _load_sbr()
    pr_934 = {
        "repo": "compliance-tracker", "pr_number": 934,
        "merged_at": "2026-08-05T03:45:00Z",
        "required_status_checks": [
            {"name": "Metadata Index Coverage Check", "conclusion": "success"},
            {"name": "Lint", "conclusion": "success"},
        ],
        "approving_reviews_count": 1,
        "required_approving_review_count": 1,
    }
    verdict, reason = sbr.refuse_certification_if_merged_without_required_checks(pr_934)
    assert verdict is True
    assert "Certification allowed" in reason
    print("PASS: test_certification_allowed_when_checks_pass_and_reviews_met")


def test_certification_refused_only_for_review_count_when_checks_pass():
    """Isolates the review-count condition: checks passing but reviews below
    the required count must still refuse."""
    sbr = _load_sbr()
    record = {
        "repo": "veridian-scripts", "pr_number": 60,
        "merged_at": "2026-08-05T04:00:00Z",
        "required_status_checks": [{"name": "CI", "conclusion": "success"}],
        "approving_reviews_count": 0,
        "required_approving_review_count": 1,
    }
    verdict, reason = sbr.refuse_certification_if_merged_without_required_checks(record)
    assert verdict is False
    assert "approving review count (0) below required (1)" in reason
    print("PASS: test_certification_refused_only_for_review_count_when_checks_pass")


def test_apply_certification_verdict_records_real_audit_event_on_refusal():
    """The caller-side usage pattern: apply_certification_verdict() must
    record a real, permanent 'certification_refused' audit event when the
    pure function refuses, and must NOT record one when it allows."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        pr_932 = {
            "repo": "compliance-tracker", "pr_number": 932,
            "merged_at": "2026-08-05T03:20:24Z",
            "required_status_checks": [{"name": "Metadata Index Coverage Check", "conclusion": "failure"}],
            "approving_reviews_count": 0,
            "required_approving_review_count": 1,
        }
        verdict, reason = sbr.apply_certification_verdict(conn, pr_932)
        assert verdict is False
        rows = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE event_type='certification_refused'"
        ).fetchall()
        assert len(rows) == 1
        detail = json.loads(rows[0]["detail_json"])
        assert detail["pr_number"] == 932

        pr_ok = {
            "repo": "veridian-scripts", "pr_number": 61,
            "merged_at": "2026-08-05T04:10:00Z",
            "required_status_checks": [{"name": "CI", "conclusion": "success"}],
            "approving_reviews_count": 1,
            "required_approving_review_count": 1,
        }
        verdict2, _ = sbr.apply_certification_verdict(conn, pr_ok)
        assert verdict2 is True
        rows2 = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE event_type='certification_refused'"
        ).fetchall()
        assert len(rows2) == 1  # unchanged -- no new refusal event for an allowed verdict
        conn.close()
        print("PASS: test_apply_certification_verdict_records_real_audit_event_on_refusal")


if __name__ == "__main__":
    test_resolve_ocid_canonical_multi_umr_found_reports_all_with_canonical_choice()
    test_resolve_ocid_canonical_not_found_honest_reporting()
    test_resolve_ocid_canonical_single_match_skips_last_resort()
    test_reconcile_umr_status_detects_stale_status_and_proposes_correction()
    test_reconcile_umr_status_non_stale_is_a_real_no_op()
    test_reconcile_umr_status_no_merged_pr_evidence_is_a_real_no_op()
    test_certification_refused_for_pr_932_real_historical_facts()
    test_certification_refused_for_pr_933_real_historical_facts()
    test_certification_allowed_when_checks_pass_and_reviews_met()
    test_certification_refused_only_for_review_count_when_checks_pass()
    test_apply_certification_verdict_records_real_audit_event_on_refusal()
    print("ALL PASS")
