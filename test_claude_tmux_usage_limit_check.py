"""Real subprocess tests for claude-tmux-usage-limit-check.sh.

This script has no `case "$1"` dispatcher -- it is a linear top-level
script -- so it is executed the natural way: `bash <path-to-script>` as a
real subprocess with a controlled PATH/env, exactly like a real caller
would run it.

Safety notes (read before touching this file):

* The real `tmux` binary is NEVER put on PATH for these tests. A fake
  `tmux` executable is installed first on PATH instead, and it logs every
  invocation's argv to a file the tests read back and assert on. This
  machine has a REAL, live tmux session literally named "claude" running
  right now, which is exactly the session this script watches by default
  -- every test below overrides CLAUDE_TMUX_SESSION_NAME to a unique
  pytest-only name and, more importantly, never lets the real tmux binary
  run at all, so the real session is never touched, read, or sent
  keystrokes.

* The script hard-codes STATE_FILE/LOG_FILE under the real, shared
  /opt/veridian/ai-os/{locks,logs} production directories, with no env-var
  override available (confirmed by reading the script -- unlike
  CLAUDE_TMUX_SESSION_NAME and the *_WAIT_SECONDS knobs, which are
  overridable). Since the script really does read/write those two exact
  files, the `protect_real_state_files` fixture below backs their content
  up before every test and restores them byte-for-byte afterwards, so
  these tests never leave the real box in a different state than they
  found it, and never race a legitimate concurrent writer.
"""
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "claude-tmux-usage-limit-check.sh"

REAL_STATE_FILE = Path("/opt/veridian/ai-os/locks/claude-tmux-usage-limit-resume-epoch.txt")
REAL_LOG_FILE = Path("/opt/veridian/ai-os/logs/claude-tmux-usage-limit-watchdog.log")


@pytest.fixture(autouse=True)
def protect_real_state_files():
    backups = {}
    for path in (REAL_STATE_FILE, REAL_LOG_FILE):
        backups[path] = path.read_bytes() if path.exists() else None
    try:
        yield
    finally:
        for path, content in backups.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)


