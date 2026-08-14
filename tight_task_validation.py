#!/usr/bin/env python3
"""
Faithful Python port of src/lib/task-tightening.ts's validateTightTask()
for the shell-layer dispatch scripts (worker-entrypoint.sh,
doc-worker-entrypoint.sh), which have no access to the TS/DB-backed
original. Deployed 2026-07-20, closing a real gap found in the
"constitution cross-check" audit: the shell fleet was entirely outside
this validation.

Deliberately a PORT, not a reinvention -- same field-check logic, same
placeholder patterns, same ambiguity phrase list, same field-contradiction
detection algorithm, ported line-for-line from the TS original so the two
enforcement points (TS for the app/HTTP dispatch, this for the shell
fleet) apply IDENTICAL rules instead of two similar-but-drifting ones.

Scope note: only validates prompts written in the new labeled-field format
(## OBJECTIVE / ## SCOPE / etc. headers). A prompt with none of these
headers is treated as legacy free-text (pre-existing tasks, dispatched
before this validator existed) and is NOT blocked -- this must never
retroactively fail an already-running task. New task prompts should adopt
the labeled format going forward.

2026-07-24 addition (INS-20260724-113032-8032, root-cause close for 5
initiatives -- self-sustaining-system-engine, 20-Engines/10-Gateways,
Auditor Engine, Testing Engine, Terminology Standardization -- that each
stalled at Phase 1): a SUCCESS_CRITERIA section can pass every check above
(present, not a placeholder, long enough, no ambiguous phrases) while still
being pure prose with nothing a human or CI could actually run to verify
the task -- e.g. "All tests pass and the feature works end to end." That
gap meant postflight_audit_gate.py's audit_cmd (which IS run for real,
subprocess + exit code) could be satisfied by copying prose verbatim
rather than citing a real check. check_success_criteria_has_runnable_command()
closes this: it requires at least one SUCCESS_CRITERIA line that looks like
a real, runnable shell command, per the heuristic documented on that
function.

2026-08-14 addition (UMR-20260814-132703-a1f9): a required FILE_PATHS
field -- check_file_paths() -- closing the gap where a task's Objective/
Scope/Expected output can name "the module" or "the feature" without ever
naming a real, concrete repo-relative file, leaving file identity for the
model to guess exactly the way this validator already refuses to let
Objective/Scope/etc. do. This is a real, local divergence from
src/lib/task-tightening.ts (this file's own source of truth per the PORT
note above) -- src/lib/task-tightening.ts lives in a different repo not
reachable from here; this addition is scripts-fleet-only until/unless the
TS original is updated to match.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workflow_contract import REQUIRED_TASK_SECTIONS  # noqa: E402

MIN_FIELD_LENGTH = 10

PLACEHOLDER_PATTERNS = [
    re.compile(r"^(tbd|todo|n/?a|none|null|undefined|xxx+|\.\.\.|fill.?in|same as (above|objective|scope))$", re.IGNORECASE),
    re.compile(r"^\s*$"),
]

AMBIGUITY_PHRASES = [
    "etc.", "and so on", "and so forth", "as appropriate", "as needed",
    "if needed", "if necessary", "when necessary", "handle edge cases",
    "handle appropriately", "figure it out", "use your judgment", "use your judgement",
    "some kind of", "some sort of", "not sure", "we'll see", "tbd later",
]

NEGATION_TRIGGERS = ["do not", "don't", "never", "must not", "should not", "shouldn't", "excluding", "without"]
CONTRADICTION_STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at", "by", "with",
    "it", "this", "that", "under", "any", "all", "circumstances", "as", "is", "be",
}

# Phrases that make a nearby prohibition/requirement CONDITIONAL rather than
# absolute -- e.g. "do not add X without an approved citation" only forbids
# adding X in the unapproved case, and "build X where missing" only requires
# X where it doesn't already exist. Keyword overlap across one of these
# boundaries is not evidence of a real conflict: the two clauses are often
# each other's complement (forbidden-without-citation vs required-only-
# where-missing), not a restatement of the same unconditional rule.
QUALIFIER_PHRASES = [
    "without", "unless", "except", "excluding", "only when", "only if",
    "already", "where missing", "not yet", "so long as", "provided that",
    "as long as",
]

# Bare negation markers, scanned across the WHOLE requirement text (not just
# after one of the fixed NEGATION_TRIGGERS phrases) so that a word used only
# inside a negative/prohibitive clause elsewhere in the prompt (e.g. "...are
# not implemented", "isn't required", "adds no cron entries") is never
# counted as an affirmative requirement. Two negatives about the same thing
# are agreement, not a contradiction.
NEGATION_MARKER_WORDS = {"not", "never", "without", "excluding", "no"}

VALID_TIERS = ["mechanical", "integrative", "judgment"]

# Built from the shared REQUIRED_TASK_SECTIONS list (workflow_contract.py) instead
# of a second hardcoded name list -- task-20260726-092433 dedup audit, SCOPE item 3:
# this regex and task-gateway.py's cmd_start section-presence check used to name the
# same 7 sections independently and could silently drift.
#
# FILE_PATHS (UMR-20260814-132703-a1f9) is deliberately NOT added to the
# shared REQUIRED_TASK_SECTIONS list itself: that list is also consumed by
# task-gateway.py's cmd_start()/has_all_required_sections() submit-time gate
# and prompt_gateway/gateway.py's dispatch-readiness router, and this task's
# scope is this validator only -- silently making FILE_PATHS required at
# those OTHER, unrelated call sites too is a bigger, unreviewed blast radius
# than "add a required field to the task-tightening validator" asked for.
# It IS added to this module's own LOCAL_ONLY_SECTIONS below so a real
# prompt can still supply "## FILE_PATHS" and have it parsed exactly like
# every other labeled field, and check_file_paths() below enforces it here.
LOCAL_ONLY_SECTIONS = ["FILE_PATHS"]
FIELD_HEADER_RE = re.compile(
    r"^##\s*(" + "|".join(REQUIRED_TASK_SECTIONS + LOCAL_ONLY_SECTIONS) + r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# HOLD_FOR_OWNER_SIGNOFF marker (2026-07-26, root-caused against the PR563
# incident): a dispatch prompt's prose instruction "must be held for Owner
# sign-off, do not merge under any circumstance" had zero effect on the actual
# merge decision -- nothing in the pipeline read prompt-level prose, so the
# task auto-merged anyway. This is the real, machine-readable replacement: a
# literal marker line inside EXPECTED_OUTPUT or CONSTRAINTS (the same two
# sections dispatch prompts already use for hold/merge-affecting directives),
# extracted here and threaded through task-gateway.py's cmd_start() into
# task.yaml, so supervisor-entrypoint.sh's merge-decision block can enforce it
# for real instead of trusting an AI reader to have honored the prose.
HOLD_FOR_OWNER_SIGNOFF_RE = re.compile(r"^\s*HOLD_FOR_OWNER_SIGNOFF\s*:\s*(true|false)\s*$", re.IGNORECASE | re.MULTILINE)

# --- SUCCESS_CRITERIA runnable-command heuristic -----------------------------
# Concrete, testable rule (documented here, not just in the docstring above):
# a SUCCESS_CRITERIA line "looks like a real, runnable shell command" if EITHER:
#   (a) its first whitespace token (ignoring a leading list marker like "-"/
#       "1." and a leading "./" or "$") is one of COMMAND_WORDS below, a
#       closed, deliberately curated list of interpreters/CLIs this project's
#       own tasks actually use (see every SUCCESS_CRITERIA in this session's
#       own dispatched prompts for the source list); OR
#   (b) it contains a backtick-quoted `span` that is not "prose" -- prose is
#       detected mechanically as >=4 words with >=3 hits against
#       PROSE_STOPWORDS (common English connective/verb words that show up
#       in descriptive sentences but essentially never inside a shell
#       command) -- and that span contains a command-shaped token: a path
#       separator ("/"), a flag ("--" or a lone "-x"), or a pipe ("|").
# A line failing both (a) and (b) is prose: it describes an outcome
# ("all tests pass", "the feature works end to end") but supplies nothing
# copy-pasteable that a human or CI could actually run.
COMMAND_WORDS = {
    "python3", "python", "bash", "sh", "zsh", "curl", "gh", "git",
    "npm", "npx", "yarn", "pnpm", "pytest", "node", "bunx", "bun",
    "systemctl", "crontab", "sqlite3", "psql", "jq", "make", "docker",
}

PROSE_STOPWORDS = {
    "the", "is", "are", "will", "should", "that", "with", "and", "was",
    "this", "be", "has", "have", "shows", "confirms", "means", "which",
    "all", "not", "than", "when", "then", "than", "were", "been", "being",
}

LIST_MARKER_RE = re.compile(r"^\s*[-*]\s+|^\s*\d+[.)]\s+")
BACKTICK_SPAN_RE = re.compile(r"`([^`]+)`")


def _looks_like_prose(text):
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) < 4:
        return False
    hits = sum(1 for w in words if w in PROSE_STOPWORDS)
    return hits >= 3


def _line_is_runnable_command(raw_line):
    line = LIST_MARKER_RE.sub("", raw_line).strip()
    if not line:
        return False

    backtick_spans = BACKTICK_SPAN_RE.findall(line)
    candidates = backtick_spans if backtick_spans else [line]

    for cand in candidates:
        cand = cand.strip().lstrip("$").strip()
        if not cand:
            continue
        first_token = re.split(r"\s+", cand)[0].lower().lstrip("./")
        if first_token in COMMAND_WORDS:
            return True
        if backtick_spans and not _looks_like_prose(cand) and re.search(r"[/]|--|\s-[a-zA-Z]\b|\|", cand):
            return True
    return False


def check_success_criteria_has_runnable_command(success_criteria_text):
    """Returns None if at least one runnable-looking command line is found,
    else a {valid:false, reason, guidance} dict per this module's contract."""
    lines = [l for l in (success_criteria_text or "").splitlines() if l.strip()]
    if any(_line_is_runnable_command(l) for l in lines):
        return None
    return {
        "valid": False,
        "reason": "no_runnable_verification_command_in_success_criteria: SUCCESS_CRITERIA contains only "
                   "descriptive prose -- no line that looks like a real, runnable shell command.",
        "guidance": "Add at least one line to SUCCESS_CRITERIA that is an actual command someone (or CI, "
                    "or postflight_audit_gate.py's audit_cmd) could copy-paste and run to verify this task "
                    "for real -- e.g. `python3 script.py --check`, `pytest tests/test_foo.py`, or "
                    "`gh pr view <n> --json state`. A sentence describing the desired outcome (\"all tests "
                    "pass\", \"the feature works end to end\") is not a substitute for a command that "
                    "actually checks it.",
    }


