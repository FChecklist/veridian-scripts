#!/usr/bin/env python3
"""
test_verify_real_completion_evidence.py -- regression test for
UMR-20260814-181115's real fix: agent_work_briefing.py's record-completion
must independently verify real PR/merge state before accepting a
status=completed/completed_unmerged self-report, instead of trusting it at
face value.

Real, repeated finding this session (see this task's own SPEC): several
register rows recorded misleading statuses, independently disproven by
direct checking -- among them, a self-report citing a real PR whose entire
diff was docs-only (progress-notes only, no real code), and a self-report
citing zero PR and zero files at all. Both were previously accepted as
status=completed with no independent check whatsoever.

Two fixture cases matching those real incidents, both asserted to get
downgraded (never accepted as completed):
  1. FixtureDocsOnlyPr: --umr-status completed --umr-pr-number N
     --umr-repo veridian-scripts, where PR #N's real (faked-via-gh-mock)
     diff is 100% *.md files.
  2. FixtureZeroEvidence: --umr-status completed, no --umr-pr-number, no
     --files-touched at all.

Never calls the real `gh` CLI -- resource_governor.py's own `_run()`
subprocess wrapper is replaced with a deterministic fake, same convention
test_find_real_pr_across_repos.py already established for testing PR-facing
code in this repo without network access. Runs record_completion() directly
(not via CLI subprocess) against a real throwaway temp sqlite DB, same
SUPERBOSS_REGISTER_DB / stub-table-bootstrap convention
test_agent_work_briefing.py already uses.

Run: python3 test_verify_real_completion_evidence.py
"""
import importlib.util as _ilu
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEFING = os.path.join(HERE, "agent_work_briefing.py")
SUPERBOSS = os.path.join(HERE, "superboss-register.py")


