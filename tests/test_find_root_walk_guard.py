#!/usr/bin/env python3
"""UMR-20260806-121825-8ece (governing UMR-20260806-071025-1d28, PM decision
row 56): real tests for hooks/find_root_walk_guard.py, the Claude Code
PreToolUse hook that blocks unbounded `find /` root-filesystem walks before
they execute.

Invokes the hook exactly the way Claude Code's hook runner does: JSON payload
on stdin, verdict read from (exit code, stderr).
"""
import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(SCRIPTS_DIR, "hooks", "find_root_walk_guard.py")


def run_hook(command, cwd="/opt/veridian/scripts", tool_name="Bash"):
    payload = {
        "session_id": "test-session",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
    }
    result = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result


# ---------------------------------------------------------------------------
# Core required cases: unbounded root rejected, subtree-scoped allowed,
# fail-closed unclassifiable rejected.
# ---------------------------------------------------------------------------

def test_unbounded_root_find_is_rejected():
    result = run_hook("find / -iname '*gtm_certification_categories*'")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr
    assert "/opt/veridian/scripts/superboss-register.py" in result.stderr
    assert "lookup-capability" in result.stderr


def test_subtree_scoped_find_is_allowed():
    result = run_hook("find /opt/veridian -iname 'resource_governor.py'")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"
    assert result.stderr == ""


def test_unclassifiable_variable_root_is_rejected_not_allowed():
    """Fail-closed: a find whose root is a shell variable can't be resolved
    at hook time (we don't know what it expands to) -- must reject, not
    silently allow."""
    result = run_hook('find $SEARCH_ROOT -iname "*.py"')
    assert result.returncode == 2, f"expected fail-closed block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr
    assert "could not be confidently classified" in result.stderr


# ---------------------------------------------------------------------------
# Matches the real incident command lines from PM decision row 56 verbatim.
# ---------------------------------------------------------------------------

def test_real_incident_command_superboss_register_lookup_is_rejected():
    result = run_hook("find / -iname superboss-register.py")
    assert result.returncode == 2


def test_real_incident_command_with_xdev_is_still_rejected():
    """-xdev only prevents crossing mount points; the root filesystem mount
    itself is still ~247GB and unbounded, so this must still be blocked."""
    result = run_hook("find / -xdev -iname resource_governor.py")
    assert result.returncode == 2


def test_correctly_scoped_ai_os_tasks_find_is_allowed():
    """The one find from the real incident that was left running untouched
    because it was correctly scoped, not rooted at /."""
    result = run_hook("find /opt/veridian/ai-os/tasks -maxdepth 3 -iname 'task.yaml'")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Root-equivalence and evasion coverage.
# ---------------------------------------------------------------------------

def test_trailing_slash_root_is_rejected():
    result = run_hook("find / -iname 'x'".replace("find /", "find //"))
    assert result.returncode == 2


def test_root_glob_star_is_rejected():
    result = run_hook("find /* -iname 'x'")
    assert result.returncode == 2


def test_sudo_prefixed_unbounded_find_is_rejected():
    result = run_hook("sudo find / -iname 'x'")
    assert result.returncode == 2


def test_piped_unbounded_find_is_rejected():
    result = run_hook("find / -type f -iname 'x' | xargs grep foo")
    assert result.returncode == 2


def test_chained_unbounded_find_is_rejected():
    result = run_hook("cd /tmp && find / -iname 'x'")
    assert result.returncode == 2


def test_multiple_roots_one_unbounded_is_rejected():
    result = run_hook("find /opt/veridian / -iname 'x'")
    assert result.returncode == 2


def test_no_path_argument_defaults_to_cwd_root_is_rejected():
    result = run_hook("find -iname 'x'", cwd="/")
    assert result.returncode == 2


def test_no_path_argument_defaults_to_cwd_bounded_is_allowed():
    result = run_hook("find -iname 'x'", cwd="/opt/veridian/scripts")
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# False-positive avoidance: this guard's mandate is `find` invocations only.
# ---------------------------------------------------------------------------

def test_non_bash_tool_is_allowed_untouched():
    result = run_hook("find / -iname 'x'", tool_name="Read")
    assert result.returncode == 0


def test_command_without_find_word_is_allowed():
    result = run_hook("grep -rn 'pattern' /opt/veridian/scripts")
    assert result.returncode == 0


def test_find_word_as_plain_argument_not_invocation_is_allowed():
    """'find' appearing as a grep search term (not the find command itself)
    must not be treated as a find invocation."""
    result = run_hook("grep -rn find /opt/veridian/scripts/README.md")
    assert result.returncode == 0


def test_find_code_helper_script_itself_is_unaffected():
    result = run_hook("/opt/veridian/scripts/find_code.sh 'PATTERN' /opt/veridian/scripts")
    assert result.returncode == 0


def test_empty_command_is_allowed():
    result = run_hook("")
    assert result.returncode == 0
