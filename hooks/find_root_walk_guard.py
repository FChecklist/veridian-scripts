#!/usr/bin/env python3
"""find_root_walk_guard.py -- Claude Code PreToolUse hook (matcher: Bash).

WHY THIS EXISTS (UMR-20260806-121825-8ece, governing UMR-20260806-071025-1d28,
PM decision row 56, opened 2026-08-06T11:28:14Z): three consecutive real
incidents on 2026-08-06 in which unbounded `find /` root-filesystem walks
(launched through a Claude Code agent shell, relayed via the `snip` PreToolUse
hook) drove load average to 28.86 with swap free at 0MB/4095MB -- the
documented OOM incident pattern for this box. This filesystem is ~247GB with
~1416 task worktrees and 334+ node_modules trees; an unbounded walk over it is
a large, sustained random-read amplifier. Every one of those incidents
searched for something whose real location was already documented (see the
canonical routes cited in the rejection messages below) or, in one case
(`find / -iname *gtm_certification_categories*`), for something that is a
database TABLE, not a file on disk at all -- that scan could never have
succeeded no matter how long it ran.

WHAT THIS DOES: reads the PreToolUse hook JSON payload on stdin
(`{"tool_name": "Bash", "tool_input": {"command": "..."}, "cwd": "...", ...}`).
If the command contains a real `find` invocation whose search root resolves
to `/` (or an equivalent unbounded root, e.g. an unresolvable root or a
top-level `/*` glob), the command is BLOCKED (exit 2, reason on stderr --
this is how Claude Code hooks report a PreToolUse denial back to the
model/user; confirmed against the current Claude Code hooks documentation).
A `find` scoped to a real subtree (e.g. `/opt/veridian`) is allowed.

FAIL-CLOSED POLICY: if this guard cannot confidently determine a `find`
invocation's search root (unresolved shell variable/command-substitution in
root position, or the command fails to tokenize at all while still
containing the literal word `find`), the command is REJECTED, not allowed.
Unclassifiable is treated as unbounded. This is a deliberate, narrow
fail-closed scope: commands that do not contain the word `find` at all are
allowed immediately without full parsing (this guard's whole mandate is
`find`-rooted walks, not general shell command validation -- rejecting
unrelated, harmless bash syntax this guard cannot parse would erode trust in
the guard and invite it being disabled).

This hook does not weaken, replace, or run instead of any existing
PreToolUse hook (e.g. `snip hook`) -- it is registered as an *additional*
hook entry under the same `Bash` matcher. Claude Code runs all hooks for a
matcher and applies the most restrictive decision, so this guard adds a
strictly-narrowing check without touching the existing gate.
"""
import json
import os
import re
import shlex
import sys

CANONICAL_ROUTES_MSG = """\
Before scanning the filesystem, the file you want is almost always already documented:
  - /opt/veridian/scripts/superboss-register.py
  - /opt/veridian/scripts/resource_governor.py
  - /opt/veridian/ai-os/MASTER_INDEX.yaml (canonical file routes + search guidance)

Use the capability registry instead of scanning:
  1. python3 /opt/veridian/scripts/superboss-register.py lookup-capability --capability-name "<name>" (or --intent-text "<what you need>")
  2. python3 /opt/veridian/scripts/superboss-register.py list-capabilities
  3. python3 /opt/veridian/scripts/superboss-register.py --query-umr --search "<term>" (or resource_governor.py --query-umr --search) for anything UMR-shaped -- never grep/find the filesystem for a UMR id, it is a row in superboss-register.sqlite's umr_tasks table.
  4. /opt/veridian/scripts/find_code.sh "<pattern>" [scope_dir] for a real pruned, scoped code search (never over /).

If you genuinely need a filesystem search, scope it to a real subtree, e.g.:
  find /opt/veridian <expression...>\
"""

# Wrapper commands that may legitimately precede `find` in a segment without
# themselves being the thing invoked (their own flag tokens are also
# skipped, best-effort).
_SKIP_WRAPPER_CMDS = {"sudo", "nice", "ionice", "time", "nohup", "exec", "command", "env"}

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_FIND_WORD_RE = re.compile(r"(?<![\w-])find(?![\w-])")
_VAR_OR_SUBST_RE = re.compile(r"\$|`")

# Control-operator tokens that end one command segment and start another,
# once shlex (punctuation_chars=True) has split them out as their own
# tokens. Real, confirmed bug (2026-08-08, independent tier1 review of this
# exact hook): the plain background operator "&" was missing here -- shlex
# already tokenizes it as its own punctuation token (its default
# punctuation_chars set includes "&"), so `<anything> & find /` (e.g.
# `true & find /`, `cd /opt/veridian & find /`) was folded into a single
# segment whose first word was never "find", bypassing this guard entirely.
# Verified live before the fix: both examples exited 0 (allow), reproducing
# the exact real incident class (unbounded find / walk) this hook exists to
# prevent. "&" is now a real segment break, same as every other control
# operator here.
_SEGMENT_BREAKS = {";", "&", "&&", "||", "|", "(", ")"}

# Roots (after normalization relative to cwd) that count as an unbounded
# walk. "/" is the one the real incidents used; the trailing-slash and
# double-slash spellings normalize to it via os.path.normpath, so they are
# covered without needing separate entries.
_UNBOUNDED_GLOB_RE = re.compile(r"^/\*+$")


