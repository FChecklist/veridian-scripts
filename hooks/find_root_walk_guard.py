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
# themselves being the thing invoked. Real, confirmed bug fixed 2026-08-08
# (independent tier1 review, round 2): "timeout" was missing -- a very
# common, non-adversarial prefix for a potentially slow `find` (e.g.
# `timeout 300 find / -iname x`) bypassed this guard entirely, since
# "timeout" was never recognized at all. Unlike every other wrapper here,
# "timeout" takes a real positional duration argument (not just flags)
# before the wrapped command -- handled specially in _find_invocation_argv
# below, not just added to this flag-only-skip set.
_SKIP_WRAPPER_CMDS = {"sudo", "nice", "ionice", "time", "nohup", "exec", "command", "env"}
_TIMEOUT_WRAPPER_CMDS = {"timeout"}

# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, round 4):
# the wrapper-flag-skip loop originally assumed EVERY flag on every wrapper
# above was value-less -- but sudo -u USER, nice -n N, ionice -c/-n/-p N,
# and env -u NAME/-C DIR/-S STRING all take a real value token immediately
# after the flag. Blindly skipping one token per flag desynced the parser:
# the flag's VALUE (e.g. "root" after "-u") was then treated as the next
# thing to check against "find", which it never equals, so the whole
# invocation fell through to allow. Empirically verified live before this
# fix: `sudo -u root find / -iname secret`, `nice -n 19 find / -iname
# secret`, and `env -u FOO find / -iname secret` all exited 0 (allowed).
#
# This is the third real bypass found in this exact flag-skip logic across
# four independent review rounds (background operator, timeout's own
# positional arg, now wrapper value-flags) -- rather than keep enumerating
# one more flag shape each round, this adopts the reviewer's own suggested,
# more robust design: known value-taking flags (below) are skipped along
# with their value; any OTHER flag-looking token for a wrapper in
# _SKIP_WRAPPER_CMDS is now Unclassifiable (fail closed -- reject) rather
# than silently assumed value-less, since this guard can no longer
# confidently resolve whether a find invocation follows. This matches the
# guard's own documented fail-closed philosophy (see module docstring) and
# closes the whole bug CLASS, not just the three instances found so far.
_WRAPPER_VALUE_FLAGS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-R", "-r", "-t"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p"},
    "env": {"-u", "-C", "-S"},
    # time/nohup/exec/command take no common value-bearing flags in real
    # usage -- any flag encountered for these is Unclassifiable (reject),
    # never silently skipped, per the fail-closed redesign above.
}

# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, round 2):
# this guard only ever looked at the literal first word of a segment -- a
# `find` invoked as a STRING ARGUMENT to a shell-invocation command
# (`bash -c "find / ..."`, `sh -c '...'`, `eval "..."`) was invisible to it
# entirely, since shlex correctly parses the quoted string as one opaque
# token, not further tokenized. Every command here takes the embedded shell
# text as its own single string argument (the token immediately after -c
# for bash/sh/zsh/ksh/dash, or the first non-flag argument for eval) --
# recursively re-evaluated as a fresh command line, see
# _recursive_shell_string_verdict() below.
_SHELL_STRING_EXEC_CMDS = {"bash", "sh", "zsh", "ksh", "dash"}
_EVAL_CMDS = {"eval"}

# xargs forwards its trailing command name (and any literal, non-templated
# arguments after it) directly to exec -- `xargs find /` (or
# `echo | xargs find /`) really does run an unbounded find / walk, same
# real incident class as every other case here. xargs's OWN flags are
# skipped first (best-effort; the value-taking ones below are the real,
# common ones -- an unrecognized flag with a value would misparse, which
# fails toward Unclassifiable/reject via the normal argv-shape checks
# downstream, never toward silently allowing).
_XARGS_CMDS = {"xargs"}
_XARGS_VALUE_FLAGS = {"-I", "-n", "-P", "-L", "-s", "-a", "-d", "-E", "-e", "-l"}

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


def _cmd_name(token):
    """Real, confirmed bug fixed 2026-08-08 (independent tier1 review,
    round 3): every command-name comparison in this file used to be a
    strict string equality (e.g. segment[i] == "find"), so any absolute- or
    relative-path invocation (/usr/bin/find /, /bin/find /, ./find /) was
    never recognized and silently allowed through -- not an adversarial
    edge case, an ordinary, common shell idiom. Every real comparison below
    now goes through this: os.path.basename() of the token, so /usr/bin/
    find and find compare equal, the same way a real shell's PATH lookup
    would treat them as the same program."""
    return os.path.basename(token)


