#!/usr/bin/env python3
"""hooks/pretooluse_worker_enforcement.py -- Claude Code PreToolUse hook
(matcher: "*", i.e. every tool).

WHY THIS EXISTS (task-20260814-132651-add-pretooluse-hook-enforcement-layer-fo,
UMR-20260814-131747-420e): the standing hard rules given to every dispatched
AI worker -- stay inside your assigned repo+branch, never bypass the queue
mechanism (dispatch-owner-task.sh -> superboss-register.py/resource_governor.py
-> a real `veridian-worker@*.service`, see resource_governor.py's
`_perform_spawn()`/`next_queued_task()`), never invoke a frozen/blocked
script directly -- used to live ONLY as prose inside each worker's own prompt
text. Nothing mechanically stopped a worker that ignored its own
instructions (confused, not necessarily adversarial -- e.g. a worker that
loses track of which worktree it's in after a `cd`, or that "helpfully"
relays a message with a raw `tmux send-keys` instead of going through
dispatch-owner-task.sh). This hook is that mechanical stop: it runs BEFORE
the tool call executes and can deny it outright (exit 2), independent of
whatever the worker's own system prompt says.

This hook does not weaken, replace, or run instead of any existing
PreToolUse hook (`snip hook`, `find_root_walk_guard.py`) -- it is registered
as an *additional* hook entry (this one under a "*" / all-tools matcher, so
it also sees Write/Edit/NotebookEdit calls that the Bash-only matcher used
by the other two never sees). Claude Code runs every hook for a matching
event and applies the most restrictive decision, so this adds a
strictly-narrowing check without touching the existing gates.

WHAT THIS DOES, five real, mechanical checks:

  1. Blocks a `git commit`/`git push` (Bash tool) whose EFFECTIVE target repo
     (resolved through any `git -C <path>` / `git --git-dir=... -C` flag, or
     the payload's own `cwd` if no `-C` is given) is not the calling
     worker's own assigned workspace, or whose current branch (or, for
     `push`, target branch) is not that worker's own assigned branch. Also
     blocks a `Write`/`Edit`/`NotebookEdit` tool call whose target file path
     resolves outside that same assigned workspace. A git global flag that
     takes a separate value argument (e.g. `--git-dir /other/path`, `-c
     x=y`, `--work-tree <dir>`) has that value token consumed along with the
     flag itself, so it is never mistaken for the subcommand -- see
     `_git_invocation`'s own docstring for the exact bypass this used to
     allow.

  2. Extends that same outside-workspace-write block to real Bash-based
     file-write patterns beyond git/Write/Edit -- shell redirection (`>`,
     `>>`) and `curl -o`/`wget -O` -- so `curl https://... -o
     /other/path/file` or `echo x > /other/path/file` are caught too, not
     just the tool-call-shaped writes. See `check_bash_file_write`.

  3. Blocks a raw `tmux ... send-keys` invocation (Bash tool) from inside a
     worker session -- the real, documented dispatch relay is
     dispatch-owner-task.sh; a worker itself issuing send-keys is
     impersonating that relay, not performing its own assigned work (see
     dispatch-owner-task.sh's own header: "there is no separate 'raw tmux
     send-keys' step left to accidentally use instead of this script").
     Also blocks direct invocation of a small, explicit set of
     queue-owning/frozen scripts (_BLOCKED_SCRIPT_BASENAMES below) -- through
     a plain interpreter (`python3`/`python`/`bash`/`sh`) or through
     `env`/`xargs`/`nohup`/`setsid` wrapping one of those, in any
     combination (e.g. `env python3 dispatch_core.py`) -- and a direct
     `systemctl ... start|enable|restart veridian-worker@...` -- the only
     real, mechanical spawn path is resource_governor.py's
     `_perform_spawn()`, which already holds `dispatch_core.acquire_dispatch_lock()`
     and respects CONCURRENCY_CAP; a worker calling systemctl on another
     worker unit directly bypasses both (see systemd/veridian-worker@.service's
     own 2026-08-01 comment: 24 units launched this way at once OOM-killed
     the box).

  4. Blocks a worker session's Bash `cp`/`mv`/`install`/`tee`/`rsync`/`dd`/
     `sed` invocation that names the shared Claude Code settings file
     (`~/.claude/settings.json` -- the very file this hook is itself wired
     into) as a source or destination. This exists because this hook's own
     initial rollout (task-20260814-132651) wrote that file directly, mid
     task, before its diff was ever reviewed or merged -- a real,
     unauthorized-live-deployment incident (UMR-20260814-131747-420e's
     PR#375 audit finding #4). Changing what's wired into it must land as a
     reviewed, merged, separately-dispatched deploy step from here on, never
     a worker self-deploying mid-task. (Redirection/curl writes to that same
     path are already caught by check 2, which is path-generic.)

  5. Appends EVERY tool call this hook observes -- allowed or denied -- to a
     queryable sqlite audit log (see AUDIT_DB below), independent of the
     allow/deny decision. Logging failures never block a real tool call
     (best-effort, fail-open on the logging path only -- the enforcement
     decision itself is computed first, and does not depend on logging
     succeeding).

HOW A CALLING SESSION IS IDENTIFIED AS "A WORKER FOR TASK X": primarily via
this process's own cgroup membership (`/proc/self/cgroup`), which contains
the real systemd unit name `veridian-worker@<task_id>.service` for any
process descended from that unit -- this is kernel-enforced, not something
an unprivileged worker process can spoof by unsetting an env var or `cd`-ing
elsewhere (confirmed live: this hook's own module docstring development
process observed exactly this line in its own `/proc/self/cgroup` while
running as this task's real worker). Falls back to the `VERIDIAN_TASK_ID`
env var (set by worker-entrypoint.sh, for non-cgroup/test environments),
then to the payload's own `cwd` resolving under
`/opt/veridian/ai-os/tasks/<task_id>/workspace` -- in that order. If none of
these resolve, this is not a recognized worker session (e.g. an interactive
operator session): check 3 still logs it (task_id=NULL), checks 1/2 do not
apply (nothing to enforce against -- this hook's whole mandate is dispatched
*worker* execution, not general-purpose shell use on the box).

FAIL-CLOSED POLICY for check 1 specifically: if a worker session IS
identified (cgroup/env/cwd resolved a task_id) but that task's own
`task.yaml` cannot be read/parsed (so its assigned repo/branch are unknown),
every git commit/push and every Write/Edit/NotebookEdit call is REJECTED
until it can be read again -- unclassifiable is treated as unsafe, not
allowed, matching this repo's existing fail-closed hook (find_root_walk_guard.py).
"""
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a real, standing dependency
    # of this whole codebase (worker-entrypoint.sh itself shells out to
    # `python3 -c "import yaml; ..."` to read task.yaml); if it's ever
    # missing, assignment resolution simply fails closed below like any
    # other unreadable task.yaml.
    yaml = None

