#!/usr/bin/env python3
"""Real tests for worker-entrypoint.sh and doc-worker-entrypoint.sh's two
deterministic, side-effect-bounded safety mechanisms: the lifetime
invocation cap, and the pre-flight-guard hard-stop-vs-transient
classification (the PREFLIGHT-GUARD-BLOCK, whose own header comment already
says "tests/preflight_guard_hardstop_test.sh extracts this" -- that file was
never actually written; this is that real test). SPEC: owner absolute
stop-work order, task-20260806-165921, first real test batch for the
~101-script untested gap, UMR-20260806-175915-8f47 (both scripts are named
explicitly in that dispatch's own load-bearing-script list).

Both entrypoint scripts run a real `claude -p` invocation, real `git push`,
and real systemd unit management -- none of that is safe or meaningful to
run in a test. Instead, each block under test is extracted VERBATIM from the
live script file (regex-anchored on real, already-present markers/unique
text, not a frozen copy), spliced into a minimal real-bash harness that
defines only the small number of upstream variables the block itself reads,
and executed for real via `bash` with `python3`/`systemctl` intercepted
through a PATH-prepended stub (their absolute-path callees, e.g. `python3
/opt/veridian/scripts/veridian-task.py`, can only be intercepted by shadowing
the interpreter itself -- same technique required because these scripts
never accept an overridable script-path env var). Any invocation this stub
does not recognize (e.g. the script's own `python3 -c "import json,sys; ..."`
JSON-field extraction) passes straight through to the real interpreter, so
that real, unmodified logic is exercised too, not bypassed.
"""
import os
import re
import stat
import subprocess
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_ENTRYPOINT = os.path.join(SCRIPTS_DIR, "worker-entrypoint.sh")
DOC_WORKER_ENTRYPOINT = os.path.join(SCRIPTS_DIR, "doc-worker-entrypoint.sh")

CHECKPOINT_STUB = """#!/bin/bash
# Stand-in for `python3 /opt/veridian/scripts/veridian-task.py` and
# `.../preflight-guard.py`, both invoked by absolute path in the real
# scripts. Anything else (e.g. `python3 -c "..."` JSON parsing) passes
# through to the real interpreter unchanged.
args=("$@")
if [[ "${args[0]}" == *"veridian-task.py"* ]]; then
  echo "CHECKPOINT_CALL: ${args[*]:1}" >> "$CHECKPOINT_LOG"
  exit 0
fi
if [[ "${args[0]}" == *"preflight-guard.py"* ]]; then
  echo -n "$GUARD_FAKE_JSON"
  exit "${GUARD_FAKE_EXIT:-0}"
fi
exec /usr/bin/python3 "$@"
"""

SYSTEMCTL_STUB = """#!/bin/bash
echo "$@" >> "$SYSTEMCTL_LOG"
exit 0
"""


def _extract(pattern, content, label):
    m = re.search(pattern, content, re.DOTALL)
    assert m, f"extraction anchor for {label!r} not found -- script structure has drifted, update the test's anchor"
    return m.group(0)


def _extract_invocation_cap_block(script_path):
    content = open(script_path).read()
    return _extract(
        r'MAX_LIFETIME_INVOCATIONS="\$\{VERIDIAN[A-Z_]*MAX_LIFETIME_INVOCATIONS:-\d+\}".*?\nfi\n',
        content, "invocation cap block",
    )


def _extract_preflight_guard_block(script_path):
    content = open(script_path).read()
    return _extract(
        r"# --- PREFLIGHT-GUARD-BLOCK-START.*?# --- PREFLIGHT-GUARD-BLOCK-END\n",
        content, "preflight guard block",
    )


def _make_stub_bin(d, checkpoint_log, systemctl_log):
    bin_dir = os.path.join(d, "bin")
    os.makedirs(bin_dir)
    py = os.path.join(bin_dir, "python3")
    with open(py, "w") as f:
        f.write(CHECKPOINT_STUB)
    os.chmod(py, os.stat(py).st_mode | stat.S_IEXEC)
    sc = os.path.join(bin_dir, "systemctl")
    with open(sc, "w") as f:
        f.write(SYSTEMCTL_STUB)
    os.chmod(sc, os.stat(sc).st_mode | stat.S_IEXEC)
    return bin_dir


# ---------------------------------------------------------------------------
# Invocation cap block (both scripts share the identical real structure,
# just a different env var name / default)
# ---------------------------------------------------------------------------