def is_placeholder(value):
    trimmed = value.strip()
    return any(p.match(trimmed) for p in PLACEHOLDER_PATTERNS)


def detect_ambiguous_language(value):
    lower = value.lower()
    for phrase in AMBIGUITY_PHRASES:
        if phrase in lower:
            return {"detected": True, "matchedPhrase": phrase}
    return {"detected": False}


def content_words(text, limit=None):
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in CONTRADICTION_STOPWORDS and len(w) > 2]
    return words[:limit] if limit else words


def _truncate_at_qualifier(text):
    """Cut `text` at the first QUALIFIER_PHRASES match. Text after a
    qualifier describes the exception/condition attached to a prohibition,
    not the core prohibited action, and must not be compared for overlap
    (e.g. "add cron entries without an approved citation" -> the core
    prohibited action is "add cron entries"; "an approved citation" is what
    makes it OK, not part of what's forbidden)."""
    cut = len(text)
    for phrase in QUALIFIER_PHRASES:
        idx = text.find(phrase)
        if idx != -1 and idx < cut:
            cut = idx
    return text[:cut]


def _scoped_token_positions(tokens, window=10):
    """Token indices that fall within `window` tokens of a negation marker
    (not/never/without/excluding/no/n't) or a QUALIFIER_PHRASES match.
    Content words at these positions are either part of a negative/
    prohibitive clause ("...are not implemented", "adds no cron entries")
    or a scope-narrowing qualifier ("...where missing", "...already done")
    -- neither states a plain, unconditional requirement."""
    scoped = set()
    for i, tok in enumerate(tokens):
        if tok in NEGATION_MARKER_WORDS or tok.endswith("n't"):
            lo, hi = max(0, i - window), min(len(tokens), i + window + 1)
            scoped.update(range(lo, hi))
    for phrase in QUALIFIER_PHRASES:
        phrase_tokens = phrase.split()
        n = len(phrase_tokens)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == phrase_tokens:
                lo, hi = max(0, i - window), min(len(tokens), i + n + window)
                scoped.update(range(lo, hi))
    return scoped


