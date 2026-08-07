"""Real subprocess tests for claude-usage-limit-retry.sh.

This file has no `case "$1"` dispatcher -- it is purely a library of shell
functions (claude_text_signals_usage_limit, claude_usage_limit_parse_resume_epoch,
run_claude_usage_limit_retry) meant to be sourced by callers. So each test
here runs a real `bash -c 'source <script>; <call>'` subprocess: the real
file is really sourced and the real functions are really invoked with real
stdin/argv, not re-implemented in Python. The only thing ever stubbed is
the external `claude` CLI itself (never invoked for real -- no real
network/API call ever fires); `timeout`, `sleep`, `date`, `python3`, etc.
are the real system binaries, which is safe since they have no side
effects here.
"""
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = REPO_ROOT / "claude-usage-limit-retry.sh"


def _base_env(bin_dir: Path, extra: dict | None = None) -> dict:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(bin_dir),
    }
    if extra:
        env.update(extra)
    return env


def _write_claude_stub(bin_dir: Path, log_file: Path) -> None:
    """A fake `claude` binary. On each invocation it logs its full argv,
    increments a call counter in $CLAUDE_STUB_COUNTER_FILE, and then prints
    the stdout/stderr/exit-code scripted for that call number via
    CLAUDE_STUB_STDOUT_<n> / CLAUDE_STUB_STDERR_<n> / CLAUDE_STUB_EXIT_<n>.
    """
    stub = bin_dir / "claude"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            echo "CALL $*" >> "{log_file}"
            n=0
            if [ -f "$CLAUDE_STUB_COUNTER_FILE" ]; then
              n=$(cat "$CLAUDE_STUB_COUNTER_FILE")
            fi
            n=$((n+1))
            echo "$n" > "$CLAUDE_STUB_COUNTER_FILE"
            stdout_var="CLAUDE_STUB_STDOUT_$n"
            stderr_var="CLAUDE_STUB_STDERR_$n"
            exit_var="CLAUDE_STUB_EXIT_$n"
            printf '%s' "${{!stdout_var}}"
            if [ -n "${{!stderr_var}}" ]; then
              printf '%s' "${{!stderr_var}}" >&2
            fi
            exit "${{!exit_var:-0}}"
            """
        )
    )
    stub.chmod(0o755)


# --- claude_text_signals_usage_limit ----------------------------------------

@pytest.mark.parametrize(
    "text,expected_match",
    [
        ("Claude AI usage limit reached", True),
        ("You've hit your usage limit for today.", True),
        ("Upgrade to Pro or try again later.", True),
        ("5-hour limit reached, resets at 3pm", True),
        ("Five-hour limit reached again", True),
        ("Rate limited by some unrelated upstream system", False),
        # This is credit-accountant's own message shape -- the detector is
        # deliberately narrow so it must NOT misfire on it.
        ("Budget limit reached: $10.00 spent this month", False),
        ("All systems normal, no issues.", False),
    ],
)
def test_claude_text_signals_usage_limit_real_grep_behavior(tmp_path, text, expected_match):
    result = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; claude_text_signals_usage_limit'],
        input=text,
        env=_base_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if expected_match:
        assert result.returncode == 0, f"expected match for {text!r}"
    else:
        assert result.returncode != 0, f"expected NO match for {text!r}"


# --- claude_usage_limit_parse_resume_epoch ----------------------------------

def test_parse_resume_epoch_embedded_unix_epoch_form(tmp_path):
    text = "Claude AI usage limit reached|1735689600"
    result = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; claude_usage_limit_parse_resume_epoch'],
        input=text,
        env=_base_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "1735689600"


def test_parse_resume_epoch_embedded_millisecond_epoch_form(tmp_path):
    text = "Claude AI usage limit reached|1735689600000"
    result = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; claude_usage_limit_parse_resume_epoch'],
        input=text,
        env=_base_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == "1735689600"


def test_parse_resume_epoch_human_clock_time_form(tmp_path):
    env = _base_env(tmp_path, {"TZ": "UTC"})
    text = "You've hit your usage limit. Try again at 3:00pm."
    before = int(time.time())
    result = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; claude_usage_limit_parse_resume_epoch'],
        input=text,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    after = int(time.time())
    out = result.stdout.strip()
    assert out.isdigit(), out
    epoch = int(out)
    # Script must always resolve to a real future instant, never <= now.
    assert epoch > before
    assert epoch <= after + 24 * 3600
    parsed = time.gmtime(epoch)
    assert parsed.tm_hour == 15
    assert parsed.tm_min == 0


def test_parse_resume_epoch_no_recognizable_time_prints_nothing(tmp_path):
    text = "Everything is fine, no limits mentioned here at all."
    result = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; claude_usage_limit_parse_resume_epoch'],
        input=text,
        env=_base_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.strip() == ""


# --- run_claude_usage_limit_retry -------------------------------------------

def test_run_claude_usage_limit_retry_success_on_first_attempt(tmp_path):
    claude_log = tmp_path / "claude_calls.log"
    _write_claude_stub(tmp_path, claude_log)
    counter_file = tmp_path / "counter"
    out_file = tmp_path / "out.txt"
    log_file = tmp_path / "log.txt"

    env = _base_env(
        tmp_path,
        {
            "CLAUDE_STUB_COUNTER_FILE": str(counter_file),
            "CLAUDE_STUB_STDOUT_1": "normal claude output\n",
            "CLAUDE_STUB_EXIT_1": "0",
        },
    )
    cmd = f'source "{SCRIPT}"; run_claude_usage_limit_retry "{out_file}" "{log_file}" -- -p hello'
    result = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert out_file.read_text() == "normal claude output\n"
    calls = claude_log.read_text().strip().splitlines()
    assert calls == ["CALL -p hello"], calls


def test_run_claude_usage_limit_retry_recovers_after_one_usage_limit_hit(tmp_path):
    claude_log = tmp_path / "claude_calls.log"
    _write_claude_stub(tmp_path, claude_log)
    counter_file = tmp_path / "counter"
    out_file = tmp_path / "out.txt"
    log_file = tmp_path / "log.txt"

    env = _base_env(
        tmp_path,
        {
            "CLAUDE_STUB_COUNTER_FILE": str(counter_file),
            "CLAUDE_STUB_STDOUT_1": "",
            "CLAUDE_STUB_STDERR_1": "Claude AI usage limit reached. Upgrade to Pro or try again later.\n",
            "CLAUDE_STUB_EXIT_1": "1",
            "CLAUDE_STUB_STDOUT_2": "recovered output\n",
            "CLAUDE_STUB_EXIT_2": "0",
            # Keep the real `sleep` short so the test stays fast, and cap
            # the max wait too as a safety net against any parsing bug
            # accidentally producing a huge wait.
            "CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS": "1",
            "CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS": "3",
        },
    )
    cmd = f'source "{SCRIPT}"; run_claude_usage_limit_retry "{out_file}" "{log_file}" -- -p hello'
    start = time.time()
    result = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=30)
    elapsed = time.time() - start

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert out_file.read_text() == "recovered output\n"
    # It must have really slept through the fallback wait, not just
    # returned immediately.
    assert elapsed >= 1, elapsed

    calls = claude_log.read_text().strip().splitlines()
    assert calls == ["CALL -p hello", "CALL -p hello"], calls

    log_text = log_file.read_text()
    assert "USAGE_LIMIT_RETRY" in log_text
    assert "Detected a Claude Code usage-limit hit (attempt 1" in log_text
    assert "Wait complete, automatically re-invoking claude (attempt 2 of 2)." in log_text


def test_run_claude_usage_limit_retry_gives_up_after_second_hit_same_run(tmp_path):
    claude_log = tmp_path / "claude_calls.log"
    _write_claude_stub(tmp_path, claude_log)
    counter_file = tmp_path / "counter"
    out_file = tmp_path / "out.txt"
    log_file = tmp_path / "log.txt"

    usage_limit_text = "Claude AI usage limit reached. Upgrade to Pro or try again later.\n"
    env = _base_env(
        tmp_path,
        {
            "CLAUDE_STUB_COUNTER_FILE": str(counter_file),
            "CLAUDE_STUB_STDOUT_1": "",
            "CLAUDE_STUB_STDERR_1": usage_limit_text,
            "CLAUDE_STUB_EXIT_1": "1",
            "CLAUDE_STUB_STDOUT_2": "",
            "CLAUDE_STUB_STDERR_2": usage_limit_text,
            "CLAUDE_STUB_EXIT_2": "1",
            "CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS": "1",
            "CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS": "3",
        },
    )
    cmd = f'source "{SCRIPT}"; run_claude_usage_limit_retry "{out_file}" "{log_file}" -- -p hello'
    result = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=30)

    # Second attempt also hit the limit -> function must give up and
    # propagate that attempt's real (non-zero) exit code, NOT loop a 3rd
    # time.
    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    calls = claude_log.read_text().strip().splitlines()
    assert calls == ["CALL -p hello", "CALL -p hello"], calls

    log_text = log_file.read_text()
    assert "not retrying a 2nd time" in log_text


def test_run_claude_usage_limit_retry_honors_per_attempt_timeout_param(tmp_path):
    """Exercises the `run_claude_usage_limit_retry OUT LOG TIMEOUT_SECONDS -- ...`
    form, which wraps the claude invocation in the real `timeout` command.
    """
    claude_log = tmp_path / "claude_calls.log"
    _write_claude_stub(tmp_path, claude_log)
    counter_file = tmp_path / "counter"
    out_file = tmp_path / "out.txt"
    log_file = tmp_path / "log.txt"

    env = _base_env(
        tmp_path,
        {
            "CLAUDE_STUB_COUNTER_FILE": str(counter_file),
            "CLAUDE_STUB_STDOUT_1": "fast output\n",
            "CLAUDE_STUB_EXIT_1": "0",
        },
    )
    cmd = f'source "{SCRIPT}"; run_claude_usage_limit_retry "{out_file}" "{log_file}" 5 -- -p hello'
    result = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert out_file.read_text() == "fast output\n"
    calls = claude_log.read_text().strip().splitlines()
    assert calls == ["CALL -p hello"], calls