def _run_invocation_cap(script_path, task_dir, prior_count, max_lifetime_env, systemd_unit_prefix):
    block = _extract_invocation_cap_block(script_path)
    with tempfile.TemporaryDirectory() as d:
        checkpoint_log = os.path.join(d, "checkpoint.log")
        systemctl_log = os.path.join(d, "systemctl.log")
        open(checkpoint_log, "w").close()
        open(systemctl_log, "w").close()
        bin_dir = _make_stub_bin(d, checkpoint_log, systemctl_log)

        if prior_count is not None:
            with open(os.path.join(task_dir, ".invocation_count"), "w") as f:
                f.write(str(prior_count))

        harness = f"""#!/bin/bash
set -uo pipefail
TASK_ID="test-task"
TASK_DIR={task_dir!r}
{block}
echo "REACHED_END_RC=$?"
"""
        harness_path = os.path.join(d, "harness.sh")
        with open(harness_path, "w") as f:
            f.write(harness)
        os.chmod(harness_path, os.stat(harness_path).st_mode | stat.S_IEXEC)

        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env["PATH"]
        env["CHECKPOINT_LOG"] = checkpoint_log
        env["SYSTEMCTL_LOG"] = systemctl_log
        if max_lifetime_env:
            k, v = max_lifetime_env
            env[k] = v
        proc = subprocess.run(["bash", harness_path], capture_output=True, text=True, timeout=15, env=env)
        checkpoint_content = open(checkpoint_log).read()
        systemctl_content = open(systemctl_log).read()
        new_count = open(os.path.join(task_dir, ".invocation_count")).read()
        return proc, checkpoint_content, systemctl_content, new_count


def test_worker_entrypoint_cap_hit_blocks_and_disables_unit(tmp_path):
    proc, checkpoint_log, systemctl_log, new_count = _run_invocation_cap(
        WORKER_ENTRYPOINT, str(tmp_path), prior_count=20,
        max_lifetime_env=("VERIDIAN_MAX_LIFETIME_INVOCATIONS", "20"),
        systemd_unit_prefix="veridian-worker@",
    )
    assert new_count.strip() == "21"
    assert "REACHED_END_RC=0" not in proc.stdout, "cap-hit path must exit before reaching past the cap check"
    assert "--status blocked" in checkpoint_log
    assert "PREVENTION CAP HIT" in checkpoint_log
    assert "disable veridian-worker@test-task.service" in systemctl_log
    assert proc.returncode == 0


def test_worker_entrypoint_under_cap_does_not_block(tmp_path):
    proc, checkpoint_log, systemctl_log, new_count = _run_invocation_cap(
        WORKER_ENTRYPOINT, str(tmp_path), prior_count=3,
        max_lifetime_env=("VERIDIAN_MAX_LIFETIME_INVOCATIONS", "20"),
        systemd_unit_prefix="veridian-worker@",
    )
    assert new_count.strip() == "4"
    assert "REACHED_END_RC=0" in proc.stdout
    assert checkpoint_log == ""
    assert systemctl_log == ""


def test_doc_worker_entrypoint_cap_hit_blocks_and_disables_unit(tmp_path):
    proc, checkpoint_log, systemctl_log, new_count = _run_invocation_cap(
        DOC_WORKER_ENTRYPOINT, str(tmp_path), prior_count=8,
        max_lifetime_env=("VERIDIAN_DOC_MAX_LIFETIME_INVOCATIONS", "8"),
        systemd_unit_prefix="veridian-docworker@",
    )
    assert new_count.strip() == "9"
    assert "REACHED_END_RC=0" not in proc.stdout
    assert "--status blocked" in checkpoint_log
    assert "PREVENTION CAP HIT" in checkpoint_log
    assert "disable veridian-docworker@test-task.service" in systemctl_log


def test_doc_worker_entrypoint_under_cap_does_not_block(tmp_path):
    proc, checkpoint_log, systemctl_log, new_count = _run_invocation_cap(
        DOC_WORKER_ENTRYPOINT, str(tmp_path), prior_count=1,
        max_lifetime_env=("VERIDIAN_DOC_MAX_LIFETIME_INVOCATIONS", "8"),
        systemd_unit_prefix="veridian-docworker@",
    )
    assert new_count.strip() == "2"
    assert "REACHED_END_RC=0" in proc.stdout
    assert checkpoint_log == ""


# ---------------------------------------------------------------------------
# Preflight guard block: hard-stop reasons vs transient reasons
# ---------------------------------------------------------------------------

def _run_preflight_guard(script_path, guard_exit, guard_reason, systemd_unit_prefix):
    block = _extract_preflight_guard_block(script_path)
    with tempfile.TemporaryDirectory() as d:
        task_dir = os.path.join(d, "task")
        os.makedirs(task_dir)
        checkpoint_log = os.path.join(d, "checkpoint.log")
        systemctl_log = os.path.join(d, "systemctl.log")
        open(checkpoint_log, "w").close()
        open(systemctl_log, "w").close()
        bin_dir = _make_stub_bin(d, checkpoint_log, systemctl_log)

        harness = f"""#!/bin/bash
set -uo pipefail
TASK_ID="test-task"
TASK_DIR={task_dir!r}
WORKSPACE={d!r}
{block}
echo "REACHED_END_RC=$?"
"""
        harness_path = os.path.join(d, "harness.sh")
        with open(harness_path, "w") as f:
            f.write(harness)
        os.chmod(harness_path, os.stat(harness_path).st_mode | stat.S_IEXEC)

        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env["PATH"]
        env["CHECKPOINT_LOG"] = checkpoint_log
        env["SYSTEMCTL_LOG"] = systemctl_log
        env["GUARD_FAKE_EXIT"] = str(guard_exit)
        env["GUARD_FAKE_JSON"] = f'{{"proceed": false, "reason": "{guard_reason}", "detail": "synthetic test detail"}}' if guard_exit != 0 else '{"proceed": true}'
        proc = subprocess.run(["bash", harness_path], capture_output=True, text=True, timeout=15, env=env)
        return proc, open(checkpoint_log).read(), open(systemctl_log).read()