def _max_unconditional_cluster_overlap(requirement_text, phrase_words, cluster_window=12):
    """Slide a `cluster_window`-token span across requirement_text and
    return the largest number of `phrase_words` found TOGETHER within any
    single span that isn't scoped (see _scoped_token_positions).

    This is deliberately a local, clustered check rather than "does this
    word appear anywhere in the document": a Constraints prohibition like
    "do not add cron entries" sharing one word ("cron") with an unrelated
    sentence about a cron-driven entrypoint, and a different word
    ("entries") with yet another unrelated sentence elsewhere, is not
    evidence those words were restated together as a real, conflicting
    requirement -- they never actually co-occur. Only a genuine local
    restatement of the same cluster counts."""
    tokens = re.findall(r"[a-z0-9']+", requirement_text)
    scoped = _scoped_token_positions(tokens)
    phrase_set = set(phrase_words)
    best = 0
    n = len(tokens)
    for start in range(n):
        end = min(n, start + cluster_window)
        if any(p in scoped for p in range(start, end)):
            continue
        span_words = set(tokens[start:end])
        overlap = len(phrase_set & span_words)
        if overlap > best:
            best = overlap
            if best == len(phrase_set):
                break
    return best


def detect_field_contradiction(task):
    constraint_text = (task.get("constraints") or "").lower()
    if not constraint_text.strip():
        return {"detected": False}
    requirement_text = " ".join(
        v for v in [task.get("objective"), task.get("scope"), task.get("successCriteria"), task.get("expectedOutput")] if v
    ).lower()
    if not requirement_text.strip():
        return {"detected": False}

    for trigger in NEGATION_TRIGGERS:
        search_from = 0
        while True:
            idx = constraint_text.find(trigger, search_from)
            if idx == -1:
                break
            after = _truncate_at_qualifier(constraint_text[idx + len(trigger):])
            words = content_words(after, 6)
            if len(words) >= 2:
                matched = _max_unconditional_cluster_overlap(requirement_text, words)
                if matched >= 2 and matched / len(words) >= 0.6:
                    return {"detected": True, "conflictingTerm": " ".join(words)}
            search_from = idx + len(trigger)
    return {"detected": False}


