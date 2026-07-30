#!/usr/bin/env python3
"""
VERIDIAN Prompt Gateway - Layer 0 Deterministic Router
========================================================
task-20260730-owner-engine-layer0 (root cause: KE-20260730-041850-63cf /
gateway_output_20260730.json -- a real governance/policy instruction from
the Owner scored category=QUERY, confidence=0.3263 under the existing
CHAT_CATEGORIES scoring in engine/classifier.py, and
determine_lifecycle_route() in gateway.py has no branch for anything other
than TASK/status-with-id/complete-spec -- so the message produced a saved
chat record (gateway.py's _save_chat_record() already runs unconditionally)
but zero real, structured, actionable persistence and zero owner-facing
signal that anything needed attention.

This module mirrors compliance-tracker/src/lib/services/chat-service.ts's
tryDeterministicRoute() pattern: a small, ordered set of real regex SHAPES
checked BEFORE engine/classifier.py's keyword/pattern scoring ever runs.
When one matches, it short-circuits straight to a real, disclosed action
-- zero scoring, zero ambiguity -- exactly the same "zero LLM/API cost when
it matches" property tryDeterministicRoute() documents for its own callers.
When nothing here matches, callers fall through to the existing
ChatClassifier.classify() unchanged (this module never replaces it, only
runs earlier).

IMPORTANT, disclosed limitation: engine/classifier.py's own module
docstring says "ALL processing is done by SOFTWARE (deterministic Python
code), NOT by AI" -- there is no LLM/AI call anywhere in gateway.py today.
This module therefore does NOT sit "before an AI fallback" in the literal
sense; it sits before the EXISTING deterministic keyword/pattern
classifier, adding a second, higher-precision deterministic pass for three
shapes that classifier's argmax-over-fixed-categories scoring structurally
cannot separate from unrelated categories (see GOVERNANCE_PATTERNS below).

Priority order (checked in this order, first match wins):
  1. GOVERNANCE   - policy/correction/standing-rule statements from the
                     Owner. Checked first because a long governance
                     paragraph legitimately contains "how will..." /
                     "what..." phrasing (see the real failing input) that
                     would otherwise satisfy TASK_DISPATCH or STATUS_QUERY
                     patterns incidentally.
  2. TASK_DISPATCH - explicit "build/fix/implement/create X" with a clear
                     subject.
  3. STATUS_QUERY  - "what is the status of X" / "is X done" without
                     necessarily naming a formal task-YYYYMMDD-HHMMSS id
                     (gateway.py's existing STATUS_QUERY_WORDS_RE only
                     fires when a TASK_ID entity is already present).
"""
import re

# =============================================================================
# GOVERNANCE / POLICY DIRECTIVE PATTERNS
# =============================================================================
# Deliberately phrase-based, not single keywords: single words like "ensure"
# or "issue" are far too common in ordinary CODE/OPS/ANALYSIS requests to be
# a governance signal on their own. Every pattern below is a multi-word shape
# that is realistically only used for standing-rule / correction / root-cause
# language, matching the real failing input's own wording (task-20260730,
# gateway_output_20260730.json machine_prompt) almost verbatim.
GOVERNANCE_PATTERNS = [
    re.compile(r"\bgoing\s+forward\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\s+on\b", re.IGNORECASE),
    re.compile(r"\bwe\s+identified\s+(this|the)\s+issue\b", re.IGNORECASE),
    re.compile(r"\broot\s+cause\b", re.IGNORECASE),
    re.compile(r"\bensure\s+that\b", re.IGNORECASE),
    re.compile(r"\bneed\s+to\s+understand\s+(your\s+understanding|how)\b", re.IGNORECASE),
    re.compile(r"\bsingle\s+point\s+of\s+entry\b", re.IGNORECASE),
    re.compile(r"\bno\s+new\s+(cron\s+job|table|metadata|\.py|\.yaml)\b", re.IGNORECASE),
    re.compile(r"\b(existing\s+are\s+used|use\s+existing|not\s+(a\s+)?new\s+(table|script|cron))\b", re.IGNORECASE),
    re.compile(r"\bversioning\s+is\s+there\b", re.IGNORECASE),
    re.compile(r"\btraceability\s+is\s+there\b", re.IGNORECASE),
    re.compile(r"\b100\s*%\s+end\s+to\s+end\b", re.IGNORECASE),
    re.compile(r"\bnot\s+assumption\b", re.IGNORECASE),
    re.compile(r"\bfor\s+any\s+work\s+that\s+is\s+given\s+by\s+owner\b", re.IGNORECASE),
    re.compile(r"\bevery\s+time\s+(going\s+forward|this\s+happens)\b", re.IGNORECASE),
]