def _write_tmux_stub(bin_dir: Path, log_file: Path) -> None:
    """A fake `tmux` binary. Logs every invocation's argv, and answers
    has-session / capture-pane / send-keys based on env vars the test sets:
      TMUX_STUB_HAS_SESSION_EXIT   -- exit code for `has-session`
      TMUX_STUB_PANE_TEXT_FILE     -- file whose contents `capture-pane` prints
    """
    stub = bin_dir / "tmux"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            printf '%s\\n' "$*" >> "{log_file}"
            case "$1" in
              has-session)
                exit "${{TMUX_STUB_HAS_SESSION_EXIT:-0}}"
                ;;
              capture-pane)
                if [ -n "$TMUX_STUB_PANE_TEXT_FILE" ] && [ -f "$TMUX_STUB_PANE_TEXT_FILE" ]; then
                  cat "$TMUX_STUB_PANE_TEXT_FILE"
                fi
                exit 0
                ;;
              send-keys)
                exit 0
                ;;
              *)
                exit 0
                ;;
            esac
            """
        )
    )
    stub.chmod(0o755)


def _run_script(bin_dir: Path, env_overrides: dict) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir),
    }
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_no_tmux_session_and_no_prior_state_is_a_silent_noop(tmp_path):
    if REAL_STATE_FILE.exists():
        REAL_STATE_FILE.unlink()
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-noop"

    result = _run_script(
        tmp_path,
        {"CLAUDE_TMUX_SESSION_NAME": session, "TMUX_STUB_HAS_SESSION_EXIT": "1"},
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    calls = tmux_log.read_text().strip().splitlines()
    assert calls == [f"has-session -t {session}"], calls
    assert not REAL_STATE_FILE.exists()


def test_no_tmux_session_clears_stale_state_file_and_logs_it(tmp_path):
    REAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REAL_STATE_FILE.write_text("9999999999\n")
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-stale-clear"

    result = _run_script(
        tmp_path,
        {"CLAUDE_TMUX_SESSION_NAME": session, "TMUX_STUB_HAS_SESSION_EXIT": "1"},
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not REAL_STATE_FILE.exists()
    log_text = REAL_LOG_FILE.read_text()
    assert f"No tmux session '{session}' found; clearing stale wait-state" in log_text


def test_session_exists_normal_pane_no_state_is_noop(tmp_path):
    if REAL_STATE_FILE.exists():
        REAL_STATE_FILE.unlink()
    pane_file = tmp_path / "pane.txt"
    pane_file.write_text("$ some normal shell prompt output\nOK\n")
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-normal-noop"

    result = _run_script(
        tmp_path,
        {
            "CLAUDE_TMUX_SESSION_NAME": session,
            "TMUX_STUB_HAS_SESSION_EXIT": "0",
            "TMUX_STUB_PANE_TEXT_FILE": str(pane_file),
        },
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not REAL_STATE_FILE.exists()
    calls = tmux_log.read_text().strip().splitlines()
    assert calls[0] == f"has-session -t {session}"
    assert any(c.startswith("capture-pane") for c in calls)
    assert not any(c.startswith("send-keys") for c in calls)


def test_session_exists_normal_pane_but_stale_state_clears_it(tmp_path):
    REAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REAL_STATE_FILE.write_text(f"{int(time.time()) + 500}\n")
    pane_file = tmp_path / "pane.txt"
    pane_file.write_text("$ back to normal now\n")
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-resumed-by-hand"

    result = _run_script(
        tmp_path,
        {
            "CLAUDE_TMUX_SESSION_NAME": session,
            "TMUX_STUB_HAS_SESSION_EXIT": "0",
            "TMUX_STUB_PANE_TEXT_FILE": str(pane_file),
        },
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not REAL_STATE_FILE.exists()
    calls = tmux_log.read_text().strip().splitlines()
    assert not any(c.startswith("send-keys") for c in calls)
    log_text = REAL_LOG_FILE.read_text()
    assert f"session '{session}'" in log_text
    assert "clearing wait-state" in log_text


def test_banner_detected_first_time_records_resume_epoch(tmp_path):
    if REAL_STATE_FILE.exists():
        REAL_STATE_FILE.unlink()
    future_epoch = int(time.time()) + 500
    pane_file = tmp_path / "pane.txt"
    pane_file.write_text(f"Claude AI usage limit reached|{future_epoch}\n")
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-first-detect"

    result = _run_script(
        tmp_path,
        {
            "CLAUDE_TMUX_SESSION_NAME": session,
            "TMUX_STUB_HAS_SESSION_EXIT": "0",
            "TMUX_STUB_PANE_TEXT_FILE": str(pane_file),
        },
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REAL_STATE_FILE.exists()
    recorded = int(REAL_STATE_FILE.read_text().strip())
    assert recorded == future_epoch

    calls = tmux_log.read_text().strip().splitlines()
    assert not any(c.startswith("send-keys") for c in calls), (
        "must never send the resume keystroke on the very first detection"
    )
    log_text = REAL_LOG_FILE.read_text()
    assert "Detected Claude usage-limit banner" in log_text


def test_banner_present_and_resume_time_not_yet_reached_keeps_waiting(tmp_path):
    future_epoch = int(time.time()) + 500
    REAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REAL_STATE_FILE.write_text(f"{future_epoch}\n")
    pane_file = tmp_path / "pane.txt"
    pane_file.write_text("Claude AI usage limit reached. Try again later.\n")
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-still-waiting"

    result = _run_script(
        tmp_path,
        {
            "CLAUDE_TMUX_SESSION_NAME": session,
            "TMUX_STUB_HAS_SESSION_EXIT": "0",
            "TMUX_STUB_PANE_TEXT_FILE": str(pane_file),
        },
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert REAL_STATE_FILE.exists()
    assert int(REAL_STATE_FILE.read_text().strip()) == future_epoch
    calls = tmux_log.read_text().strip().splitlines()
    assert not any(c.startswith("send-keys") for c in calls)
    log_text = REAL_LOG_FILE.read_text()
    assert f"Still waiting on tmux session '{session}'" in log_text


def test_banner_present_and_resume_time_reached_sends_resume_keystroke(tmp_path):
    past_epoch = int(time.time()) - 5
    REAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REAL_STATE_FILE.write_text(f"{past_epoch}\n")
    pane_file = tmp_path / "pane.txt"
    pane_file.write_text("Claude AI usage limit reached. Try again later.\n")
    tmux_log = tmp_path / "tmux_calls.log"
    _write_tmux_stub(tmp_path, tmux_log)
    session = "pytest-session-resume-now"

    result = _run_script(
        tmp_path,
        {
            "CLAUDE_TMUX_SESSION_NAME": session,
            "TMUX_STUB_HAS_SESSION_EXIT": "0",
            "TMUX_STUB_PANE_TEXT_FILE": str(pane_file),
        },
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert not REAL_STATE_FILE.exists(), "state file must be cleared after sending the resume keystroke"

    calls = tmux_log.read_text().strip().splitlines()
    send_calls = [c for c in calls if c.startswith("send-keys")]
    assert len(send_calls) == 1, calls
    assert send_calls[0] == (
        f"send-keys -t {session} claude --continue --permission-mode bypassPermissions Enter"
    )

    log_text = REAL_LOG_FILE.read_text()
    assert "sending the resume command automatically" in log_text
    assert "Resume command sent." in log_text