def check_field(value, label, example):
    trimmed = (value or "").strip()
    if not trimmed:
        return {"valid": False, "reason": f"{label} is missing.", "guidance": f'Please add a {label.lower()} before this can proceed. Example: "{example}"'}
    if is_placeholder(trimmed):
        return {"valid": False, "reason": f'{label} is a placeholder, not a real value ("{trimmed}").', "guidance": f'Please replace it with the actual {label.lower()}. Example: "{example}"'}
    if len(trimmed) < MIN_FIELD_LENGTH:
        return {"valid": False, "reason": f'{label} is too short to be actionable ("{trimmed}").', "guidance": f'Could you be a little more specific -- name the concrete file, behavior, or outcome, not just a category? Example: "{example}"'}
    return None


def _parse_file_paths(raw):
    """Split a FILE_PATHS value into individual candidate path strings.
    Accepts either a real list (a caller building `task` directly, not via
    a prompt's labeled headers) or the raw multi-line/comma-separated
    section text parse_labeled_fields() extracts -- one path per line
    (an optional leading "-"/"1." list marker is stripped, same convention
    SUCCESS_CRITERIA lines already use) or comma-separated on one line."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p).strip() for p in raw]
    parts = []
    for line in str(raw).splitlines():
        line = LIST_MARKER_RE.sub("", line).strip()
        if not line:
            continue
        parts.extend(p.strip() for p in line.split(","))
    return parts


def check_file_paths(value):
    """Required `filePaths` field (UMR-20260814-132703-a1f9, real incident:
    UMR-20260814-125933-3377's own gap-finding work named a real file path
    -- pm-sentinel-tick.sh -- only in free-text prose, nowhere machine-
    readable). Every tight task must name the real repo-relative path(s)
    its scope touches, the same "don't leave this for the model to guess"
    principle this module already applies to Objective/Scope/Success
    criteria/Expected output. Reuses check_field() (for the missing/too-
    short/placeholder-as-a-whole case) and is_placeholder() (per individual
    path) -- this module's own existing helpers, not a second, parallel
    validator. Returns None if valid, else a {valid:false, reason,
    guidance} dict per this module's contract."""
    paths = _parse_file_paths(value)
    joined = ", ".join(paths)
    whole_field_failure = check_field(
        joined, "File paths", "scripts/tight_task_validation.py, tests/test_tight_task_validation.py")
    if whole_field_failure:
        return whole_field_failure
    for p in paths:
        if not p or is_placeholder(p):
            return {
                "valid": False,
                "reason": f'File paths contains a placeholder or empty entry ("{p}").',
                "guidance": "Every entry in FILE_PATHS must be a real repo-relative path (e.g. "
                            '"scripts/tight_task_validation.py"), not a placeholder or a blank line.',
            }
        if p.startswith("/") or "://" in p:
            return {
                "valid": False,
                "reason": f'File paths entry "{p}" is not a repo-relative path.',
                "guidance": "Use a path relative to the repo root (e.g. "
                            '"scripts/tight_task_validation.py"), not an absolute filesystem path or a URL.',
            }
    return None