# =============================================================================
# TASK DISPATCH PATTERNS (explicit imperative verb + real subject)
# =============================================================================
# Reuses the same imperative-verb vocabulary CHAT_CATEGORIES["CODE"]/["TASK"]
# already encode in config.py, but as an explicit, prioritized, standalone
# check -- so a message like the real failing input (which also contains the
# word "build" nowhere, but easily could in another real Owner message
# alongside governance language) is decided by GOVERNANCE first, not by
# incidental TASK_DISPATCH pattern presence deeper in a long paragraph.
TASK_DISPATCH_PATTERNS = [
    re.compile(r"^\s*(build|fix|implement|create|add|write|refactor|debug)\s+(a\s+|the\s+|an\s+)?\S+", re.IGNORECASE),
    re.compile(r"\bplease\s+(build|fix|implement|create|add|write)\s+\S+", re.IGNORECASE),
    re.compile(r"\b(build|fix|implement|create)\s+(a|the|an)\s+\w+.{3,}", re.IGNORECASE),
]

# =============================================================================
# STATUS / COMPLETION QUERY PATTERNS (no formal task-id required)
# =============================================================================
STATUS_QUERY_PATTERNS = [
    re.compile(r"\bwhat(?:'s|\s+is)\s+the\s+status\s+of\b", re.IGNORECASE),
    re.compile(r"\bis\s+.{2,80}?\s+(done|complete|completed|finished)\b", re.IGNORECASE),
    re.compile(r"\bhas\s+.{2,80}?\s+been\s+(completed|done|finished|merged|shipped|deployed)\b", re.IGNORECASE),
    re.compile(r"\b(status|progress)\s+(of|on)\s+\S+", re.IGNORECASE),
]


def _first_match(text, patterns):
    for p in patterns:
        m = p.search(text)
        if m:
            return p.pattern, m.group(0)
    return None, None


def classify_layer0(raw_text: str) -> "dict | None":
    """
    Returns None if no Layer 0 pattern matches (caller falls through to
    ChatClassifier.classify() unchanged). Otherwise returns:
        {
            "layer0_category": "GOVERNANCE_DIRECTIVE" | "TASK_DISPATCH" | "STATUS_QUERY",
            "matched_pattern": str,   # the regex source that matched
            "matched_text": str,      # the literal substring matched
        }
    Pure function: no I/O, no DB, no subprocess -- unit-testable in isolation,
    same discipline plan_generator.py's split_objective_into_steps() and
    tryDeterministicRoute()'s own shape-matching documents for themselves.
    """
    if not raw_text or not raw_text.strip():
        return None

    pattern, matched_text = _first_match(raw_text, GOVERNANCE_PATTERNS)
    if pattern:
        return {"layer0_category": "GOVERNANCE_DIRECTIVE", "matched_pattern": pattern, "matched_text": matched_text}

    pattern, matched_text = _first_match(raw_text, TASK_DISPATCH_PATTERNS)
    if pattern:
        return {"layer0_category": "TASK_DISPATCH", "matched_pattern": pattern, "matched_text": matched_text}

    pattern, matched_text = _first_match(raw_text, STATUS_QUERY_PATTERNS)
    if pattern:
        return {"layer0_category": "STATUS_QUERY", "matched_pattern": pattern, "matched_text": matched_text}

    return None


if __name__ == "__main__":
    # Self-test, same convention as engine/classifier.py's own __main__ block.
    tests = [
        ("governance (real failing input, shortened)",
         "GOING FORWARD, FOR ANY WORK THAT IS GIVEN BY OWNER, we identified this issue "
         "and need to understand your understanding, not assumption. ensure that "
         "no new cron job, table, metadata is written."),
        ("task dispatch", "Build a script that reconciles umr_tasks against work_items nightly"),
        ("status query, no task id", "What is the status of the OAuth refresh fix?"),
        ("status query, completion", "Is the dedup masking fix done yet?"),
        ("ordinary code request (intentionally now routes as TASK_DISPATCH per task step 2a)",
         "Write a Python script to parse a CSV file"),
        ("ordinary query (should fall through)", "How do I deploy the Next.js app to production?"),
    ]
    for label, text in tests:
        result = classify_layer0(text)
        print(f"{label}: {result}")