def _bootstrap_umr_tasks(db_path, umr_id, status="running"):
    """Same real, full live umr_tasks column set test_agent_work_briefing.py
    already uses -- _ensure_umr_table()'s fast path short-circuits (skips
    every further migration) once these columns are present, and a real row
    must already exist for mark-umr-terminal to have anything to UPDATE."""
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE umr_tasks (
        umr_id TEXT PRIMARY KEY,
        task_identity TEXT NOT NULL,
        ts_submitted TEXT NOT NULL,
        tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 4),
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','dispatched','running','completed','completed_unmerged','failed','rejected_duplicate','sigterm_sent','killed')),
        source_trigger TEXT NOT NULL,
        task_kind TEXT NOT NULL DEFAULT 'systemctl_action',
        unit_name TEXT,
        inputs_json TEXT NOT NULL DEFAULT '{}',
        outputs_json TEXT NOT NULL DEFAULT '{}',
        logs_ref TEXT,
        metric_snapshot_json TEXT,
        ts_dispatched TEXT,
        ts_sigterm TEXT,
        ts_completed TEXT,
        reason TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        last_heartbeat TEXT, tenant_id TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_content TEXT, utm_term TEXT, external_agent_eligible INTEGER NOT NULL DEFAULT 0,
        external_agent_task_type TEXT, blast_radius TEXT, requires_multi_file_context INTEGER NOT NULL DEFAULT 0,
        files_touched TEXT NOT NULL DEFAULT '[]', external_agent_status TEXT,
        external_agent_reject_count INTEGER NOT NULL DEFAULT 0, external_agent_dispatch_count INTEGER NOT NULL DEFAULT 0,
        ts_relay_attempted TEXT, relay_outcome TEXT, relay_detail TEXT
    )""")
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger) "
        "VALUES (?, 'test-task-identity', '2026-08-14T00:00:00Z', 1, ?, 'test')",
        (umr_id, status),
    )
    conn.commit()
    conn.close()


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class VerifyRealCompletionEvidenceUnitTests(unittest.TestCase):
    """Unit-level tests against verify_real_completion_evidence() directly
    -- no DB, no subprocess -- for the core decision logic, plus the two
    real-incident fixtures at the record_completion() integration level
    below."""

    @classmethod
    def setUpClass(cls):
        spec = _ilu.spec_from_file_location("verify_real_completion_evidence_test_mod", BRIEFING)
        cls.mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def setUp(self):
        self._orig_run = self.mod.resource_governor._run

    def tearDown(self):
        self.mod.resource_governor._run = self._orig_run

    def test_non_completed_status_always_passes_unchecked(self):
        result = self.mod.verify_real_completion_evidence("failed", None, None, None)
        self.assertTrue(result["verified"])
        self.assertFalse(result["checked"])

    def test_docs_only_pr_is_refused(self):
        self.mod.resource_governor._run = lambda cmd, **kw: _FakeCompletedProcess(
            0, json.dumps({"state": "MERGED", "mergedAt": "2026-08-14T00:00:00Z",
                           "files": [{"path": "progress/foo.md"}, {"path": "PROGRESS.md"}]}))
        result = self.mod.verify_real_completion_evidence("completed", 999, "veridian-scripts", None)
        self.assertFalse(result["verified"])
        self.assertIn("docs-only", result["reason"])

    def test_pr_with_real_code_file_is_verified(self):
        self.mod.resource_governor._run = lambda cmd, **kw: _FakeCompletedProcess(
            0, json.dumps({"state": "MERGED", "mergedAt": "2026-08-14T00:00:00Z",
                           "files": [{"path": "progress/foo.md"}, {"path": "agent_work_briefing.py"}]}))
        result = self.mod.verify_real_completion_evidence("completed", 1000, "veridian-scripts", None)
        self.assertTrue(result["verified"])

    def test_gh_error_is_refused_not_silently_accepted(self):
        self.mod.resource_governor._run = lambda cmd, **kw: _FakeCompletedProcess(1, "", "gh: PR not found")
        result = self.mod.verify_real_completion_evidence("completed", 1234, "veridian-scripts", None)
        self.assertFalse(result["verified"])
        self.assertIn("could not independently verify", result["reason"])

    def test_zero_pr_zero_files_is_refused(self):
        result = self.mod.verify_real_completion_evidence("completed", None, None, None)
        self.assertFalse(result["verified"])

    def test_zero_pr_but_real_files_touched_is_verified(self):
        result = self.mod.verify_real_completion_evidence(
            "completed", None, None, ["resource_governor.py"])
        self.assertTrue(result["verified"])

    def test_pr_cited_without_repo_is_refused(self):
        result = self.mod.verify_real_completion_evidence("completed", 42, None, None)
        self.assertFalse(result["verified"])
        self.assertIn("no --umr-repo given", result["reason"])


class RecordCompletionRealIncidentFixturesTest(unittest.TestCase):
    """Integration-level: the exact two real-incident fixtures the SPEC
    names, run through record_completion() itself against a real temp DB --
    both must be downgraded (status=unverified_self_report), never accepted
    as status=completed, and umr_tasks must NOT actually be flipped to
    'completed' in the database."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="verify_real_completion_evidence_test_")
        self.db_path = os.path.join(self.tmpdir, "test.sqlite")
        self.umr_id = "UMR-20260814-181115-test1"
        _bootstrap_umr_tasks(self.db_path, self.umr_id, status="running")

        self._orig_env = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = self.db_path

        # cmd_mark_umr_terminal's own init_db_silent() needs the full real
        # schema (e.g. the 'instructions' table) to exist, not just the
        # umr_tasks stub above -- same real `init` CLI call
        # test_agent_work_briefing.py already runs before exercising
        # record-completion.
        proc = subprocess.run(["python3", SUPERBOSS, "init"], capture_output=True, text=True,
                               env=dict(os.environ))
        self.assertEqual(proc.returncode, 0, f"db init failed: {proc.stderr}")

        spec = _ilu.spec_from_file_location(
            f"record_completion_fixture_test_mod_{id(self)}", BRIEFING)
        self.mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        sbr = self.mod._superboss_register()
        self.assertEqual(os.path.realpath(sbr.DB_PATH), os.path.realpath(self.db_path),
                          "test safety check: must be pointed at the temp db, never the live one")

        air = self.mod._ai_agent_registry()
        air.AGENT_MEMORY_DIR = os.path.join(self.tmpdir, "agents")

        self._orig_run = self.mod.resource_governor._run

    def tearDown(self):
        self.mod.resource_governor._run = self._orig_run
        if self._orig_env is None:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        else:
            os.environ["SUPERBOSS_REGISTER_DB"] = self._orig_env

    def _real_status_in_db(self):
        sbr = self.mod._superboss_register()
        conn = sbr._connect()
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (self.umr_id,)).fetchone()
        conn.close()
        return row["status"] if row else None

    def test_fixture_docs_only_pr_self_report_is_downgraded(self):
        """Real incident #1: a self-report citing only a docs-only PR."""
        self.mod.resource_governor._run = lambda cmd, **kw: _FakeCompletedProcess(
            0, json.dumps({"state": "MERGED", "mergedAt": "2026-08-14T09:00:00Z",
                           "files": [{"path": "progress/task-fake.md"}, {"path": "PROGRESS.md"}]}))

        result = self.mod.record_completion(
            self.umr_id, "finished the work, see PR #4242", "test agent",
            "completed", "claims completion", None, None, None, None, None, None, None, None, None,
            umr_pr_number=4242, umr_repo="veridian-scripts",
        )

        self.assertFalse(result["completion_verification"]["verified"])
        self.assertEqual(result["umr_tasks"]["status"], "unverified_self_report")
        self.assertEqual(result["umr_tasks"]["written"], False)
        self.assertEqual(result["umr_tasks"]["claimed_status"], "completed")
        self.assertNotEqual(self._real_status_in_db(), "completed",
                             "docs-only-PR self-report must never actually flip umr_tasks to completed")

    def test_fixture_zero_pr_zero_files_self_report_is_downgraded(self):
        """Real incident #2: a self-report citing zero PR or files."""
        result = self.mod.record_completion(
            self.umr_id, "done, everything works now", "test agent",
            "completed", "claims completion", None, None, None, None, None, None, None, None, None,
        )

        self.assertFalse(result["completion_verification"]["verified"])
        self.assertEqual(result["umr_tasks"]["status"], "unverified_self_report")
        self.assertEqual(result["umr_tasks"]["written"], False)
        self.assertEqual(result["umr_tasks"]["claimed_status"], "completed")
        self.assertNotEqual(self._real_status_in_db(), "completed",
                             "zero-evidence self-report must never actually flip umr_tasks to completed")

    def test_real_non_docs_pr_with_files_touched_is_still_gated_by_existing_evidence_rule(self):
        """Sanity check this new gate does not itself weaken the pre-existing
        commit/file-path evidence gate in superboss-register.py: a
        genuinely non-docs-only PR still needs a real --umr-commit-sha or
        --umr-file-path to actually be written (that older gate is
        untouched by this fix)."""
        self.mod.resource_governor._run = lambda cmd, **kw: _FakeCompletedProcess(
            0, json.dumps({"state": "MERGED", "mergedAt": "2026-08-14T09:00:00Z",
                           "files": [{"path": "agent_work_briefing.py"}]}))

        result = self.mod.record_completion(
            self.umr_id, "finished the work, see PR #4243", "test agent",
            "completed", "claims completion", None, None, None, None, None, None, None, None, None,
            umr_pr_number=4243, umr_repo="veridian-scripts",
        )

        self.assertTrue(result["completion_verification"]["verified"],
                         "a real non-docs-only PR must pass THIS new independent check")
        self.assertTrue(result["umr_tasks"].get("refused"),
                         "but the pre-existing commit/file-path evidence gate must still refuse it "
                         "since no real --umr-commit-sha/--umr-file-path was supplied")


if __name__ == "__main__":
    unittest.main()
