#!/usr/bin/env python3
"""Real tests for progress_completion_gate.py (UMR-20260813-195922-f548).

Proves the two real claims the fix depends on:

  (a) two simulated workers recording progress concurrently, each on their
      own branch, produce a NON-CONFLICTING diff when merged -- because
      each writes its own progress/<task_id>.md rather than a shared
      PROGRESS.md. This is a real `git merge` against a real temp
      repository, not a mocked assertion.
  (b) a doc-only diff for a code-named objective is REJECTED by
      check_completion() / the check-completion CLI, with a real non-zero
      exit code -- and a diff that DOES touch the named file is accepted.

Every test uses a real, isolated, temp-dir git repository -- never the live
production workspace, same convention as this repo's other git-backed
tests.
"""
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import progress_completion_gate as gate  # noqa: E402


def run(cwd, *args):
    res = subprocess.run(
        ["git", "-C", cwd, *args], capture_output=True, text=True
    )
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res.stdout


def init_repo(path):
    os.makedirs(path, exist_ok=True)
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.email", "test@example.com")
    run(path, "config", "user.name", "Test")


class TestExtractNamedCodeFiles(unittest.TestCase):
    def test_finds_real_code_file_in_prose(self):
        text = "Unwedge dispatch: swap gate vetoes on STATIC occupancy (dispatch_core.py, in the swap gate)."
        self.assertEqual(gate.extract_named_code_files(text), ["dispatch_core.py"])

    def test_finds_script_file(self):
        text = "Third attempt: pm-sentinel-tick.sh positional systemctl show parse"
        self.assertEqual(gate.extract_named_code_files(text), ["pm-sentinel-tick.sh"])

    def test_excludes_progress_and_rca_artifacts(self):
        text = "update PROGRESS.md after fixing dispatch_core.py, see RCA_20260813_foo.md"
        self.assertEqual(gate.extract_named_code_files(text), ["dispatch_core.py"])

    def test_no_code_file_named(self):
        text = "docs: re-verify UMR-20260813-155201-da76 self-block claim"
        self.assertEqual(gate.extract_named_code_files(text), [])

    def test_excludes_bare_boilerplate_tool_names(self):
        """Real fix (RCA of UMR-20260813-060311-6eea): pm-sentinel-tick.sh's
        own Check 2a RCA template cites resource_governor.py/
        superboss-register.py as CLI tools to run in essentially every
        killed-row RCA prompt it generates -- these bare mentions are never
        a real, distinguishing objective file."""
        text = (
            "REAL GAP FOUND: resource_governor.py --query-umr --umr-id "
            "UMR-X shows status=killed. This needs a real RCA: read the "
            "row's full real outputs_json/reason (query resource_governor.py "
            "--query-umr --umr-id UMR-X yourself first), determine the real "
            "root cause, and either fix + redispatch the real remaining "
            "scope, or record a real, honest terminal outcome via "
            "superboss-register.py mark-umr-terminal citing real evidence."
        )
        self.assertEqual(gate.extract_named_code_files(text), [])

    def test_path_prefixed_boilerplate_tool_name_still_counts(self):
        """A genuinely path-prefixed mention (e.g. naming the file's own
        code inside scripts/) is a real, distinguishing reference -- NOT
        excluded, unlike the bare-name boilerplate citation above."""
        text = "Fix the --query-umr filter in scripts/resource_governor.py"
        self.assertEqual(
            gate.extract_named_code_files(text), ["scripts/resource_governor.py"]
        )

    def test_excludes_bare_live_deploy_drift_tool_names(self):
        """Real fix (UMR-20260814-051532-2ae4): the recurring "reconcile
        live deploy drift" task's own dispatch prompt cites
        check_live_scripts_drift.py (the tool to run) and sync-repos.sh
        (the script whose dirty/non-main-skip behavior to read) by bare
        name in essentially every occurrence -- same boilerplate-citation
        shape as resource_governor.py/superboss-register.py above, not a
        real, distinguishing objective inside the task's own workspace."""
        text = (
            "REAL GAP FOUND: the real live checkout at /opt/veridian/scripts "
            "is NOT in sync with origin/main, verified live this tick via "
            "check_live_scripts_drift.py --live-dir /opt/veridian/scripts -- "
            "branch=fix/foo real HEAD=abc vs real origin/main=def, 3 real "
            "tracked file(s) differ: AGENTS.md, resource_governor.py, "
            "superboss-register.py. Determine why the live checkout is "
            "stuck off-main / dirty (sync-repos.sh deliberately refuses to "
            "force-overwrite a dirty or non-main checkout -- see its own "
            "comments)."
        )
        self.assertEqual(gate.extract_named_code_files(text), [])

    def test_excludes_evidence_list_only_filenames(self):
        """Real fix (UMR-20260814-051532-2ae4): a filename cited ONLY inside
        check_live_scripts_drift.py's own "N real tracked file(s) differ:"
        evidence list is not a real objective -- it's evidence of what
        differs between two git refs, quoted verbatim into the dispatch
        prompt, not an instruction to edit that file in this workspace."""
        text = (
            "verified live this tick via check_live_scripts_drift.py -- "
            "5 real tracked file(s) differ: AGENTS.md, resource_governor.py, "
            "superboss-register.py, tests/test_credit_accountant_report_approval.py, "
            "tests/test_perform_spawn_seeds_credit_accountant_plan.py. "
            "This is a confirmed root cause of prior false RCAs."
        )
        self.assertEqual(gate.extract_named_code_files(text), [])

    def test_evidence_list_filename_also_named_elsewhere_still_counts(self):
        """A filename cited in the evidence list AND named again elsewhere
        as a real, distinguishing objective is NOT excluded -- only a
        mention that appears exclusively inside the evidence list is."""
        text = (
            "5 real tracked file(s) differ: AGENTS.md, resource_governor.py, "
            "dispatch_core.py. The real fix belongs in dispatch_core.py's "
            "swap gate -- fix it there."
        )
        self.assertEqual(gate.extract_named_code_files(text), ["dispatch_core.py"])

    def test_excludes_quoted_reason_citation_filenames(self):
        """Real fix (UMR-20260814-080423-bd93): pm-sentinel-tick.sh's Check
        2a RCA template quotes the TARGET row's own live `reason` field
        verbatim ('real recorded reason: "..."'). A code filename that
        appears only inside that quoted historical citation (e.g. the
        script responsible for the ORIGINAL incident under investigation)
        is not this RCA task's own objective and must not be treated as
        one."""
        text = (
            "REAL GAP FOUND: resource_governor.py --query-umr --umr-id "
            "UMR-X shows status=killed, real recorded reason: \"already-live "
            "D-Bus StopUnit/KillUnit instrumentation "
            "(directive-engine-stop-audit-monitor.sh, systemd unit "
            "veridian-directive-engine-stop-audit.service)\" (unit_name=none). "
            "This needs a real RCA: determine the real root cause, and "
            "either fix + redispatch the real remaining scope, or record a "
            "real, honest terminal outcome via superboss-register.py "
            "mark-umr-terminal citing real evidence."
        )
        self.assertEqual(gate.extract_named_code_files(text), [])

    def test_reason_citation_filename_also_named_elsewhere_still_counts(self):
        """A filename cited inside a quoted reason AND named again elsewhere
        as a real, distinguishing objective is NOT excluded -- only a
        mention that appears exclusively inside the quoted citation is."""
        text = (
            "real recorded reason: \"directive-engine-stop-audit-monitor.sh "
            "misbehaved\" (unit_name=none). The real fix belongs in "
            "directive-engine-stop-audit-monitor.sh's own retry loop -- fix "
            "it there."
        )
        self.assertEqual(
            gate.extract_named_code_files(text),
            ["directive-engine-stop-audit-monitor.sh"],
        )

    def test_mixed_boilerplate_and_real_objective(self):
        """A prompt that cites the tools in boilerplate form AND names a
        real, different objective file keeps only the real objective."""
        text = (
            "GOVERNING CHAIN: query resource_governor.py --query-umr "
            "yourself first. REAL GAP FOUND in pm-sentinel-tick.sh Check 2a: "
            "fix + redispatch, or record via superboss-register.py "
            "mark-umr-terminal."
        )
        self.assertEqual(
            gate.extract_named_code_files(text), ["pm-sentinel-tick.sh"]
        )