HARD_STOP_REASONS_WORKER = [
    "circuit_breaker_tripped", "budget_exhausted", "openrouter_balance_exhausted",
    "credit_accountant_rejected", "tight_task_schema_violation", "crontab_unauthorized_change",
]
HARD_STOP_REASONS_DOC_WORKER = [
    "circuit_breaker_tripped", "tight_task_schema_violation", "crontab_unauthorized_change",
]


def test_worker_entrypoint_every_documented_hard_stop_reason_blocks():
    for reason in HARD_STOP_REASONS_WORKER:
        proc, checkpoint_log, systemctl_log = _run_preflight_guard(
            WORKER_ENTRYPOINT, guard_exit=1, guard_reason=reason, systemd_unit_prefix="veridian-worker@")
        assert "REACHED_END_RC=0" not in proc.stdout, f"{reason} must exit before reaching past the guard block"
        assert f"PRE-FLIGHT HARD STOP ({reason})" in checkpoint_log, (reason, checkpoint_log)
        assert "--status blocked" in checkpoint_log
        assert "disable veridian-worker@test-task.service" in systemctl_log, (reason, systemctl_log)


def test_worker_entrypoint_unrecognized_reason_is_transient_not_blocked():
    proc, checkpoint_log, systemctl_log = _run_preflight_guard(
        WORKER_ENTRYPOINT, guard_exit=1, guard_reason="disk_space_low", systemd_unit_prefix="veridian-worker@")
    assert "PRE-FLIGHT REJECTED (disk_space_low, transient)" in checkpoint_log
    assert "--status failed" in checkpoint_log
    assert systemctl_log == "", "a transient rejection must never disable the unit"


def test_worker_entrypoint_guard_pass_does_not_block():
    proc, checkpoint_log, systemctl_log = _run_preflight_guard(
        WORKER_ENTRYPOINT, guard_exit=0, guard_reason="", systemd_unit_prefix="veridian-worker@")
    assert "REACHED_END_RC=0" in proc.stdout
    assert checkpoint_log == ""
    assert systemctl_log == ""


def test_doc_worker_entrypoint_every_documented_hard_stop_reason_blocks():
    for reason in HARD_STOP_REASONS_DOC_WORKER:
        proc, checkpoint_log, systemctl_log = _run_preflight_guard(
            DOC_WORKER_ENTRYPOINT, guard_exit=1, guard_reason=reason, systemd_unit_prefix="veridian-docworker@")
        assert "REACHED_END_RC=0" not in proc.stdout, f"{reason} must exit before reaching past the guard block"
        assert f"PRE-FLIGHT HARD STOP ({reason})" in checkpoint_log, (reason, checkpoint_log)
        assert "disable veridian-docworker@test-task.service" in systemctl_log, (reason, systemctl_log)


def test_doc_worker_entrypoint_budget_exhausted_is_NOT_a_hard_stop_here():
    """doc-worker-entrypoint.sh's real hard-stop list is deliberately
    NARROWER than worker-entrypoint.sh's (no proxy/budget checks apply --
    this path uses the real subscription, see the script's own header
    comment). budget_exhausted/openrouter_balance_exhausted/credit_
    accountant_rejected must fall through to the transient branch here,
    proving the two scripts' real, currently-divergent lists rather than
    assuming they're identical."""
    proc, checkpoint_log, systemctl_log = _run_preflight_guard(
        DOC_WORKER_ENTRYPOINT, guard_exit=1, guard_reason="budget_exhausted",
        systemd_unit_prefix="veridian-docworker@")
    assert "PRE-FLIGHT REJECTED (budget_exhausted, transient)" in checkpoint_log
    assert systemctl_log == ""


if __name__ == "__main__":
    import pathlib
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d1:
        test_worker_entrypoint_cap_hit_blocks_and_disables_unit(pathlib.Path(d1))
    with _tf.TemporaryDirectory() as d2:
        test_worker_entrypoint_under_cap_does_not_block(pathlib.Path(d2))
    with _tf.TemporaryDirectory() as d3:
        test_doc_worker_entrypoint_cap_hit_blocks_and_disables_unit(pathlib.Path(d3))
    with _tf.TemporaryDirectory() as d4:
        test_doc_worker_entrypoint_under_cap_does_not_block(pathlib.Path(d4))
    test_worker_entrypoint_every_documented_hard_stop_reason_blocks()
    test_worker_entrypoint_unrecognized_reason_is_transient_not_blocked()
    test_worker_entrypoint_guard_pass_does_not_block()
    test_doc_worker_entrypoint_every_documented_hard_stop_reason_blocks()
    test_doc_worker_entrypoint_budget_exhausted_is_NOT_a_hard_stop_here()
    print("PASS: all worker-entrypoint.sh / doc-worker-entrypoint.sh guard tests")