def _skip_wrapper_flags(segment, i, n, value_flags):
    """Real, shared, fail-closed flag-skip for one wrapper command's own
    argv, starting at segment[i] (the token right after the wrapper name).
    A flag in `value_flags` is skipped along with its value token; any
    OTHER flag-looking token raises Unclassifiable (reject) rather than
    being silently assumed value-less -- see _WRAPPER_VALUE_FLAGS's own
    comment for why (three real, empirically-confirmed bypasses came from
    the opposite assumption). Returns the new index, positioned at the
    first non-flag token (the wrapped command, or end of segment)."""
    while i < n and segment[i].startswith("-"):
        if segment[i] in value_flags:
            if i + 1 >= n:
                raise Unclassifiable(
                    f"wrapper flag {segment[i]!r} takes a value but none follows in this segment")
            i += 2
        else:
            raise Unclassifiable(
                f"unrecognized wrapper flag {segment[i]!r} -- cannot confidently determine "
                f"whether a find invocation follows (fail closed, not silently allowed)")
    return i


def _find_invocation_argv(segment):
    """If `segment` invokes the real `find` command (as the command itself,
    optionally after env-var assignments and/or a small set of benign
    wrapper commands), returns the argv that follows `find`. Otherwise
    returns None (this segment is out of this guard's scope). Raises
    Unclassifiable (fail closed) if a wrapper's own flags can't be
    confidently parsed -- see _skip_wrapper_flags()."""
    i, n = 0, len(segment)
    while i < n and _ENV_ASSIGN_RE.match(segment[i]):
        i += 1
    while i < n and (_cmd_name(segment[i]) in _SKIP_WRAPPER_CMDS
                      or _cmd_name(segment[i]) in _TIMEOUT_WRAPPER_CMDS
                      or _cmd_name(segment[i]) in _XARGS_CMDS):
        wrapper = _cmd_name(segment[i])
        i += 1
        if wrapper in _XARGS_CMDS:
            i = _skip_wrapper_flags(segment, i, n, _XARGS_VALUE_FLAGS)
        else:
            i = _skip_wrapper_flags(segment, i, n, _WRAPPER_VALUE_FLAGS.get(wrapper, set()))
            if wrapper in _TIMEOUT_WRAPPER_CMDS and i < n and _cmd_name(segment[i]) != "find":
                # timeout's own positional DURATION argument (e.g. "300",
                # "30s") -- real, not a flag, must be skipped too, unlike
                # every other wrapper here which takes only flags. Its own
                # flags (-k/-s/--foreground/--preserve-status) are handled
                # by the fail-closed _skip_wrapper_flags call above --
                # timeout has no entry in _WRAPPER_VALUE_FLAGS, so ANY flag
                # before the duration is Unclassifiable, same principle.
                i += 1
                i = _skip_wrapper_flags(segment, i, n, set())
    if i < n and _cmd_name(segment[i]) == "find":
        return segment[i + 1:]
    return None


_EXEC_FLAGS = {"-exec", "-execdir"}
_EXEC_TERMINATORS = {";", "+"}


def _scan_nested_execs(argv, cwd, evaluate_fn):
    """Real, confirmed bug fixed 2026-08-08 (independent tier1 review,
    round 3): a find invocation correctly scoped to a safe subtree can
    still embed a second, unbounded find via `-exec`/`-execdir` (e.g.
    `find /opt/veridian -exec find / -iname '*secret*' \\;`, or
    `-exec sh -c 'find / ...' \\;`) -- _extract_root_tokens() stops at the
    first flag-like token (-exec itself), so the embedded command was never
    looked at. Scans `argv` for every -exec/-execdir occurrence, extracts
    the embedded command up to its own real terminator (a literal ';' or
    '+' token -- shlex, posix mode, correctly preserves a shell-escaped
    \\; as one such token, distinct from an unescaped segment-breaking ';'),
    and recursively evaluates that embedded command the same way any other
    segment is evaluated (real find invocation, or a shell-string wrapper
    around one). Returns the first real (verdict, reason) that isn't
    'allow', or None if nothing embedded is a problem."""
    i, n = 0, len(argv)
    while i < n:
        if argv[i] in _EXEC_FLAGS:
            i += 1
            embedded = []
            while i < n and argv[i] not in _EXEC_TERMINATORS:
                embedded.append(argv[i])
                i += 1
            if embedded:
                is_find, unbounded_root = _segment_unbounded_root(embedded, cwd)
                if is_find and unbounded_root is not None:
                    return "reject_unbounded", unbounded_root
                if not is_find:
                    recursive = _recursive_shell_string_verdict(embedded, cwd, evaluate_fn)
                    if recursive is not None and recursive[0] != "allow":
                        return recursive
                # A nested find/-exec inside the embedded command itself
                # (real, if unlikely, recursion) -- re-scan its own argv too.
                nested_argv = _find_invocation_argv(embedded)
                if nested_argv is not None:
                    deeper = _scan_nested_execs(nested_argv, cwd, evaluate_fn)
                    if deeper is not None:
                        return deeper
        else:
            i += 1
    return None