def _is_unbounded(resolved_path):
    """True for '/' and its normpath-preserved variants ('//' is kept
    literally by POSIX normpath -- exactly two leading slashes has
    implementation-defined meaning and Python does not collapse it -- '///'
    and beyond do collapse to '/'). Any of these is still the whole root
    filesystem."""
    return resolved_path.startswith("/") and resolved_path.rstrip("/") == ""


class Unclassifiable(Exception):
    pass


def _tokenize_line(line):
    """Tokenize one line with shlex, splitting out ; && || | ( ) as their
    own punctuation tokens. Returns a list of tokens, or raises
    Unclassifiable if the line does not tokenize cleanly (e.g. an
    unterminated quote)."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = []
    try:
        while True:
            tok = lexer.get_token()
            if tok is None:
                break
            tokens.append(tok)
    except ValueError as exc:
        raise Unclassifiable(f"command did not tokenize cleanly ({exc})")
    return tokens


def _split_segments(tokens):
    """Splits a flat token list on control-operator tokens into a list of
    command segments (each a list of word tokens, operators removed)."""
    segments = []
    current = []
    for tok in tokens:
        if tok in _SEGMENT_BREAKS:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    return [s for s in segments if s]


def _find_invocation_argv(segment):
    """If `segment` invokes the real `find` command (as the command itself,
    optionally after env-var assignments and/or a small set of benign
    wrapper commands), returns the argv that follows `find`. Otherwise
    returns None (this segment is out of this guard's scope)."""
    i, n = 0, len(segment)
    while i < n and _ENV_ASSIGN_RE.match(segment[i]):
        i += 1
    while i < n and segment[i] in _SKIP_WRAPPER_CMDS:
        i += 1
        while i < n and segment[i].startswith("-"):
            i += 1
    if i < n and segment[i] == "find":
        return segment[i + 1:]
    return None


def _extract_root_tokens(argv):
    """find's leading non-flag arguments are its search roots (paths).
    Stops at the first token that looks like a flag/expression operator."""
    roots = []
    for tok in argv:
        if tok.startswith("-") or tok in ("(", ")", "!", ","):
            break
        roots.append(tok)
    return roots


def _resolve_root(token, cwd):
    """Resolves one find root token to an absolute, normalized path.
    Raises Unclassifiable if the token contains an unresolved shell
    variable or command substitution (we cannot know its real value at
    hook time)."""
    if _VAR_OR_SUBST_RE.search(token):
        raise Unclassifiable(f"root argument {token!r} contains an unresolved shell variable/substitution")
    if _UNBOUNDED_GLOB_RE.match(token):
        return "/"  # top-level glob under root -- treat as unbounded
    if os.path.isabs(token):
        return os.path.normpath(token)
    return os.path.normpath(os.path.join(cwd or "/", token))


def _segment_unbounded_root(segment, cwd):
    """Returns (is_find_invocation, unbounded_root_or_None). Raises
    Unclassifiable if a find invocation's root can't be resolved."""
    argv = _find_invocation_argv(segment)
    if argv is None:
        return False, None
    roots = _extract_root_tokens(argv)
    if not roots:
        roots = [cwd or "/"]
    for tok in roots:
        resolved = _resolve_root(tok, cwd)
        if _is_unbounded(resolved):
            return True, resolved
    return True, None


def evaluate(command, cwd):
    """Returns (verdict, reason) where verdict is one of 'allow',
    'reject_unbounded', 'reject_unclassifiable'."""
    if not command or not command.strip():
        return "allow", None

    if not _FIND_WORD_RE.search(command):
        return "allow", None  # no `find` anywhere -- out of this guard's scope

    try:
        for line in command.split("\n"):
            if not line.strip():
                continue
            tokens = _tokenize_line(line)
            for segment in _split_segments(tokens):
                is_find, unbounded_root = _segment_unbounded_root(segment, cwd)
                if is_find and unbounded_root is not None:
                    return "reject_unbounded", unbounded_root
    except Unclassifiable as exc:
        return "reject_unclassifiable", str(exc)

    return "allow", None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Can't even read the hook payload -- nothing to evaluate, and this
        # is not a find-shaped decision at all. Allow; the harness's own
        # malformed-payload handling (if any) is not this guard's concern.
        sys.exit(0)

    if payload.get("tool_name") != "Bash":
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    cwd = payload.get("cwd") or os.getcwd()

    verdict, detail = evaluate(command, cwd)

    if verdict == "allow":
        sys.exit(0)

    if verdict == "reject_unbounded":
        print(
            f"BLOCKED by find_root_walk_guard: this `find` walks an unbounded root "
            f"({detail!r}).\n"
            f"Unbounded `find /` (or equivalent) walks have repeatedly driven this "
            f"247GB / ~1416-worktree box into real load/OOM-risk incidents today "
            f"(PM decision row 56, UMR-20260806-071025-1d28) -- terminated three "
            f"times in one day before this guard existed.\n\n"
            f"{CANONICAL_ROUTES_MSG}",
            file=sys.stderr,
        )
        sys.exit(2)

    # reject_unclassifiable
    print(
        f"BLOCKED by find_root_walk_guard: this command contains a `find` "
        f"invocation whose search root could not be confidently classified "
        f"({detail}).\n"
        f"Fail-closed policy: an unclassifiable `find` is treated as an "
        f"unbounded walk and rejected, not allowed (PM decision row 56, "
        f"UMR-20260806-121825-8ece).\n\n"
        f"Rewrite the command with a literal, explicit root under /opt/veridian, "
        f"or use one of these instead:\n\n"
        f"{CANONICAL_ROUTES_MSG}",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