TASKS_ROOT = "/opt/veridian/ai-os/tasks"
AUDIT_DB = os.environ.get(
    "VERIDIAN_WORKER_AUDIT_DB", "/opt/veridian/ai-os/logs/pretooluse_worker_audit.sqlite"
)

# --- queue-bypass block list (check 2) --------------------------------------
# Real, explicit, intentionally small -- see module docstring. dispatch_core.py
# is additionally under its own real 2026-08-08 stop-work order (never edited
# directly by any task); blocking WORKERS from *invoking* it directly is a
# separate, real concern (it owns the shared dispatch lock/CONCURRENCY_CAP,
# see resource_governor.py's _perform_spawn()/acquire_dispatch_lock()) and
# does not touch that file at all.
_BLOCKED_SCRIPT_BASENAMES = {"dispatch_core.py", "dispatch-tick.py"}
_SYSTEMCTL_WORKER_ACTION_RE = re.compile(r"\bveridian-worker@")
_SYSTEMCTL_ACTIONS = {"start", "enable", "restart", "reset-failed"}

_WORKSPACE_CWD_RE = re.compile(r"^/opt/veridian/ai-os/tasks/([^/]+)/workspace(?:/.*)?$")
_CGROUP_UNIT_RE = re.compile(r"veridian-worker@([^/]+)\.service")