def _recursive_shell_string_verdict(segment, cwd, evaluate_fn):
    """If `segment` is a shell-invocation command (bash -c STRING, sh -c
    STRING, eval STRING, ...) carrying embedded shell text as one of its own
    argv tokens, recursively evaluates that text as a fresh command line via
    `evaluate_fn` (the real evaluate() below -- passed in rather than called
    by name to keep this a pure, testable function). Returns the recursive
    (verdict, reason) tuple, or None if `segment` doesn't match this shape
    at all (out of scope, caller falls through to its normal handling)."""
    i, n = 0, len(segment)
    while i < n and _ENV_ASSIGN_RE.match(segment[i]):
        i += 1
    if i >= n:
        return None
    cmd = _cmd_name(segment[i])
    if cmd in _SHELL_STRING_EXEC_CMDS:
        rest = segment[i + 1:]
        if "-c" not in rest:
            return None
        c_idx = rest.index("-c")
        string_args = [t for t in rest[c_idx + 1:] if t not in ("-c",)]
        if not string_args:
            return None
        return evaluate_fn(string_args[0], cwd)
    if cmd in _EVAL_CMDS:
        string_args = [t for t in segment[i + 1:] if not t.startswith("-")]
        if not string_args:
            return None
        return evaluate_fn(" ".join(string_args), cwd)
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


def _extract_command_substitutions(command):
    """Real, confirmed bug fixed 2026-08-08 (independent tier1 review, round
    5): backtick command substitution (`` `find / -iname secret` ``) and
    $(...) command substitution both let a `find` invocation's raw text be
    glued to adjacent punctuation (a backtick token never equals the literal
    word "find" after tokenization) or be embedded somewhere the segment-
    based tokenizer never isolates as its own command. Extracted here, as
    raw substring content, for recursive evaluation the same way bash -c/
    eval/-exec are handled -- backtick pairs (real POSIX shell backtick
    substitution never nests without escaping, so simple non-nested
    extraction is correct for the real, common case) and $(...) pairs
    (balanced-paren scan, since these genuinely can nest, e.g.
    $(find $(dirname .) -name x))."""
    substitutions = []
    # Backtick pairs: `...`
    for m in re.finditer(r"`([^`]*)`", command):
        substitutions.append(m.group(1))
    # $(...) pairs, balanced.
    i, n = 0, len(command)
    while i < n:
        if command[i] == "$" and i + 1 < n and command[i + 1] == "(":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                j += 1
            if depth == 0:
                substitutions.append(command[i + 2:j - 1])
                i = j
                continue
        i += 1
    return substitutions


def evaluate(command, cwd):
    """Returns (verdict, reason) where verdict is one of 'allow',
    'reject_unbounded', 'reject_unclassifiable'."""
    if not command or not command.strip():
        return "allow", None

    # Real, confirmed bug fixed 2026-08-08 (independent tier1 review, round
    # 5): the original fast-path pre-filter here matched _FIND_WORD_RE
    # against the RAW, untokenized command string, and returned "allow"
    # immediately with zero tokenization if no contiguous "find" substring
    # was present. Live-verified bypasses: 'f'ind / and f\\ind / (quote-
    # fragment and backslash-escape tricks that shlex would correctly
    # reduce to the literal word "find" if ever reached) both skipped
    # tokenization entirely and were allowed outright. There is no reliable
    # raw-text shortcut for "definitely does not contain find" once quoting
    # and escaping are in play -- this guard now always tokenizes; the
    # real, measured cost (shlex over a typical shell command string) is
    # microseconds, not worth trading away this guard's own fail-closed
    # purpose for.
    try:
        for substitution in _extract_command_substitutions(command):
            sub_verdict = evaluate(substitution, cwd)
            if sub_verdict[0] != "allow":
                return sub_verdict

        for line in command.split("\n"):
            if not line.strip():
                continue
            tokens = _tokenize_line(line)
            for segment in _split_segments(tokens):
                is_find, unbounded_root = _segment_unbounded_root(segment, cwd)
                if is_find and unbounded_root is not None:
                    return "reject_unbounded", unbounded_root
                if is_find:
                    # Real, confirmed bug fixed 2026-08-08 (independent
                    # tier1 review, round 3): a find invocation correctly
                    # scoped to a safe subtree can still embed a SECOND,
                    # unbounded find (or a shell-string-wrapped one) via
                    # -exec/-execdir -- scan this find's own argv for that.
                    nested_argv = _find_invocation_argv(segment)
                    if nested_argv is not None:
                        nested = _scan_nested_execs(nested_argv, cwd, evaluate)
                        if nested is not None and nested[0] != "allow":
                            return nested
                if not is_find:
                    # Real, confirmed bug fixed 2026-08-08 (independent
                    # tier1 review, round 2): a `find` embedded as a STRING
                    # ARGUMENT to bash -c/sh -c/eval was invisible to the
                    # literal-first-word check above -- recursively evaluate
                    # the embedded shell text as its own fresh command line.
                    recursive = _recursive_shell_string_verdict(segment, cwd, evaluate)
                    if recursive is not None and recursive[0] != "allow":
                        return recursive
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
