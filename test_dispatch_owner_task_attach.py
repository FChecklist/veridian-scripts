"""Real subprocess tests for dispatch-owner-task.sh's --attach flag
(task-20260814-180459).

Same convention test_dispatch_docworker_task.py already establishes for
this family of scripts: dispatch-owner-task.sh hardcodes real, live,
outside-this-workspace calls (superboss-register.py's dedup checks,
task-gateway.py submit, resource_governor.py --submit/log-work) that would
register a real instruction/UMR/queued task against the live production
database if run for real -- not safe or in-scope to exercise here. Those
are stubbed with a fake `python3` placed first on PATH, which forwards
`-c` invocations to the REAL python3 (the script's own inline JSON-parsing
one-liners are pure functions of piped-in text, safe to run for real) and
returns canned JSON only for the three real script paths this script
calls by name. The script's OWN real bash logic (--attach argument
parsing, building ATTACH_SUBMIT_ARGS, and folding the returned attachment
record into $PROMPT via the real, unstubbed attachment_intake.py module)
runs unstubbed.

tmux IS real here (not stubbed) -- DISPATCH_TMUX_SESSION points at a
disposable, throwaway session created and destroyed by this test, exactly
per this script's own documented testability seam (see its module
docstring) -- so the actual relayed keystrokes are inspected for real via
`tmux capture-pane`, proving the attachment content reaches the real relay
text, not just an intermediate variable.

No file is ever created or modified under /opt/veridian outside this
workspace, and no row is ever written to the live production database, by
these tests.
"""
import json
import os
import shutil
import stat
import subprocess
import time
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
    fake_rg_submit_json_file = tmp_path / "fake_rg_submit.json"
    fake_rg_submit_json_file.write_text(json.dumps({
        "accepted": True,
        "umr_id": "UMR-FAKE-TEST-1",
    }))

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    env["REAL_PYTHON3"] = shutil.which("python3")
    env["FAKE_SUBMIT_JSON_FILE"] = str(fake_submit_json_file)
    env["FAKE_RG_SUBMIT_JSON_FILE"] = str(fake_rg_submit_json_file)

    session = f"dispatch-owner-attach-test-{uuid.uuid4().hex[:8]}"
    env["DISPATCH_TMUX_SESSION"] = session
    env["VERIDIAN_DISPATCH_LOCK_DIR"] = str(tmp_path / "locks")

    subprocess.run(["tmux", "new-session", "-d", "-s", session, "-x", "220", "-y", "50"], check=True)
    try:
        yield {
            "env": env,
            "call_log": call_log,
            "fake_submit_json_file": fake_submit_json_file,
            "session": session,
            "tmp_path": tmp_path,
        }
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


def _capture_pane(session):
    proc = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", session],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def test_attach_flag_is_stripped_and_positional_args_still_parse(stub_env, tmp_path):
    """--attach <path> must not be mistaken for a positional arg (title/
    prompt/tier/medium/repo), and must not require a real file to exist
    just to make it through bash's own arg-parsing loop (task-gateway.py's
    own --attach validates real existence; this script's job is only to
    pass the value through)."""
    stub_env["fake_submit_json_file"].write_text(json.dumps({
        "instruction_id": "INS-FAKE-TEST-1",
        "attachment": None,
    }))
    attach_path = str(tmp_path / "irrelevant-for-this-test.txt")

    proc = _run(stub_env, [
        "Test Title", "Test prompt text", "2", "claude_code_cli", "some-repo",
        "--attach", attach_path,
    ])

    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr
    calls = stub_env["call_log"].read_text()
    assert "task-gateway.py submit" in calls
    assert f"--attach {attach_path}" in calls
    # Positional args parsed correctly despite --attach being interleaved
    # into the same argv.
    assert "UMR-FAKE-TEST-1" in proc.stdout


def test_no_attach_flag_omits_attach_arg_from_submit_call(stub_env):
    """No behavior change for existing callers that never pass --attach."""
    stub_env["fake_submit_json_file"].write_text(json.dumps({
        "instruction_id": "INS-FAKE-TEST-2",
        "attachment": None,
    }))

    proc = _run(stub_env, ["Test Title", "Test prompt text", "2"])

    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr
    calls = stub_env["call_log"].read_text()
    assert "--attach" not in calls


def test_attachment_content_reaches_the_real_tmux_relay(stub_env):
    """End-to-end (within this stubbed boundary): the attachment record
    task-gateway.py submit (stubbed here) returns is rendered by the real,
    unstubbed attachment_intake.render_attachment_block() and folded into
    $PROMPT, which is what actually gets relayed into the real tmux
    session -- proving the wiring reaches the real delivery path, not just
    an intermediate shell variable."""
    canary = f"attach-canary-{uuid.uuid4().hex[:8]}"
    stub_env["fake_submit_json_file"].write_text(json.dumps({
        "instruction_id": "INS-FAKE-TEST-3",
        "attachment": {
            "path": "/fake/path/note.txt",
            "exists": True,
            "size_bytes": len(canary),
            "sha256": "deadbeef" * 8,
            "mimetype": "text/plain",
            "category": "text",
            "extraction_status": "extracted",
            "extracted_text": canary,
            "extracted_chars": len(canary),
            "truncated": False,
            "note": None,
        },
    }))

    proc = _run(stub_env, [
        "Test Title", "Original typed prompt", "2", "claude_code_cli", "some-repo",
        "--attach", "/fake/path/note.txt",
    ])

    assert "RELAYED into tmux session" in proc.stdout, proc.stdout + proc.stderr

    # tmux send-keys + a 1s sleep + Enter happen inside the script -- give
    # the real pane a moment to render before capturing it.
    time.sleep(1.5)
    pane_text = _capture_pane(stub_env["session"])
    assert canary in pane_text, pane_text
    assert "deadbeef" in pane_text, pane_text
    assert "Original typed prompt" in pane_text, pane_text
