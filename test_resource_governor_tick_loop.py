#!/usr/bin/env python3
"""Real test for resource_governor_tick_loop.sh's run_governor() -- the exit-2
(argparse usage-error) escalation added after the 2026-08-02 incident where a
dropped --reconcile-stale flag silently logged "unrecognized arguments" 7946
times before anyone noticed. SPEC: owner absolute stop-work order,
task-20260806-165921, first real test batch for the ~101-script untested
gap, UMR-20260806-175915-8f47.

Sources the REAL run_governor() function definition straight out of the live
script (bash itself parses/defines it; only the loop line `python3 /opt/
veridian/scripts/resource_governor.py "$subcmd"` is not invoked, since this
test never runs the infinite `while true` loop or touches the real governor)
-- extracted via `sed`/awk between the function's own real, unique
`run_governor() {` / closing `}` markers already present in the file, so a
future edit to this function's real body is exactly what the next test run
exercises, not a frozen copy. Real python3 stand-ins for resource_governor.py
are placed first on PATH via a PATH-prepended wrapper script (the loop's own
hardcoded absolute path means it can only be intercepted by shadowing
`python3` itself, exactly like the codebase's own PREFLIGHT-GUARD-BLOCK
tests intercept absolute-path callees).
"""
import os
import re
import stat
import subprocess
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(SCRIPTS_DIR, "resource_governor_tick_loop.sh")


def _extract_run_governor_function():
    with open(SCRIPT_PATH) as f:
        content = f.read()
    m = re.search(r"run_governor\(\)\s*\{.*?\n\}\n", content, re.DOTALL)
    assert m, "run_governor() function definition not found in resource_governor_tick_loop.sh -- test extraction anchor is stale"
    return m.group(0)


FAKE_PYTHON3 = """#!/bin/bash
# Stand-in for `python3 /opt/veridian/scripts/resource_governor.py <subcmd>`.
# Reads desired exit code from RG_FAKE_EXIT_CODE; everything else (e.g. a
# real `python3 -c ...` call elsewhere) passes straight through to the real
# interpreter so this never breaks unrelated tooling.
if [ "$2" = "/opt/veridian/scripts/resource_governor.py" ] || [[ "$*" == *"resource_governor.py"* ]]; then
  echo "fake resource_governor.py invoked with: $*"
  exit "${RG_FAKE_EXIT_CODE:-0}"
fi
exec /usr/bin/python3 "$@"
"""


def _run_with_fake_governor(exit_code):
    fn_src = _extract_run_governor_function()
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "tick.log")
        attention = os.path.join(d, "ATTENTION.md")
        bin_dir = os.path.join(d, "bin")
        os.makedirs(bin_dir)
        fake_py = os.path.join(bin_dir, "python3")
        with open(fake_py, "w") as f:
            f.write(FAKE_PYTHON3)
        os.chmod(fake_py, os.stat(fake_py).st_mode | stat.S_IEXEC)

        harness = f"""#!/bin/bash
set -uo pipefail
LOG={log!r}
ATTENTION={attention!r}
{fn_src}
run_governor --tick
echo "RC=$?"
"""
        harness_path = os.path.join(d, "harness.sh")
        with open(harness_path, "w") as f:
            f.write(harness)
        os.chmod(harness_path, os.stat(harness_path).st_mode | stat.S_IEXEC)

        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env["PATH"]
        env["RG_FAKE_EXIT_CODE"] = str(exit_code)
        proc = subprocess.run(["bash", harness_path], capture_output=True, text=True, timeout=15, env=env)
        log_content = open(log).read() if os.path.exists(log) else ""
        attention_content = open(attention).read() if os.path.exists(attention) else None
        return proc, log_content, attention_content


def test_exit_2_writes_critical_message_and_attention_file():
    proc, log_content, attention_content = _run_with_fake_governor(2)
    assert "RC=2" in proc.stdout
    assert "GOVERNOR-TICK-LOOP CRITICAL" in log_content
    assert "exited 2 (argparse usage/unrecognized-arguments error)" in log_content
    assert attention_content is not None, "ATTENTION.md must be written on exit code 2"
    assert "RESOURCE GOVERNOR TICK LOOP" in attention_content


def test_exit_0_does_not_write_attention_file():
    proc, log_content, attention_content = _run_with_fake_governor(0)
    assert "RC=0" in proc.stdout
    assert "GOVERNOR-TICK-LOOP CRITICAL" not in log_content
    assert attention_content is None, "ATTENTION.md must not be written on a normal exit"


def test_exit_1_ordinary_failure_does_not_write_attention_file():
    proc, log_content, attention_content = _run_with_fake_governor(1)
    assert "RC=1" in proc.stdout
    assert "GOVERNOR-TICK-LOOP CRITICAL" not in log_content
    assert attention_content is None, "an ordinary (non-argparse) failure must not be conflated with the exit-2 special case"


if __name__ == "__main__":
    test_exit_2_writes_critical_message_and_attention_file()
    test_exit_0_does_not_write_attention_file()
    test_exit_1_ordinary_failure_does_not_write_attention_file()
    print("PASS: all resource_governor_tick_loop.sh tests")