def validate_tight_task(task):
    objective_failure = check_field(task.get("objective"), "Objective", "Document the Leads module end to end")
    if objective_failure:
        return objective_failure

    scope_failure = check_field(task.get("scope"), "Scope", "Only the Leads module of this CRM tenant")
    if scope_failure:
        return scope_failure

    success_failure = check_field(task.get("successCriteria"), "Success criteria", "All 8 documentation points covered, screenshots taken, PROGRESS.md complete")
    if success_failure:
        return success_failure

    output_failure = check_field(task.get("expectedOutput"), "Expected output", "One markdown file per module, committed and pushed")
    if output_failure:
        return output_failure

    file_paths_failure = check_file_paths(task.get("filePaths"))
    if file_paths_failure:
        return file_paths_failure

    for label, key in [("Objective", "objective"), ("Scope", "scope"), ("Success criteria", "successCriteria"), ("Expected output", "expectedOutput")]:
        ambiguity = detect_ambiguous_language(task.get(key) or "")
        if ambiguity["detected"]:
            return {
                "valid": False,
                "reason": f'{label} contains vague, unresolved language ("{ambiguity["matchedPhrase"]}").',
                "guidance": f'Please replace "{ambiguity["matchedPhrase"]}" with the actual decision -- stating exactly what should happen helps avoid leaving it for the model to guess.',
            }

    contradiction = detect_field_contradiction(task)
    if contradiction["detected"]:
        return {
            "valid": False,
            "reason": f'Constraints say not to do "{contradiction["conflictingTerm"]}", but that same thing is required elsewhere in the task.',
            "guidance": "Could you resolve this contradiction before dispatch -- either remove it from Constraints, or remove the requirement from Objective/Scope/Success criteria/Expected output?",
        }

    tier = task.get("complexityTier")
    if not tier:
        return {"valid": False, "reason": "Complexity tier is missing.", "guidance": f"Please set complexityTier to one of: {', '.join(VALID_TIERS)} -- this determines which models are even eligible to receive this task."}
    if tier not in VALID_TIERS:
        return {"valid": False, "reason": f'Complexity tier "{tier}" is not recognized.', "guidance": f"Please use one of: {', '.join(VALID_TIERS)}."}

    if tier != "mechanical":
        known_context_failure = check_field(task.get("knownContext"), "Known context",
                                             "Read task-tightening.ts's existing TightTask type and validateTightTask() before extending them")
        if known_context_failure:
            return {
                "valid": False,
                "reason": f'Complexity tier "{tier}" requires understanding an existing component, but no known context was supplied -- {known_context_failure["reason"]}',
                "guidance": f'Please add knownContext describing what you already know or have read about the existing code or state this task touches. {known_context_failure["guidance"]}',
            }

    runnable_command_failure = check_success_criteria_has_runnable_command(task.get("successCriteria"))
    if runnable_command_failure:
        return runnable_command_failure

    return {"valid": True}