class TestCompletionGateConcurrentProgressFiles(unittest.TestCase):
    """(a) two simulated workers, two branches, two progress/<task_id>.md
    files -- merge produces zero conflicts."""

    def test_two_workers_no_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = os.path.join(tmp, "origin.git")
            os.makedirs(origin)
            run(origin, "init", "-q", "--bare", "-b", "main")

            seed = os.path.join(tmp, "seed")
            init_repo(seed)
            with open(os.path.join(seed, "README.md"), "w") as f:
                f.write("seed\n")
            run(seed, "add", "-A")
            run(seed, "commit", "-q", "-m", "seed")
            run(seed, "remote", "add", "origin", origin)
            run(seed, "push", "-q", "-u", "origin", "main")

            worker_a = os.path.join(tmp, "worker_a")
            worker_b = os.path.join(tmp, "worker_b")
            run(tmp, "clone", "-q", origin, worker_a)
            run(tmp, "clone", "-q", origin, worker_b)

            # Worker A: task-aaa writes ONLY its own progress file.
            run(worker_a, "checkout", "-q", "-b", "worker/task-aaa")
            os.makedirs(os.path.join(worker_a, "progress"), exist_ok=True)
            with open(os.path.join(worker_a, "progress", "task-aaa.md"), "w") as f:
                f.write("## Completed\n- [x] step 1\n## Remaining\n- [ ] step 2\n")
            run(worker_a, "add", "-A")
            run(worker_a, "commit", "-q", "-m", "Worker task-aaa: checkpoint")
            run(worker_a, "push", "-q", "-u", "origin", "worker/task-aaa")

            # Worker B: task-bbb writes ONLY its own progress file,
            # concurrently, off the SAME original base commit.
            run(worker_b, "checkout", "-q", "-b", "worker/task-bbb")
            os.makedirs(os.path.join(worker_b, "progress"), exist_ok=True)
            with open(os.path.join(worker_b, "progress", "task-bbb.md"), "w") as f:
                f.write("## Completed\n- [x] step A\n## Remaining\n- [ ] step B\n")
            run(worker_b, "add", "-A")
            run(worker_b, "commit", "-q", "-m", "Worker task-bbb: checkpoint")
            run(worker_b, "push", "-q", "-u", "origin", "worker/task-bbb")

            # Merge both branches into main, one after another -- exactly
            # what landing both PRs looks like. Must succeed with NO
            # conflict markers.
            integrator = os.path.join(tmp, "integrator")
            run(tmp, "clone", "-q", origin, integrator)
            run(integrator, "fetch", "-q", "origin",
                "worker/task-aaa", "worker/task-bbb")
            run(integrator, "merge", "-q", "--no-edit", "origin/worker/task-aaa")
            merge_b = subprocess.run(
                ["git", "-C", integrator, "merge", "--no-edit",
                 "origin/worker/task-bbb"],
                capture_output=True, text=True,
            )
            self.assertEqual(
                merge_b.returncode, 0,
                f"expected a clean merge, got conflict: {merge_b.stdout}\n{merge_b.stderr}",
            )
            self.assertTrue(
                os.path.exists(os.path.join(integrator, "progress", "task-aaa.md"))
            )
            self.assertTrue(
                os.path.exists(os.path.join(integrator, "progress", "task-bbb.md"))
            )

    def test_shared_progress_md_DOES_conflict_control_case(self):
        """Control case proving the OLD shared-PROGRESS.md scheme really did
        conflict, so the fix above is a real behavior change, not a test
        that would have passed either way."""
        with tempfile.TemporaryDirectory() as tmp:
            origin = os.path.join(tmp, "origin.git")
            os.makedirs(origin)
            run(origin, "init", "-q", "--bare", "-b", "main")

            seed = os.path.join(tmp, "seed")
            init_repo(seed)
            with open(os.path.join(seed, "PROGRESS.md"), "w") as f:
                f.write("## Completed\n## Remaining\n")
            run(seed, "add", "-A")
            run(seed, "commit", "-q", "-m", "seed")
            run(seed, "remote", "add", "origin", origin)
            run(seed, "push", "-q", "-u", "origin", "main")

            worker_a = os.path.join(tmp, "worker_a")
            worker_b = os.path.join(tmp, "worker_b")
            run(tmp, "clone", "-q", origin, worker_a)
            run(tmp, "clone", "-q", origin, worker_b)

            for wdir, label in ((worker_a, "aaa"), (worker_b, "bbb")):
                run(wdir, "checkout", "-q", "-b", f"worker/task-{label}")
                with open(os.path.join(wdir, "PROGRESS.md"), "w") as f:
                    f.write(f"## Completed\n- [x] {label} did work\n## Remaining\n")
                run(wdir, "add", "-A")
                run(wdir, "commit", "-q", "-m", f"Worker task-{label}: checkpoint")
                run(wdir, "push", "-q", "-u", "origin", f"worker/task-{label}")

            integrator = os.path.join(tmp, "integrator")
            run(tmp, "clone", "-q", origin, integrator)
            run(integrator, "fetch", "-q", "origin",
                "worker/task-aaa", "worker/task-bbb")
            run(integrator, "merge", "-q", "--no-edit", "origin/worker/task-aaa")
            merge_b = subprocess.run(
                ["git", "-C", integrator, "merge", "--no-edit",
                 "origin/worker/task-bbb"],
                capture_output=True, text=True,
            )
            self.assertNotEqual(
                merge_b.returncode, 0,
                "expected the OLD shared-PROGRESS.md scheme to conflict here",
            )


