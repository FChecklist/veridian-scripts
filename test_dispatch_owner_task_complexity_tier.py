"""Real subprocess test for dispatch-owner-task.sh's --complexity-tier
passthrough (task-20260816-041054, tier-aware Haiku 4.5 routing -- see
OWNER_DECISION_2026-08-16_HAIKU_TIER_ROUTING_MECHANICAL_EXCEPTION.md).

Same stubbing convention as test_dispatch_owner_task_attach.py: a fake
`python3` placed first on PATH forwards `-c` invocations to the real
python3 (this script's own inline JSON one-liners are pure functions of
piped-in text, safe to run for real) and returns canned JSON for the real
script paths this script calls by name. The `resource_governor.py --submit`
stub additionally captures the real --spec-file's content (written for real
by this script's own inline `json.dump` call, just before the stub runs) so
this test can assert on the real inputs dict resource_governor.py's real
`submit()` would have received -- not a re-implementation of that dict.

tmux IS real here, same disposable-session seam as test_dispatch_owner_task_attach.py.
"""
import json
import os
import shutil
import stat
import subprocess
import uuid

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(SCRIPT_DIR, "dispatch-owner-task.sh")

FAKE_PYTHON3 = """#!/bin/bash
echo "python3 $*" >> "$CALL_LOG"
if [ "$1" = "-c" ]; then
    exec "$REAL_PYTHON3" "$@"
fi
case "$1" in
  superboss-register.py)
    shift
    sub="$1"
    case "$sub" in
      check-content-duplicate)
        echo '{"content_duplicate_found": false}'
        ;;
      check-target-identifier-duplicate)
        echo '{"target_identifier_duplicate_found": false}'
        ;;
      log-work)
        echo '{"work_item_id": "WI-FAKE-TEST-1"}'
        ;;
      mark-umr-relay-attempted)
        echo '{}'
        ;;
      *)
        echo "unexpected superboss-register.py subcommand: $sub" >&2
        exit 1
        ;;
    esac
    ;;
  task-gateway.py)
    shift
    echo "task-gateway.py $*" >> "$CALL_LOG"
    cat "$FAKE_SUBMIT_JSON_FILE"
    ;;
  resource_governor.py)
    shift
    echo "resource_governor.py $*" >> "$CALL_LOG"
    # Real --spec-file's real content, captured before dispatch-owner-task.sh
    # deletes it (rm -f "$SPEC_FILE") right after this call returns.
    for a in "$@"; do
      if [ "$prev" = "--spec-file" ]; then
        cp "$a" "$CAPTURED_SPEC_FILE"
      fi
      prev="$a"
    done
    cat "$FAKE_RG_SUBMIT_JSON_FILE"
    ;;
  *)
    echo "unexpected python3 invocation: $*" >&2
    exit 1
    ;;
esac
"""


@pytest.fixture
def stub_env(tmp_path):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not available on this host")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_path = bin_dir / "python3"
    python_path.write_text(FAKE_PYTHON3)
    python_path.chmod(python_path.stat().st_mode | stat.S_IEXEC)

    call_log = tmp_path / "python_calls.log"
    call_log.write_text("")

    fake_submit_json_file = tmp_path / "fake_submit.json"
    fake_submit_json_file.write_text(json.dumps({"instruction_id": "INS-FAKE-TEST-1", "attachment": None}))
    fake_rg_submit_json_file = tmp_path / "fake_rg_submit.json"
    fake_rg_submit_json_file.write_text(json.dumps({"accepted": True, "umr_id": "UMR-FAKE-TEST-1"}))
    captured_spec_file = tmp_path / "captured_spec.json"

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    env["REAL_PYTHON3"] = shutil.which("python3")
    env["FAKE_SUBMIT_JSON_FILE"] = str(fake_submit_json_file)
    env["FAKE_RG_SUBMIT_JSON_FILE"] = str(fake_rg_submit_json_file)
    env["CAPTURED_SPEC_FILE"] = str(captured_spec_file)

    session = f"dispatch-owner-complexity-test-{uuid.uuid4().hex[:8]}"
    env["DISPATCH_TMUX_SESSION"] = session
    env["VERIDIAN_DISPATCH_LOCK_DIR"] = str(tmp_path / "locks")

    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-x", "220", "-y", "50"], check=True)
    try:
        yield {"env": env, "captured_spec_file": captured_spec_file}
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)


def _run(stub_env, args):
    return subprocess.run(
        ["bash", SCRIPT, *args],
        env=stub_env["env"],
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_complexity_tier_flag_reaches_real_submit_spec_inputs(stub_env):
    proc = _run(stub_env, [
        "Test Title", "Test prompt text", "2", "claude_code_cli", "some-repo",
        "--complexity-tier", "mechanical",
    ])
    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr

    spec = json.loads(stub_env["captured_spec_file"].read_text())
    assert spec["inputs"].get("complexity_tier") == "mechanical", spec


def test_no_complexity_tier_flag_omits_it_from_real_submit_spec_inputs(stub_env):
    """Safe default: every existing/other caller that never passes
    --complexity-tier gets an inputs dict with NO complexity_tier key at
    all (never an empty-string placeholder)."""
    proc = _run(stub_env, ["Test Title", "Test prompt text", "2"])
    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr

    spec = json.loads(stub_env["captured_spec_file"].read_text())
    assert "complexity_tier" not in spec["inputs"], spec