def parse_labeled_fields(prompt_text):
    """Extract ## OBJECTIVE / ## SCOPE / etc. sections from a prompt. Returns
    None if no labeled headers are present at all (legacy free-text prompt --
    not this validator's concern, must not be retroactively blocked)."""
    headers = list(FIELD_HEADER_RE.finditer(prompt_text))
    if not headers:
        return None
    fields = {}
    key_map = {
        "OBJECTIVE": "objective", "SCOPE": "scope", "SUCCESS_CRITERIA": "successCriteria",
        "EXPECTED_OUTPUT": "expectedOutput", "CONSTRAINTS": "constraints",
        "COMPLEXITY_TIER": "complexityTier", "KNOWN_CONTEXT": "knownContext",
        "FILE_PATHS": "filePaths",
    }
    for i, m in enumerate(headers):
        name = m.group(1).upper()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(prompt_text)
        fields[key_map[name]] = prompt_text[start:end].strip()
    return fields


def extract_hold_for_owner_signoff(fields):
    """Scans EXPECTED_OUTPUT and CONSTRAINTS (only -- the two sections a
    dispatch prompt already uses for hold/merge-affecting directives) for a
    literal `HOLD_FOR_OWNER_SIGNOFF: true` marker line. Returns False if
    absent, or explicitly set to false -- callers must not assume a missing
    marker means "hold", only an explicit true does."""
    for key in ("expectedOutput", "constraints"):
        text = fields.get(key) or ""
        m = HOLD_FOR_OWNER_SIGNOFF_RE.search(text)
        if m:
            return m.group(1).lower() == "true"
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"valid": True, "note": "usage: tight_task_validation.py <prompt_file>"}))
        sys.exit(0)
    with open(sys.argv[1]) as f:
        text = f.read()
    fields = parse_labeled_fields(text)
    if fields is None:
        print(json.dumps({
            "valid": True,
            "note": "legacy free-text prompt, no labeled fields found -- not validated, not blocked",
            "holdForOwnerSignoff": False,
        }))
        sys.exit(0)
    result = validate_tight_task(fields)
    result["holdForOwnerSignoff"] = extract_hold_for_owner_signoff(fields)
    print(json.dumps(result))
    sys.exit(0 if result.get("valid") else 1)
