"""Real regression test for the AUDIT:FAIL finding posted on PR #301 (head
9014c2bf0a0018c116683292755cbeaf197ed9a5): quality-gate.sh's new
build-lock-contended "no active umr_tasks row to requeue" long-wait fallback
branch (the untracked-task case -- a task created directly via a task.yaml +
systemd unit, never through resource_governor.submit()) shipped with zero
test coverage. This exercises the REAL quality-gate.sh as a subprocess
against a real, isolated build-lock file and a real npm build, never a
reimplementation of the branch logic.

Two genuinely orthogonal process boundaries are faked, same convention
tests/test_build_lock_contended_requeue.py already established ("fake the
process boundary, keep everything else real"):
  1. `python3 .../superboss-register.py requeue-build-lock-contended` is
     intercepted by a PATH-prepended `python3` wrapper that returns the
     exact real "no active (queued/dispatched/running) umr_tasks row found"
     SystemExit message (see superboss-register.py's own
     cmd_requeue_build_lock_contended) with exit 1 -- that CLI's own
     correctness is already covered by test_build_lock_contended_requeue.py;
     this file's job is quality-gate.sh's own handling of that output, not
     re-testing the CLI. Every OTHER python3 call (already_passed, run_gate's
     own result-recording heredocs) passes straight through to the real
     interpreter.
  2. BUILD_LOCK_FILE/BUILD_LOCK_SHORT_WAIT_SECONDS/BUILD_LOCK_LONG_WAIT_SECONDS
     are overridden via env (this PR also makes them "${VAR:-default}",
     default unchanged for every other caller -- same precedent as
     GATE_STEP_TIMEOUT_SECONDS/BUILD_MAX_OLD_SPACE_MB) so this test never
     touches the real shared production lock file
     /tmp/veridian-quality-gate-build.lock and never waits the real 700s.

The build command itself is real (`npm run build` against a real, minimal
package.json with `"build": "true"`), not stubbed -- only the two boundaries
above are.
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUALITY_GATE = os.path.join(SCRIPTS_DIR, "quality-gate.sh")


def _make_fake_python3_bin(tmp_bin_dir, real_python3):
    """A python3 shim that intercepts ONLY the exact requeue-build-lock-
    contended CLI call this new branch is exercising, delegating every
    other invocation straight through to the real interpreter."""
    path = os.path.join(tmp_bin_dir, "python3")
    with open(path, "w") as f:
        f.write(textwrap.dedent(f"""\
            #!/bin/bash
            if [[ "$*" == *"requeue-build-lock-contended"* ]]; then
              echo "requeue-build-lock-contended: no active (queued/dispatched/running) umr_tasks row found for task_identity='fake-untracked-task' -- refusing to requeue a row that does not really exist" >&2
              exit 1
            fi
            exec "{real_python3}" "$@"
            """))
    os.chmod(path, 0o755)
    return path


def _make_node_workspace():
    ws = tempfile.mkdtemp()
    os.makedirs(os.path.join(ws, "node_modules"))  # present -> skips npm install
    with open(os.path.join(ws, "package.json"), "w") as f:
        f.write('{"name": "t", "version": "1.0.0", "scripts": {"build": "true"}}')
    return ws


@unittest.skipUnless(shutil.which("npm"), "npm not available in this environment")
class BuildLockUntrackedTaskLongWaitTest(unittest.TestCase):
    def setUp(self):
        real_python3 = shutil.which("python3")
        self.tmp_bin = tempfile.mkdtemp()
        _make_fake_python3_bin(self.tmp_bin, real_python3)
        self.lock_file = os.path.join(tempfile.mkdtemp(), "test-build.lock")
        self.env = dict(os.environ)
        self.env["PATH"] = self.tmp_bin + os.pathsep + self.env["PATH"]
        self.env["BUILD_LOCK_FILE"] = self.lock_file
        self.env["BUILD_LOCK_SHORT_WAIT_SECONDS"] = "1"
        self.env["BUILD_LOCK_LONG_WAIT_SECONDS"] = "2"

    def _run_gate_with_lock_held(self, workspace, hold_seconds):
        """Holds a real flock on self.lock_file (an isolated temp file, never
        the shared production lock) for hold_seconds in a background process,
        then runs the real quality-gate.sh while it's held -- exactly
        reproducing real host-wide build-lock contention."""
        holder = subprocess.Popen(
            ["bash", "-c", f'exec 8>"{self.lock_file}"; flock 8; sleep {hold_seconds}']
        )
        time.sleep(0.3)  # let the holder actually acquire before we start
        try:
            out = os.path.join(workspace, "gate-out.json")
            proc = subprocess.run(
                ["bash", QUALITY_GATE, workspace, out],
                capture_output=True, text=True, timeout=30, env=self.env,
            )
        finally:
            holder.wait(timeout=10)
        return proc

    def test_untracked_task_waits_out_lock_and_build_runs_for_real(self):
        # Lock held 1.5s: outlasts the 1s short wait (forces BUILD_LOCK_RC=1
        # -> faked "no active row" requeue failure -> new long-wait branch)
        # but frees up inside the 2s long wait, so the real `npm run build`
        # actually executes and passes.
        workspace = _make_node_workspace()
        proc = self._run_gate_with_lock_held(workspace, hold_seconds=1.5)
        self.assertIn(
            "build lock contended, but this task has no active umr_tasks row to requeue",
            proc.stdout,
        )
        self.assertIn("waiting up to 2s in-process for the lock", proc.stdout)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_untracked_task_long_wait_times_out_records_real_failure(self):
        # Lock held 5s: outlasts the 1s short wait AND the 2s long wait
        # combined, so the long wait itself must time out -- a real gate
        # failure must be recorded (real capacity failure), not a fabricated
        # pass and not a silent requeue.
        workspace = _make_node_workspace()
        proc = self._run_gate_with_lock_held(workspace, hold_seconds=5)
        self.assertIn(
            "build lock contended, but this task has no active umr_tasks row to requeue",
            proc.stdout,
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