_WRITE_TOOL_PATH_KEYS = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}

_GIT_WRITE_SUBCOMMANDS = {"commit", "push"}

# git global flags that take a SEPARATE value token (space-separated form,
# as opposed to `--flag=value`). Each of these must consume its value token
# too, or that value gets mistaken for the git subcommand -- see
# `_git_invocation`'s own docstring for the exact real bypass this caused
# (PR#375 audit finding #1): `git --git-dir /other/path commit` used to
# treat "/other/path" as the subcommand, miss "commit" entirely, and fall
# through check_git_write's `subcommand not in _GIT_WRITE_SUBCOMMANDS` guard
# to an unconditional allow -- completely skipping repo/branch enforcement.
_GIT_FLAGS_WITH_SEPARATE_VALUE = {"-C", "--git-dir", "--work-tree", "--namespace", "-c", "--exec-path"}
# Same flags' `--flag=value` / `-C=value` form (no separate token to skip).
_GIT_FLAG_EQUALS_PREFIXES = tuple(f + "=" for f in _GIT_FLAGS_WITH_SEPARATE_VALUE)

# Bash-level "wrapper" commands that can precede the real interpreter/script
# invocation without changing what actually runs -- e.g. `env python3
# dispatch_core.py`, `nohup python3 dispatch_core.py &`, `xargs -I{} python3
# dispatch_core.py`. See `_blocked_script_invocation` (PR#375 audit finding
# #3: the old allowed-wrapper set {python3, python, bash, sh} missed these,
# so `env python3 dispatch_core.py` bypassed the queue-bypass block).
_INTERPRETER_WRAPPERS = {"python3", "python", "bash", "sh"}
_ENV_STYLE_WRAPPERS = {"env", "xargs", "nohup", "setsid"}
_ALL_WRAPPER_COMMANDS = _INTERPRETER_WRAPPERS | _ENV_STYLE_WRAPPERS

# Bash file-mutation commands (beyond redirection/curl/wget, which are
# path-generic -- see check_bash_file_write) that name a target path
# directly as an argument rather than via `>`/`-o`. Used only by the
# protected-shared-settings guard (check_shared_settings_write) below.
_FILE_MUTATION_COMMANDS = {"cp", "mv", "install", "tee", "rsync", "dd", "truncate", "sed"}

# The one real, shared file this hook itself is wired into -- see check
# 4 in the module docstring / check_shared_settings_write.
_PROTECTED_SHARED_PATHS = {os.path.normpath(os.path.expanduser("~/.claude/settings.json"))}
_CLAUDE_SETTINGS_SUFFIX_RE = re.compile(r"(?:^|/)\.claude/settings\.json$")

_REDIRECT_OPS = {">", ">>"}
_CURL_OUTPUT_FLAGS = {"-o", "--output"}
_WGET_OUTPUT_FLAGS = {"-O", "--output-document"}
_BENIGN_WRITE_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}


# ---------------------------------------------------------------------------
# Assignment resolution
# ---------------------------------------------------------------------------
def _task_id_from_cgroup():
    try:
        with open("/proc/self/cgroup", "r") as f:
            content = f.read()
    except OSError:
        return None
    m = _CGROUP_UNIT_RE.search(content)
    return m.group(1) if m else None


def _task_id_from_cwd(cwd):
    if not cwd:
        return None
    m = _WORKSPACE_CWD_RE.match(os.path.normpath(cwd))
    return m.group(1) if m else None


def resolve_task_id(cwd):
    """Primary: real cgroup membership (kernel-enforced, not spoofable by an
    unprivileged worker process). Fallback: VERIDIAN_TASK_ID env var.
    Fallback: cwd resolving under this task's own workspace path. Returns
    None if none of these identify a real dispatched worker session."""
    return (
        _task_id_from_cgroup()
        or os.environ.get("VERIDIAN_TASK_ID")
        or _task_id_from_cwd(cwd)
        or None
    )