class TestCompletionGateRejectsDocOnlyDiff(unittest.TestCase):
    """(b) a doc-only diff for a code-named objective is rejected."""

    def _make_task(self, tmp, prompt_text):
        task_dir = os.path.join(tmp, "task")
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "prompt.txt"), "w") as f:
            f.write(prompt_text)
        return task_dir

    def _make_workspace(self, tmp):
        ws = os.path.join(tmp, "ws")
        init_repo(ws)
        with open(os.path.join(ws, "dispatch_core.py"), "w") as f:
            f.write("def swap_gate():\n    return False\n")
        with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
            f.write("## Completed\n## Remaining\n")
        run(ws, "add", "-A")
        run(ws, "commit", "-q", "-m", "seed")
        # fake an origin/main ref pointing at the seed commit
        run(ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        run(ws, "checkout", "-q", "-b", "worker/task-xxx")
        return ws

    def test_progress_md_only_diff_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(
                tmp,
                "Unwedge dispatch: swap gate vetoes on STATIC occupancy "
                "(dispatch_core.py, in the swap gate).",
            )
            ws = self._make_workspace(tmp)
            # Worker only rewrites PROGRESS.md, exactly PR #317/#321's shape.
            with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
                f.write("## Completed\n- [x] fixed the swap gate (see notes)\n## Remaining\n")
            run(ws, "add", "-A")
            run(ws, "commit", "-q", "-m", "PROGRESS.md-only change")

            ok, reason = gate.check_completion(task_dir, ws, "main")
            self.assertFalse(ok, reason)
            self.assertIn("dispatch_core.py", reason)

            rc = gate.main([
                "check-completion",
                "--task-dir", task_dir,
                "--workspace", ws,
                "--default-branch", "main",
            ])
            self.assertEqual(rc, 1)

    def test_real_code_change_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(
                tmp,
                "Unwedge dispatch: swap gate vetoes on STATIC occupancy "
                "(dispatch_core.py, in the swap gate).",
            )
            ws = self._make_workspace(tmp)
            with open(os.path.join(ws, "dispatch_core.py"), "w") as f:
                f.write("def swap_gate():\n    return True  # real fix\n")
            with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
                f.write("## Completed\n- [x] fixed the swap gate\n## Remaining\n")
            run(ws, "add", "-A")
            run(ws, "commit", "-q", "-m", "real fix + progress note")

            ok, reason = gate.check_completion(task_dir, ws, "main")
            self.assertTrue(ok, reason)

            rc = gate.main([
                "check-completion",
                "--task-dir", task_dir,
                "--workspace", ws,
                "--default-branch", "main",
            ])
            self.assertEqual(rc, 0)

    def test_no_code_named_objective_gate_does_not_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(
                tmp, "docs: re-verify UMR-20260813-155201-da76 self-block claim"
            )
            ws = self._make_workspace(tmp)
            with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
                f.write("## Completed\n- [x] re-verified\n## Remaining\n")
            run(ws, "add", "-A")
            run(ws, "commit", "-q", "-m", "docs-only change")

            ok, reason = gate.check_completion(task_dir, ws, "main")
            self.assertTrue(ok, reason)

    def test_rca_prompt_citing_only_boilerplate_tools_docs_only_diff_accepted(self):
        """Real regression, RCA of UMR-20260813-060311-6eea
        (UMR-20260814-013850-fd7f): pm-sentinel-tick.sh Check 2a's own RCA
        prompt template cites resource_governor.py/superboss-register.py in
        every killed-row RCA it dispatches. Before this fix, a genuinely
        cross-repo or no-code-needed RCA disposition (a doc-only diff) was
        wrongly REJECTED here purely because the prompt cites those two
        tool names -- this is exactly that scenario, and it must now be
        accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(
                tmp,
                "GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel "
                "tick). REAL GAP FOUND: resource_governor.py --query-umr "
                "--umr-id UMR-X shows status=killed. This needs a real RCA: "
                "read the row's full real outputs_json/reason (query "
                "resource_governor.py --query-umr --umr-id UMR-X yourself "
                "first), determine the real root cause, and either fix + "
                "redispatch the real remaining scope, or record a real, "
                "honest terminal outcome via superboss-register.py "
                "mark-umr-terminal citing real evidence.",
            )
            ws = self._make_workspace(tmp)
            with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
                f.write("## Completed\n- [x] RCA'd, already resolved by a prior session\n## Remaining\n")
            run(ws, "add", "-A")
            run(ws, "commit", "-q", "-m", "docs-only RCA disposition")

            ok, reason = gate.check_completion(task_dir, ws, "main")
            self.assertTrue(ok, reason)

    def test_live_deploy_drift_reconcile_prompt_docs_only_diff_accepted(self):
        """Real regression, UMR-20260814-051532-2ae4: this task's own real
        SPEC text cites check_live_scripts_drift.py and sync-repos.sh by
        bare name (the diagnostic tool to run / the script whose behavior
        to read), plus the two file basenames from a drift-check's own
        "N real tracked file(s) differ:" evidence list. The task's real
        completion was landing an already-open, already-audited PR by
        merging it directly and fast-forwarding the live checkout -- a
        genuinely no-new-code-in-this-workspace disposition. Before this
        fix, this was wrongly REJECTED purely because the prompt cites
        those meta-tool names; it must now be accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(
                tmp,
                "REAL GAP FOUND: the real live checkout at "
                "/opt/veridian/scripts is NOT in sync with origin/main, "
                "verified live this tick via check_live_scripts_drift.py "
                "--live-dir /opt/veridian/scripts -- real HEAD=abc vs real "
                "origin/main=def, 2 real tracked file(s) differ: "
                "resource_governor.py, superboss-register.py. Determine why "
                "the live checkout is stuck off-main / dirty (sync-repos.sh "
                "deliberately refuses to force-overwrite a dirty or "
                "non-main checkout -- see its own comments), and either "
                "safely reconcile it onto origin/main or record a real, "
                "honest terminal outcome.",
            )
            ws = self._make_workspace(tmp)
            with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
                f.write(
                    "## Completed\n- [x] merged the already-open, "
                    "already-audited PR carrying the real fix and "
                    "fast-forwarded the live checkout onto it\n## Remaining\n"
                )
            run(ws, "add", "-A")
            run(ws, "commit", "-q", "-m", "docs-only reconcile disposition")

            ok, reason = gate.check_completion(task_dir, ws, "main")
            self.assertTrue(ok, reason)


    def test_rca_prompt_quoting_killed_row_reason_docs_only_diff_accepted(self):
        """Real regression (UMR-20260814-080423-bd93), reconstructed from
        real live evidence: PM sentinel gathered 31 of 120 umr_tasks
        register rows (2026-08-14T01:51-07:47Z) with status='failed', ALL
        carrying the identical worker-exit-status-bridge.py reason, while
        `systemctl --user show` on the real units independently confirmed
        Result=success/ExecMainStatus=0 -- i.e. clean exit-0 workers wrongly
        recorded as failed. Root-caused to this exact gate: task
        UMR-20260814-071851-4d86 (RCA of UMR-20260807-003517-23bb, real unit
        veridian-worker@task-20260814-071919-rca--umr-20260807-003517-23bb-
        killed.service) did real, correct work -- verified the original
        decline, wrote a real RCA doc, called
        `superboss-register.py mark-umr-terminal` for the target row -- with
        a genuinely doc-only diff (confirmed live: `git diff` vs origin/main
        touches only PROGRESS.md, ai-os/boss/ACTIVE-CLAIMS.yaml, and its own
        progress/<task_id>.md). Before this fix, the target row's own
        quoted `reason` field (naming
        directive-engine-stop-audit-monitor.sh as part of the ORIGINAL
        incident it investigated) made this gate wrongly reject that real,
        correct completion -- forcing task.yaml to status='blocked', which
        worker-exit-status-bridge.py then (correctly, per its own scope)
        bridged to umr_tasks.status='failed' despite the real exit code
        being 0. Must now be accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(
                tmp,
                "GOVERNING CHAIN: this task's own dispatching UMR (PM-sentinel "
                "tick). REAL GAP FOUND: resource_governor.py --query-umr "
                "--umr-id UMR-20260807-003517-23bb shows status=killed, real "
                "recorded reason: \"a real, deliberate, principled decline of "
                "a request to build new /proc-based attribution tooling to "
                "identify the caller stopping veridian-directive-engine.service, "
                "citing pm_decisions_pending proposal 296 as justification... "
                "describing already-live D-Bus StopUnit/KillUnit "
                "instrumentation (directive-engine-stop-audit-monitor.sh, "
                "systemd unit veridian-directive-engine-stop-audit.service, "
                "FChecklist/veridian-scripts commit b6c7be4)\" (unit_name=none). "
                "This needs a real RCA: read the row's full real "
                "outputs_json/reason (query resource_governor.py --query-umr "
                "--umr-id UMR-20260807-003517-23bb yourself first, do not "
                "trust this summary alone), determine the real root cause, "
                "and either fix + redispatch the real remaining scope, or "
                "record a real, honest terminal outcome via "
                "superboss-register.py mark-umr-terminal citing real "
                "evidence. Do not fabricate completion.",
            )
            ws = self._make_workspace(tmp)
            with open(os.path.join(ws, "PROGRESS.md"), "w") as f:
                f.write(
                    "## Completed\n- [x] RCA confirms correctly-killed, no "
                    "fix or redispatch needed; recorded real terminal "
                    "outcome for the target row via mark-umr-terminal\n"
                    "## Remaining\n"
                )
            run(ws, "add", "-A")
            run(ws, "commit", "-q", "-m", "docs-only RCA disposition")

            ok, reason = gate.check_completion(task_dir, ws, "main")
            self.assertTrue(ok, reason)


class TestRollupIsDeterministicAndGenerated(unittest.TestCase):
    def test_rollup_concatenates_per_task_files_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(os.path.join(ws, "progress"))
            with open(os.path.join(ws, "progress", "task-bbb.md"), "w") as f:
                f.write("bbb body")
            with open(os.path.join(ws, "progress", "task-aaa.md"), "w") as f:
                f.write("aaa body")

            out_path = os.path.join(tmp, "out.md")
            rc = gate.main(["rollup", "--workspace", ws, "--output", out_path])
            self.assertEqual(rc, 0)
            with open(out_path) as f:
                content = f.read()
            self.assertLess(
                content.index("task-aaa.md"), content.index("task-bbb.md")
            )
            self.assertIn("GENERATED", content)


if __name__ == "__main__":
    unittest.main()
