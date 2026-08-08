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


def test_backgrounded_unbounded_find_is_rejected():
    """Real, confirmed bug (2026-08-08, independent tier1 review): the plain
    background operator '&' was missing from _SEGMENT_BREAKS, so a command
    of the shape '<anything> & find /' was folded into one segment whose
    first word was never 'find', bypassing this guard entirely -- verified
    live before the fix, both examples below exited 0 (allow). Regression
    coverage for the fix (adding '&' to _SEGMENT_BREAKS)."""
    result = run_hook("true & find /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr

    result2 = run_hook("cd /opt/veridian & find /")
    assert result2.returncode == 2, f"expected block (rc=2), got rc={result2.returncode}, stderr={result2.stderr}"
    assert "BLOCKED" in result2.stderr


def test_backgrounded_scoped_find_is_still_allowed():
    """The fix must not turn '&' into a blanket rejection -- a backgrounded
    command followed by a real, properly-scoped find must still be
    allowed."""
    result = run_hook("true & find /opt/veridian/scripts -iname 'x'")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


# ---------------------------------------------------------------------------
# Real, confirmed bugs fixed 2026-08-08 (independent tier1 review, round 2):
# `timeout`-wrapped find, find embedded as a string argument to bash -c/
# sh -c/eval, and find forwarded to via xargs all bypassed this guard
# entirely -- verified live before the fix, every one of these exited 0
# (allow).
# ---------------------------------------------------------------------------

def test_timeout_wrapped_unbounded_find_is_rejected():
    result = run_hook("timeout 300 find / -iname pattern")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_timeout_wrapped_scoped_find_is_still_allowed():
    result = run_hook("timeout 300 find /opt/veridian/scripts -iname pattern")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_bash_c_embedded_unbounded_find_is_rejected():
    result = run_hook('bash -c "find / -iname pattern"')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_sh_c_embedded_unbounded_find_is_rejected():
    result = run_hook("sh -c 'find / -iname pattern'")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_eval_embedded_unbounded_find_is_rejected():
    result = run_hook('eval "find / -iname pattern"')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_bash_c_embedded_scoped_find_is_still_allowed():
    result = run_hook('bash -c "find /opt/veridian/scripts -iname x"')
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_xargs_forwarded_unbounded_find_is_rejected():
    result = run_hook("xargs find /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_piped_xargs_forwarded_unbounded_find_is_rejected():
    result = run_hook("echo x | xargs find /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


# ---------------------------------------------------------------------------
# Real, confirmed bugs fixed 2026-08-08 (independent tier1 review, round 3):
# absolute/relative-path find invocations, and a nested unbounded find
# embedded via -exec/-execdir in an otherwise-properly-scoped outer find,
# both bypassed this guard entirely -- verified live before the fix, every
# one of these exited 0 (allow).
# ---------------------------------------------------------------------------

def test_absolute_path_find_invocation_is_rejected():
    result = run_hook("/usr/bin/find / -iname x")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_bin_find_absolute_path_is_rejected():
    result = run_hook("/bin/find /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_relative_path_find_invocation_is_rejected():
    result = run_hook("./find /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_absolute_path_find_scoped_is_still_allowed():
    result = run_hook("/usr/bin/find /opt/veridian/scripts -iname x")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_nested_exec_find_unbounded_is_rejected():
    result = run_hook(r"find /opt/veridian -exec find / -iname '*secret*' \;")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_nested_execdir_find_unbounded_is_rejected():
    result = run_hook(r"find /opt/veridian -execdir find / -iname '*secret*' \;")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_nested_exec_sh_c_embedded_find_unbounded_is_rejected():
    result = run_hook(r"find /opt/veridian -exec sh -c 'find / -iname x' \;")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_nested_exec_scoped_find_is_still_allowed():
    result = run_hook(r"find /opt/veridian -exec find /opt/veridian/scripts -iname x \;")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_nested_exec_non_find_command_is_still_allowed():
    result = run_hook(r"find /opt/veridian -exec echo {} \;")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


# ---------------------------------------------------------------------------
# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, round 4):
# wrapper commands (sudo/nice/ionice/env) can take real value-bearing flags
# (sudo -u USER, nice -n N, env -u VAR) that the original flag-skip loop
# assumed didn't exist, desyncing the parser and falling through to allow.
# Redesigned to a fail-closed model: known value flags are skipped with
# their value, any other flag is Unclassifiable (reject), closing the whole
# bug class rather than one more instance of it.
# ---------------------------------------------------------------------------

def test_sudo_value_flag_wrapped_unbounded_find_is_rejected():
    result = run_hook("sudo -u root find / -iname secret")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_nice_value_flag_wrapped_unbounded_find_is_rejected():
    result = run_hook("nice -n 19 find / -iname secret")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_env_value_flag_wrapped_unbounded_find_is_rejected():
    result = run_hook("env -u FOO find / -iname secret")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_sudo_value_flag_wrapped_scoped_find_is_still_allowed():
    result = run_hook("sudo -u root find /opt/veridian/scripts -iname secret")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_nice_value_flag_wrapped_scoped_find_is_still_allowed():
    result = run_hook("nice -n 19 find /opt/veridian/scripts -iname secret")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_env_value_flag_wrapped_scoped_find_is_still_allowed():
    result = run_hook("env -u FOO find /opt/veridian/scripts -iname secret")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_unrecognized_wrapper_flag_fails_closed():
    """The fail-closed redesign's whole point: an unrecognized flag on a
    known wrapper must reject, not silently assume value-less and allow."""
    result = run_hook("sudo --unknown-flag find / -iname x")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


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


# ---------------------------------------------------------------------------
# Real, confirmed bugs fixed 2026-08-08 (independent tier1 review, round 5):
# (1) backtick/$(...) command substitution around a find invocation was
# invisible to this guard -- the substitution's backtick glued to the
# adjacent word token, never comparing equal to "find" after basename
# normalization; (2) the raw-text fast-path pre-filter skipped tokenization
# entirely whenever the raw command string didn't contain a contiguous
# "find" substring, so quote-fragment ('f'ind) and backslash-escape
# (f\ind) tricks that shlex would correctly reduce to the word "find"
# bypassed the guard before tokenization ever ran. Both verified live
# before the fix, every one of these exited 0 (allow).
# ---------------------------------------------------------------------------

def test_backtick_substitution_unbounded_find_is_rejected():
    result = run_hook("`find / -iname secret`")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_backtick_substitution_assigned_unbounded_find_is_rejected():
    result = run_hook("R=`find / -iname secret`; echo $R")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_dollar_paren_substitution_unbounded_find_is_rejected():
    result = run_hook("R=$(find / -iname secret); echo $R")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_backtick_substitution_scoped_find_is_still_allowed():
    result = run_hook("`find /opt/veridian/scripts -iname secret`")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_backtick_substitution_non_find_is_still_allowed():
    result = run_hook("echo `date`")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_quote_fragment_unbounded_find_is_rejected():
    """The fast-path pre-filter (raw-text 'find' substring check) used to
    skip tokenization entirely here, allowing this through outright."""
    result = run_hook("'f'ind /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_backslash_escaped_unbounded_find_is_rejected():
    result = run_hook(r"f\ind /")
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_quote_fragment_scoped_find_is_still_allowed():
    result = run_hook("'f'ind /opt/veridian/scripts")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


# ---------------------------------------------------------------------------
# Real, confirmed bugs fixed 2026-08-08 (independent tier1 review, round 6,
# the sixth consecutive round of finding a real bypass in this one file):
# a bare shell interpreter invocation with no -c and no real positional
# script-file argument reads its commands from stdin by default -- piped
# input, process substitution, and herestrings/heredocs are all common,
# non-adversarial ways to feed one a command. Rather than patch each shape
# individually (as the prior five rounds did for other bypass classes),
# this redesigns the whole check to fail closed by default on any bare
# shell-interpreter invocation this guard cannot positively verify.
# ---------------------------------------------------------------------------

def test_piped_stdin_unbounded_find_via_bash_is_rejected():
    result = run_hook('echo "find / -iname secret" | bash')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_piped_stdin_unbounded_find_via_sh_is_rejected():
    result = run_hook('echo "find / -iname secret" | sh')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_process_substitution_unbounded_find_via_bash_is_rejected():
    result = run_hook('bash <(echo "find / -iname secret")')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_herestring_unbounded_find_via_bash_is_rejected():
    result = run_hook('bash <<< "find / -iname secret"')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"
    assert "BLOCKED" in result.stderr


def test_herestring_scoped_find_via_bash_is_still_allowed():
    result = run_hook('bash <<< "find /opt/veridian/scripts -iname x"')
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_piped_stdin_bash_is_rejected_even_when_content_would_be_scoped():
    """A bare bash fed via pipe is unclassifiable under the fail-closed
    redesign regardless of whether the piped content would itself have
    been safe -- this guard cannot verify pipe-adjacency content in
    general, and accepts this as the real cost of closing the whole
    vulnerability class rather than the next narrow patch."""
    result = run_hook('echo "find /opt/veridian/scripts -iname x" | bash')
    assert result.returncode == 2, f"expected block (rc=2), got rc={result.returncode}, stderr={result.stderr}"


def test_bash_with_real_script_file_argument_is_still_allowed():
    result = run_hook("bash /opt/veridian/scripts/some_real_script.sh")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_bash_c_with_scoped_find_is_still_allowed():
    result = run_hook('bash -c "find /opt/veridian/scripts -iname x"')
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"


def test_non_find_pipe_is_still_allowed():
    result = run_hook("echo hello | cat")
    assert result.returncode == 0, f"expected allow (rc=0), got rc={result.returncode}, stderr={result.stderr}"