def load_assignment(task_id):
    """Returns a dict with task_dir/workspace/repo/branch for `task_id`, or
    None if task_id itself is falsy. `repo`/`branch`/`workspace` are None
    (not raised) if task.yaml could not be read/parsed -- callers must treat
    that as "unknown, fail closed", not as "no assignment"."""
    if not task_id:
        return None
    task_dir = os.path.join(TASKS_ROOT, task_id)
    workspace = os.path.join(task_dir, "workspace")
    repo = branch = None
    readable = False
    if yaml is not None:
        try:
            with open(os.path.join(task_dir, "task.yaml"), "r") as f:
                data = yaml.safe_load(f) or {}
            workspace = data.get("workspace") or workspace
            repo = data.get("repo")
            branch = data.get("branch")
            readable = True
        except (OSError, yaml.YAMLError):
            readable = False
    return {
        "task_id": task_id,
        "task_dir": task_dir,
        "workspace": os.path.normpath(workspace),
        "repo": repo,
        "branch": branch,
        "readable": readable,
    }


def _under_workspace(path, workspace):
    """True iff normalized absolute `path` is `workspace` itself or a real
    subpath of it."""
    p = os.path.normpath(path)
    w = os.path.normpath(workspace)
    return p == w or p.startswith(w + os.sep)


# ---------------------------------------------------------------------------
# Shell command parsing helpers (deliberately simpler than
# hooks/find_root_walk_guard.py's own adversarial-hardened parser -- this
# hook's real threat model, per its own docstring, is a confused worker
# following its own prompt text incorrectly, not an adversary actively
# obfuscating shell syntax against this guard specifically).
# ---------------------------------------------------------------------------
def _tokenize(line):
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = []
        while True:
            tok = lexer.get_token()
            if tok is None:
                break
            tokens.append(tok)
        return tokens
    except ValueError:
        return None  # does not tokenize cleanly -- caller decides fail-open/closed


_SEGMENT_BREAKS = {";", "&", "&&", "||", "|"}


def _split_segments(tokens):
    segments, current = [], []
    for tok in tokens:
        if tok in _SEGMENT_BREAKS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _cmd_name(token):
    return os.path.basename(token)


# ---------------------------------------------------------------------------
# Check 1: git write scope
# ---------------------------------------------------------------------------
def _git_invocation(segment):
    """If `segment` invokes `git` (optionally through `python3`/`env`-style
    var assignments), returns (repo_override_or_None, subcommand_or_None,
    rest_argv). Returns None if this segment does not invoke git at all.

    PR#375 audit finding #1 (fixed here): a git global flag that takes a
    SEPARATE value token -- `--git-dir /other/path`, `-c x=y`, `--work-tree
    <dir>`, etc, as opposed to the `--flag=value` form -- must consume that
    value token along with the flag itself. The old version only did this
    for `-C`, so e.g. `git --git-dir /other/path commit` walked past
    `--git-dir` (matched the generic `tok.startswith("-")` skip, `j += 1`)
    but left `/other/path` unconsumed; the next loop iteration then read
    `/other/path` as `subcommand`, which doesn't match 'commit'/'push', so
    check_git_write's `subcommand not in _GIT_WRITE_SUBCOMMANDS` guard
    returned ('allow', None) immediately -- a real `git --git-dir
    /other/path commit` was never even inspected, let alone scope-checked.
    See test_git_dash_git_dir_space_form_value_not_mistaken_for_subcommand
    for the exact regression case from the audit."""
    i, n = 0, len(segment)
    while i < n and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", segment[i]):
        i += 1
    if i >= n or _cmd_name(segment[i]) != "git":
        return None
    argv = segment[i + 1:]
    repo_override = None
    j = 0
    subcommand = None
    while j < len(argv):
        tok = argv[j]
        if tok in ("-C", "--git-dir") and j + 1 < len(argv):
            repo_override = argv[j + 1]
            j += 2
            continue
        if tok.startswith("--git-dir=") or tok.startswith("-C="):
            repo_override = tok.split("=", 1)[1]
            j += 1
            continue
        if tok in _GIT_FLAGS_WITH_SEPARATE_VALUE and j + 1 < len(argv):
            # Value-taking flag we don't otherwise care about (-c, --work-tree,
            # --namespace, --exec-path) -- still must consume its value token
            # so it is never mistaken for the subcommand.
            j += 2
            continue
        if tok.startswith(_GIT_FLAG_EQUALS_PREFIXES):
            j += 1
            continue
        if tok.startswith("-"):
            j += 1
            continue
        subcommand = tok
        rest = argv[j + 1:]
        return repo_override, subcommand, rest
    return repo_override, None, []


