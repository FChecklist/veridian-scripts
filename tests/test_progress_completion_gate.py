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