def _resolve_repo_path(repo_override, cwd):
    if repo_override:
        return os.path.normpath(repo_override if os.path.isabs(repo_override) else os.path.join(cwd or "/", repo_override))
    return os.path.normpath(cwd or "/")


def _current_branch(repo_path):
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _push_target_branch(rest_argv, current_branch):
    """Best-effort: the branch `git push` will actually update. A bare
    `git push` / `git push origin` pushes the current branch; an explicit
    refspec `<local>:<remote>` or `<branch>` names the branch directly."""
    positional = [t for t in rest_argv if not t.startswith("-")]
    # positional[0] is the remote (if any); positional[1] is the refspec (if any)
    if len(positional) >= 2:
        refspec = positional[1]
        return refspec.split(":")[-1].removeprefix("refs/heads/")
    return current_branch


def check_git_write(segment, cwd, assignment):
    """Returns (verdict, reason) -- verdict 'allow' or 'deny'. Only fires for
    a real git commit/push invocation; anything else returns ('allow', None)
    immediately (out of scope for this check)."""
    parsed = _git_invocation(segment)
    if parsed is None:
        return "allow", None
    repo_override, subcommand, rest = parsed
    if subcommand not in _GIT_WRITE_SUBCOMMANDS:
        return "allow", None

    if assignment is None:
        # Not a recognized worker session at all -- nothing to enforce.
        return "allow", None
    if not assignment["readable"]:
        return "deny", (
            f"this task's own task.yaml could not be read, so its assigned repo/branch "
            f"are unknown -- fail-closed: git {subcommand} rejected until it can be verified"
        )

    repo_path = _resolve_repo_path(repo_override, cwd)
    if not _under_workspace(repo_path, assignment["workspace"]):
        return "deny", (
            f"git {subcommand} targets {repo_path!r}, which is outside this worker's own "
            f"assigned workspace ({assignment['workspace']!r}) -- workers may only commit/push "
            f"inside their own assigned checkout"
        )

    branch = _current_branch(repo_path)
    if branch is None:
        return "deny", (
            f"could not determine the current branch of {repo_path!r} to verify it matches "
            f"this worker's assigned branch ({assignment['branch']!r}) -- fail-closed"
        )
    if subcommand == "push":
        branch = _push_target_branch(rest, branch)
    if assignment["branch"] and branch != assignment["branch"]:
        return "deny", (
            f"git {subcommand} targets branch {branch!r}, but this worker is assigned to "
            f"branch {assignment['branch']!r} -- workers may only commit/push their own "
            f"assigned branch"
        )
    return "allow", None


def check_write_tool_scope(tool_name, tool_input, cwd, assignment):
    key = _WRITE_TOOL_PATH_KEYS.get(tool_name)
    if key is None:
        return "allow", None
    path = tool_input.get(key)
    if not path:
        return "allow", None
    if assignment is None:
        return "allow", None
    if not assignment["readable"]:
        return "deny", (
            f"this task's own task.yaml could not be read, so its assigned workspace is "
            f"unknown -- fail-closed: {tool_name} rejected until it can be verified"
        )
    resolved = os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd or "/", path))
    if not _under_workspace(resolved, assignment["workspace"]):
        return "deny", (
            f"{tool_name} targets {resolved!r}, which is outside this worker's own assigned "
            f"workspace ({assignment['workspace']!r})"
        )
    return "allow", None


# ---------------------------------------------------------------------------
# Check 1b (PR#375 audit finding #2): real Bash-based file-write patterns
# beyond git commit/push and Write/Edit/NotebookEdit -- shell redirection
# and curl/wget -o. The module's own motivating scenario ("a worker that
# 'helpfully' relays..." etc) was never limited to git or the Claude Code
# write tools; a worker can write a real file to disk with a plain `>` or a
# `curl -o` just as easily, and neither was inspected at all before this.
# ---------------------------------------------------------------------------
def _redirect_target(segment):
    """Returns the destination path token for a `>` / `>>` shell redirection
    in `segment`, else None. Best-effort: a fd-duplication target like
    `2>&1` (destination token starts with `&`) is skipped, not resolved as
    a file path -- this hook is deliberately simpler than
    find_root_walk_guard.py's adversarial-hardened parser (see module
    docstring)."""
    for i, tok in enumerate(segment):
        if tok in _REDIRECT_OPS and i + 1 < len(segment):
            dest = segment[i + 1]
            if dest.startswith("&"):
                continue
            return dest
    return None


def _curl_wget_output_path(segment):
    """Returns the output-file path if `segment` invokes `curl -o`/
    `--output <path>` or `wget -O`/`--output-document <path>` (space- or
    `=`-separated form), else None."""
    tokens = [t for t in segment if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", t)]
    if not tokens:
        return None
    cmd = _cmd_name(tokens[0])
    if cmd == "curl":
        flags = _CURL_OUTPUT_FLAGS
    elif cmd == "wget":
        flags = _WGET_OUTPUT_FLAGS
    else:
        return None
    for i, tok in enumerate(tokens):
        if tok in flags and i + 1 < len(tokens):
            return tokens[i + 1]
        for f in flags:
            if tok.startswith(f + "="):
                return tok.split("=", 1)[1]
    return None


def check_bash_file_write(segment, cwd, assignment):
    """Extends the outside-workspace-write block (see check_write_tool_scope)
    to shell redirection (`>`, `>>`) and `curl -o`/`wget -O` -- so
    `curl https://... -o /other/path/file` or `echo x > /other/path/file`
    are caught too (PR#375 audit finding #2), not just git and the Claude
    Code Write/Edit/NotebookEdit tools."""
    if assignment is None:
        return "allow", None
    if not assignment["readable"]:
        return "deny", (
            "this task's own task.yaml could not be read, so its assigned workspace is "
            "unknown -- fail-closed: this file write is rejected until it can be verified"
        )
    for dest in filter(None, (_redirect_target(segment), _curl_wget_output_path(segment))):
        if dest in _BENIGN_WRITE_TARGETS:
            continue
        resolved = os.path.normpath(dest if os.path.isabs(dest) else os.path.join(cwd or "/", dest))
        if not _under_workspace(resolved, assignment["workspace"]):
            return "deny", (
                f"this command writes to {resolved!r}, which is outside this worker's own "
                f"assigned workspace ({assignment['workspace']!r})"
            )
    return "allow", None


# ---------------------------------------------------------------------------
# Check 2: raw tmux send-keys / queue-bypassing script invocation
# ---------------------------------------------------------------------------
def _blocked_script_invocation(segment):
    """Returns the matched basename if `segment` directly invokes one of
    _BLOCKED_SCRIPT_BASENAMES, else None. Sees through any chain of
    interpreter (`python3`/`python`/`bash`/`sh`) and/or `env`/`xargs`/
    `nohup`/`setsid` wrappers, and each wrapper's own leading flags/
    `NAME=value` assignments -- e.g. `env python3 dispatch_core.py`,
    `nohup python3 dispatch_core.py &`, `xargs -I{} python3
    dispatch_core.py`, `env FOO=bar python3 dispatch_core.py`.

    PR#375 audit finding #3 (fixed here): the old version only skipped a
    single leading `python3`/`python`/`bash`/`sh` token at index 0, so
    `env python3 dispatch_core.py` left `env` unrecognized, checked `env`
    itself (not a blocked basename) against _BLOCKED_SCRIPT_BASENAMES, and
    allowed it -- a real, working bypass of the queue-owning-script block."""
    tokens = [t for t in segment if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", t)]
    if not tokens:
        return None
    idx = 0
    saw_wrapper = True
    while saw_wrapper and idx < len(tokens):
        saw_wrapper = False
        if _cmd_name(tokens[idx]) in _ALL_WRAPPER_COMMANDS and idx + 1 < len(tokens):
            idx += 1
            # Skip the wrapper's own flags (e.g. `env -i`, `xargs -I{}`) and
            # any `NAME=value` assignments (e.g. `env FOO=bar ...`) that
            # precede the actual wrapped command.
            while idx < len(tokens) and (
                tokens[idx].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[idx])
            ):
                idx += 1
            saw_wrapper = True
    if idx < len(tokens):
        base = _cmd_name(tokens[idx])
        if base in _BLOCKED_SCRIPT_BASENAMES:
            return base
    return None


def _tmux_send_keys(segment):
    if not segment or _cmd_name(segment[0]) != "tmux":
        return False
    return "send-keys" in segment[1:]


def _systemctl_worker_action(segment):
    if not segment or _cmd_name(segment[0]) != "systemctl":
        return None
    rest = [t for t in segment[1:] if not t.startswith("-")]
    action = next((t for t in rest if t in _SYSTEMCTL_ACTIONS), None)
    unit = next((t for t in rest if "veridian-worker@" in t), None)
    if action and unit:
        return action, unit
    return None


def check_queue_bypass(segment, assignment):
    if assignment is None:
        return "allow", None
    if _tmux_send_keys(segment):
        return "deny", (
            "raw `tmux send-keys` from inside a worker session is blocked -- the real "
            "dispatch/relay front door is dispatch-owner-task.sh, never a raw send-keys "
            "issued by a worker itself (see dispatch-owner-task.sh's own header)"
        )
    blocked = _blocked_script_invocation(segment)
    if blocked:
        return "deny", (
            f"direct invocation of {blocked!r} from inside a worker session is blocked -- "
            f"it owns the shared dispatch lock/queue; the only real, mechanical dispatch "
            f"path is resource_governor.py's _perform_spawn()/next_queued_task()"
        )
    systemctl_action = _systemctl_worker_action(segment)
    if systemctl_action:
        action, unit = systemctl_action
        return "deny", (
            f"`systemctl ... {action} {unit}` from inside a worker session is blocked -- "
            f"this bypasses dispatch_core.acquire_dispatch_lock()/CONCURRENCY_CAP entirely "
            f"(real incident: 24 units launched this way at once OOM-killed the box, see "
            f"systemd/veridian-worker@.service's own 2026-08-01 comment); dispatch new work "
            f"through dispatch-owner-task.sh instead"
        )
    return "allow", None


# ---------------------------------------------------------------------------
# Check 4: shared ~/.claude/settings.json is off-limits to any worker
# session's Bash file-mutation commands (PR#375 audit finding #4 -- see
# module docstring). Redirection/curl writes to this same path are already
# caught by check_bash_file_write above (path-generic); this covers the
# commands that name a target path directly instead: cp/mv/install/tee/
# rsync/dd/sed -i.
# ---------------------------------------------------------------------------
def _resolve_maybe_relative(token, cwd):
    expanded = os.path.expanduser(token)
    return os.path.normpath(expanded if os.path.isabs(expanded) else os.path.join(cwd or "/", expanded))


def _touches_protected_settings_path(segment, cwd):
    if not segment:
        return None
    cmd = _cmd_name(segment[0])
    if cmd not in _FILE_MUTATION_COMMANDS:
        return None
    for tok in segment[1:]:
        raw = tok
        if cmd == "dd" and raw.startswith("of="):
            raw = raw.split("=", 1)[1]
        if raw.startswith("-"):
            continue
        resolved = _resolve_maybe_relative(raw, cwd)
        if resolved in _PROTECTED_SHARED_PATHS or _CLAUDE_SETTINGS_SUFFIX_RE.search(resolved):
            return resolved
    return None


def check_shared_settings_write(segment, cwd, assignment):
    """No dispatched worker task may use cp/mv/install/tee/rsync/dd/sed to
    touch the shared Claude Code settings file (~/.claude/settings.json --
    the file this hook is itself wired into), as source or destination.
    This exists because this hook's own initial rollout wrote that file
    directly, mid task, before its diff was ever reviewed or merged (a real
    unauthorized-live-deployment incident, UMR-20260814-131747-420e's
    PR#375 audit finding #4). Changing what's wired into it must land as a
    reviewed, merged, separately-dispatched deploy step from here on, never
    a worker self-deploying mid-task."""
    if assignment is None:
        return "allow", None
    touched = _touches_protected_settings_path(segment, cwd)
    if touched:
        return "deny", (
            f"this command touches the shared Claude Code settings file ({touched!r}) -- no "
            f"dispatched worker task may write it directly; changes to what's wired into it "
            f"must land as a reviewed, merged, separately-dispatched deploy step, never a "
            f"worker self-deploying mid-task (see UMR-20260814-131747-420e)"
        )
    return "allow", None


def evaluate_bash(command, cwd, assignment):
    """Runs checks 1, 1b, 2, and 4 over every segment of `command`. Returns
    the first non-'allow' verdict, or ('allow', None)."""
    if not command or not command.strip():
        return "allow", None
    for line in command.split("\n"):
        if not line.strip():
            continue
        tokens = _tokenize(line)
        if tokens is None:
            continue  # does not tokenize -- out of scope for this simpler hook, not fail-closed here
        for segment in _split_segments(tokens):
            if not segment:
                continue
            verdict, reason = check_git_write(segment, cwd, assignment)
            if verdict != "allow":
                return verdict, reason
            verdict, reason = check_bash_file_write(segment, cwd, assignment)
            if verdict != "allow":
                return verdict, reason
            verdict, reason = check_queue_bypass(segment, assignment)
            if verdict != "allow":
                return verdict, reason
            verdict, reason = check_shared_settings_write(segment, cwd, assignment)
            if verdict != "allow":
                return verdict, reason
    return "allow", None


# ---------------------------------------------------------------------------
# Check 3: audit log
# ---------------------------------------------------------------------------
def _audit_conn():
    os.makedirs(os.path.dirname(AUDIT_DB), exist_ok=True)
    conn = sqlite3.connect(AUDIT_DB, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_call_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            task_id TEXT,
            session_id TEXT,
            cwd TEXT,
            tool_name TEXT NOT NULL,
            tool_input_json TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task_id ON tool_call_audit(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON tool_call_audit(ts)")
    return conn


def log_audit(task_id, session_id, cwd, tool_name, tool_input, decision, reason):
    """Best-effort: a logging failure never blocks or changes the real
    enforcement decision, which is computed independently before this is
    called."""
    try:
        conn = _audit_conn()
        with conn:
            conn.execute(
                "INSERT INTO tool_call_audit "
                "(ts, task_id, session_id, cwd, tool_name, tool_input_json, decision, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                    session_id,
                    cwd,
                    tool_name,
                    json.dumps(tool_input, default=str)[:4000],
                    decision,
                    reason,
                ),
            )
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def evaluate(tool_name, tool_input, cwd, assignment):
    if tool_name == "Bash":
        command = (tool_input or {}).get("command") or ""
        return evaluate_bash(command, cwd, assignment)
    verdict, reason = check_write_tool_scope(tool_name, tool_input or {}, cwd, assignment)
    return verdict, reason


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # nothing readable to enforce or log

    session_id = payload.get("session_id")
    cwd = payload.get("cwd") or os.getcwd()
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    task_id = resolve_task_id(cwd)
    assignment = load_assignment(task_id) if task_id else None

    verdict, reason = evaluate(tool_name, tool_input, cwd, assignment)

    log_audit(
        task_id=task_id,
        session_id=session_id,
        cwd=cwd,
        tool_name=tool_name,
        tool_input=tool_input,
        decision="deny" if verdict == "deny" else "allow",
        reason=reason,
    )

    if verdict == "deny":
        print(f"BLOCKED by pretooluse_worker_enforcement: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
