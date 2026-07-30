#!/usr/bin/env python3
"""
Dispatch-time pre-flight gate closing a real gap found 2026-07-26: the
standing rule "Supabase schema changes are tier2-by-definition, always held
for human sign-off, never auto-merged" was only enforced at the PR-merge
step (task_lifecycle_state_machine.S9_AUDITED/S10b_HELD_FOR_SIGNOFF in
PROTOCOL_OWNER_AI.yaml), not before a headless worker takes a live action.
task-20260726-071400-migration-drift-audit-and-reconciliation executed live
DROP TABLE/CREATE TABLE/ALTER TABLE/CREATE INDEX statements against the
production Supabase project (pcrjmlpuqsbocqfwoxod) via the Supabase MCP
apply_migration tool, entirely before any PR/CI/human review happened --
because its own dispatch prompt's SCOPE section told it to, and nothing in
the dispatch pipeline stopped that prompt from authorizing it. The worker did
exactly what its prompt said; the gap was a missing check on what a dispatch
prompt is allowed to authorize, not a misbehaving worker.

Same CLI contract as tight_task_validation.py, the check task-gateway.py's
cmd_start() already chains at this exact insertion point: invoked as
`python3 ddl_authorization_check.py <prompt-file>`, prints one JSON blob with
a "valid" boolean (plus "reason"/"guidance" on rejection) on stdout, exits 0
if valid else 1.

Fails closed: any reference to a DDL-capable Supabase MCP tool (apply_migration,
execute_sql, merge_branch -- see DDL_CAPABLE_TOOL_NAMES below), or any SQL
DDL/DCL keyword -- schema forms (CREATE/DROP TABLE, CREATE INDEX/CREATE
UNIQUE INDEX/DROP INDEX, ALTER TABLE, TRUNCATE, CREATE/DROP POLICY,
CREATE/DROP TRIGGER, ADD/DROP COLUMN, ADD CONSTRAINT, CREATE/DROP TYPE,
CREATE SCHEMA, CREATE EXTENSION, CREATE SEQUENCE, CREATE/DROP VIEW,
CREATE/DROP/ALTER FUNCTION, COMMENT ON) and privilege-escalation forms
(GRANT, REVOKE, CREATE/ALTER/DROP ROLE, CREATE/ALTER/DROP USER, SECURITY
DEFINER, REASSIGN OWNED -- see DDL_KEYWORD_PATTERNS below) anywhere in the
prompt-file's text is rejected UNLESS the text also contains a line literally
matching `PRE-APPROVED-LIVE-DDL:` followed by a real reference: a decision-log
entry ID (the `KE-<date>-<time>-<hex>` knowledge_engine ID format, or an
`OWNER_DECISIONS_NEEDED_<date>.yaml#<id>` citation -- the same
approved_via/decision-log convention already used elsewhere in this pipeline,
see PROTOCOL_OWNER_AI.yaml's OWNER_DECISIONS_NEEDED_2026-07-23.yaml
description), or a dated free-text note of meaningful length (e.g. "Owner
approved via Slack DM on 2026-07-25, see #ops-approvals thread"). A bare
non-empty string like `PRE-APPROVED-LIVE-DDL: yes` is NOT a citation and does
not pass -- it references nothing that can be checked. Matching is
case-insensitive and scans the whole prompt-file text (not line-by-line), so
multi-line/multi-statement SQL blocks and lowercase/mixed-case SQL are all
covered the same as single-line uppercase SQL. Plain SELECT-style read-only
SQL never matches any of the DDL keywords below and so never triggers this
gate.

A KE-<date>-<time>-<hex> or OWNER_DECISIONS_NEEDED_<date>.yaml citation is
verified for real EXISTENCE, not just shape: a KE-style ID must appear
literally somewhere under ai-os/ (checked via a full-text scan of that
directory at validation time -- see _ke_id_exists_on_disk below), and an
OWNER_DECISIONS_NEEDED_<date>.yaml reference must name a file that actually
exists on disk at ai-os/<that filename> (see _owner_decisions_file_exists
below). A citation matching the right shape but naming an ID/file that does
not exist anywhere -- e.g. a fabricated `KE-20260726-999999-dead` nobody ever
recorded -- is rejected, not accepted on format alone. This closes the round-2
gap where the check validated only the citation's STRING SHAPE via regex and
a fabricated-but-well-formed reference passed; a round-3 live test confirmed
that gap was real. The dated-free-text-note path (below MIN_DATED_NOTE_LENGTH
check) has NO equivalent existence check -- there is nothing on disk to look
up for free text like "Owner approved via Slack DM on 2026-07-25, see
#ops-approvals thread" -- so that path remains format-only and, honestly, a
worker could still fabricate a plausible-sounding dated note. Prefer a
KE-id/decision-file citation over a free-text note when authoring a real
approval for exactly this reason.

Known limitations (accepted, not solved by this gate):

1. This is a DISPATCH-TIME scan of the prompt-file's TEXT, not a RUNTIME
   interceptor of the actual tool call. It catches a dispatch prompt that
   tells a worker to run live DDL. It does NOT catch a worker whose prompt
   says nothing about DDL but which decides, mid-task, to call
   apply_migration/execute_sql/merge_branch anyway -- this session's agent
   framework has no mechanism to intercept a live MCP tool call while a
   worker is running, only to vet the prompt before the worker is dispatched.
   Closing that gap would require a runtime tool-call interceptor (e.g. an
   MCP-proxy-level policy check), which is a materially different and larger
   piece of infrastructure than a prompt-text scan and is explicitly out of
   scope here.
2. The existence check above proves the cited ID/file is a real record that
   exists somewhere under ai-os/ -- it does NOT prove that record is a
   genuine Owner approval OF THIS SPECIFIC DDL ACTION. A worker could in
   principle cite a real KE-id or decision file that exists for an unrelated
   reason. Fully closing that would require the check to parse and semantically
   match the cited record's content against the specific DDL statements found,
   which is out of scope here; treat the existence check as raising the bar
   from "any well-formed string" to "a real, findable record", not as a
   content/intent audit.

Treat this gate as a real but partial control: it stops a prompt from
authorizing live DDL up front with a fabricated or free-floating citation, it
does not guarantee no live DDL happens after dispatch, and it does not
semantically verify a cited real record actually approves this action.

PHASE 2 UNIFICATION (2026-07-26): this gate was a genuine 3rd disconnected
policy gate -- it shipped after
ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's phase_2_policy_rule_decision_unification
closed with exactly 2 gates unified (preflight-guard.py, policy-enforcement-engine.ts),
and it emitted its own ad hoc {valid,reason,guidance} shape instead of
scripts/policy_decision.py's shared PolicyDecision envelope -- flagged by
ai-os/MASTER_INDEX.yaml's own registries.engines_gateways_architecture.phase_2_scope_judgment_2026_07_26
entry as the one real follow-up Phase 2 owed. Closed here: check_ddl_authorization()'s
return dict now carries an additive "policy_decision" key (a full
policy_decision_schema envelope, source_gate="ddl_authorization_check.py:check_ddl_authorization")
alongside the original valid/reason/guidance/ddl_references_found/pre_approved_reference
keys, unchanged -- same additive, non-breaking discipline Phase 2 used on
preflight-guard.py/risk-tier.py. See ai-os/POLICY_GATE_REGISTRY_2026-07-26.yaml for
the full gate registry this is now a member of.
"""
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_OS_DIR = os.path.join(REPO_ROOT, "ai-os")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from policy_decision import emit_allow, emit_deny, make_explanation  # noqa: E402

SOURCE_GATE = "ddl_authorization_check.py:check_ddl_authorization"

DDL_KEYWORD_PATTERNS = {
    "CREATE TABLE": re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE),
    "ALTER TABLE": re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE),
    "DROP TABLE": re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    "CREATE UNIQUE INDEX": re.compile(r"\bCREATE\s+UNIQUE\s+INDEX\b", re.IGNORECASE),
    "CREATE INDEX": re.compile(r"\bCREATE\s+INDEX\b", re.IGNORECASE),
    "DROP INDEX": re.compile(r"\bDROP\s+INDEX\b", re.IGNORECASE),
    "TRUNCATE": re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    "CREATE POLICY": re.compile(r"\bCREATE\s+POLICY\b", re.IGNORECASE),
    "DROP POLICY": re.compile(r"\bDROP\s+POLICY\b", re.IGNORECASE),
    "CREATE TRIGGER": re.compile(r"\bCREATE\s+TRIGGER\b", re.IGNORECASE),
    "DROP TRIGGER": re.compile(r"\bDROP\s+TRIGGER\b", re.IGNORECASE),
    "ADD COLUMN": re.compile(r"\bADD\s+COLUMN\b", re.IGNORECASE),
    "DROP COLUMN": re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE),
    "ADD CONSTRAINT": re.compile(r"\bADD\s+CONSTRAINT\b", re.IGNORECASE),
    "CREATE TYPE": re.compile(r"\bCREATE\s+TYPE\b", re.IGNORECASE),
    "DROP TYPE": re.compile(r"\bDROP\s+TYPE\b", re.IGNORECASE),
    "CREATE SCHEMA": re.compile(r"\bCREATE\s+SCHEMA\b", re.IGNORECASE),
    "CREATE EXTENSION": re.compile(r"\bCREATE\s+EXTENSION\b", re.IGNORECASE),
    "CREATE SEQUENCE": re.compile(r"\bCREATE\s+SEQUENCE\b", re.IGNORECASE),
    "CREATE VIEW": re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\b", re.IGNORECASE),
    "DROP VIEW": re.compile(r"\bDROP\s+(?:MATERIALIZED\s+)?VIEW\b", re.IGNORECASE),
    "CREATE FUNCTION": re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", re.IGNORECASE),
    "ALTER FUNCTION": re.compile(r"\bALTER\s+FUNCTION\b", re.IGNORECASE),
    "DROP FUNCTION": re.compile(r"\bDROP\s+FUNCTION\b", re.IGNORECASE),
    "COMMENT ON": re.compile(r"\bCOMMENT\s+ON\b", re.IGNORECASE),
    # Privilege-escalation DCL/DDL primitives -- arguably more dangerous than
    # the schema-shape statements above since they change WHO can do what,
    # not just the shape of the data. Real gap found 2026-07-26 round 3: all
    # of these previously passed through undetected.
    "GRANT": re.compile(r"\bGRANT\b", re.IGNORECASE),
    "REVOKE": re.compile(r"\bREVOKE\b", re.IGNORECASE),
    "CREATE ROLE": re.compile(r"\bCREATE\s+ROLE\b", re.IGNORECASE),
    "ALTER ROLE": re.compile(r"\bALTER\s+ROLE\b", re.IGNORECASE),
    "DROP ROLE": re.compile(r"\bDROP\s+ROLE\b", re.IGNORECASE),
    "CREATE USER": re.compile(r"\bCREATE\s+USER\b", re.IGNORECASE),
    "ALTER USER": re.compile(r"\bALTER\s+USER\b", re.IGNORECASE),
    "DROP USER": re.compile(r"\bDROP\s+USER\b", re.IGNORECASE),
    # SECURITY DEFINER makes a function run with its OWNER's privileges
    # rather than the caller's -- the classic Postgres privilege-escalation
    # primitive, matched standalone (not just adjacent to CREATE FUNCTION)
    # since it can equally appear in an ALTER FUNCTION statement and the
    # clause itself, wherever it appears, is the escalation vector.
    "SECURITY DEFINER": re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE),
    "REASSIGN OWNED": re.compile(r"\bREASSIGN\s+OWNED\b", re.IGNORECASE),
}

# Every Supabase MCP tool name available to this session whose invocation can
# run or push live DDL against a database: apply_migration and execute_sql
# both take a caller-authored "query" string executed directly against
# Postgres (apply_migration's own tool description: "Use this when executing
# DDL operations"; execute_sql's: "Executes raw SQL in the Postgres
# database"), and merge_branch pushes a dev branch's accumulated migrations
# live to production ("Merges migrations and edge functions from a
# development branch to production"). Branch-lifecycle tools that don't take
# caller-authored SQL/DDL text (create_branch, reset_branch, rebase_branch,
# delete_branch) are deliberately excluded -- they manage branches, they
# don't execute arbitrary DDL a prompt-file could smuggle through.
DDL_CAPABLE_TOOL_NAMES = ["apply_migration", "execute_sql", "merge_branch"]

DDL_TOOL_PATTERN = re.compile(
    r"\b(?:mcp__[\w]*Supabase[\w]*__)?(?:" + "|".join(DDL_CAPABLE_TOOL_NAMES) + r")\b",
    re.IGNORECASE,
)

APPROVAL_LINE_RE = re.compile(r"^\s*PRE-APPROVED-LIVE-DDL:\s*(.*)$", re.MULTILINE)

PLACEHOLDER_REFERENCE_RE = re.compile(
    r"^(tbd|todo|n/?a|none|null|undefined|yes|approved|xxx+|\.\.\.|fill.?in|pending|<.*>)$",
    re.IGNORECASE,
)

# A real decision-log citation: either the knowledge_engine KE-<date>-<time>-<hex>
# ID format, or a direct OWNER_DECISIONS_NEEDED_<date>.yaml[#<id>] file reference
# -- both are real, grep-able record formats already in use elsewhere in this
# pipeline (see PROTOCOL_OWNER_AI.yaml's approved_via/decision-log convention).
# Matching this SHAPE is necessary but not sufficient -- see
# _ke_id_exists_on_disk / _owner_decisions_file_exists below, which check the
# cited ID/file is a real record, not just a well-formed string.
KE_ID_RE = re.compile(r"KE-\d{8}-\d{6}-[0-9a-f]{4}", re.IGNORECASE)
OWNER_DECISIONS_FILE_RE = re.compile(r"OWNER_DECISIONS_NEEDED_\d{4}-\d{2}-\d{2}\.ya?ml", re.IGNORECASE)

DATED_NOTE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# A dated free-text approval note has to actually say something -- "ok
# 2026-07-25" has a date but isn't a citation of anything. This is a floor,
# not a guarantee of truthfulness; it's here to reject one-word/one-date
# non-answers, not to verify the note's contents.
MIN_DATED_NOTE_LENGTH = 25


def find_ddl_references(text):
    """Real hits, in DDL_KEYWORD_PATTERNS order, plus any DDL-capable Supabase
    MCP tool name found last. Empty list means no DDL-executing language was
    found."""
    hits = [label for label, pattern in DDL_KEYWORD_PATTERNS.items() if pattern.search(text)]
    tool_hit = DDL_TOOL_PATTERN.search(text)
    if tool_hit:
        hits.append(tool_hit.group(0).rsplit("__", 1)[-1].lower())
    return hits


def _ke_id_exists_on_disk(ke_id):
    """True if the literal KE-<date>-<time>-<hex> string appears anywhere in
    a file under ai-os/ -- i.e. it's a real recorded ID, not just a
    well-formed one. A full-text scan (ai-os/ is ~11MB across ~50 files, cheap
    to re-scan per check) rather than an index, since there's no existing
    index of KE IDs to consult."""
    if not os.path.isdir(AI_OS_DIR):
        return False
    for root, _dirs, files in os.walk(AI_OS_DIR):
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    if ke_id in f.read():
                        return True
            except OSError:
                continue
    return False


def _owner_decisions_file_exists(filename):
    """True if ai-os/<filename> actually exists on disk. Only the file's
    existence is checked, not that any #<id> fragment on the citation matches
    a specific entry inside it -- see the module docstring's residual-gap
    note."""
    return os.path.isfile(os.path.join(AI_OS_DIR, filename))


def is_real_reference(reference):
    """A citation, not a rephrasing: either a decision-log entry ID/file
    reference in one of this pipeline's real record formats THAT ACTUALLY
    EXISTS on disk under ai-os/, or a dated free-text note long enough to
    actually say something. A bare word like "yes" or "approved" is neither,
    and a well-formed but fabricated KE-id/decision-file citation is rejected
    too -- shape alone is not enough."""
    if not reference or PLACEHOLDER_REFERENCE_RE.match(reference):
        return False
    ke_match = KE_ID_RE.search(reference)
    if ke_match:
        return _ke_id_exists_on_disk(ke_match.group(0))
    file_match = OWNER_DECISIONS_FILE_RE.search(reference)
    if file_match:
        return _owner_decisions_file_exists(file_match.group(0))
    if DATED_NOTE_RE.search(reference) and len(reference) >= MIN_DATED_NOTE_LENGTH:
        return True
    return False


def find_pre_approval(text):
    """Returns the first PRE-APPROVED-LIVE-DDL: reference that passes
    is_real_reference(), or None if no line matches or every match fails."""
    for m in APPROVAL_LINE_RE.finditer(text):
        reference = m.group(1).strip()
        if is_real_reference(reference):
            return reference
    return None


def check_ddl_authorization(text):
    hits = find_ddl_references(text)
    if not hits:
        decision = emit_allow(
            source_gate=SOURCE_GATE, reason_code="no_ddl_language_found",
            detail="No DDL/DCL keyword or DDL-capable Supabase MCP tool name found in this prompt-file.",
        )
        return {"valid": True, "policy_decision": decision.to_dict()}

    reference = find_pre_approval(text)
    if reference:
        explanation = make_explanation(
            summary="Live DDL pre-approved by a real, verified citation.",
            reasoning=f"Matched DDL language ({', '.join(hits)}) but a valid PRE-APPROVED-LIVE-DDL citation ({reference}) was found and verified to exist.",
        )
        decision = emit_allow(
            source_gate=SOURCE_GATE, reason_code="ddl_pre_approved",
            detail=f"pre_approved_reference={reference}",
            explanation=explanation, evidence=hits,
        )
        return {
            "valid": True, "ddl_references_found": hits, "pre_approved_reference": reference,
            "policy_decision": decision.to_dict(),
        }

    explanation = make_explanation(
        summary="Live DDL language found with no valid pre-approval citation.",
        reasoning=(
            f"Matched DDL/DCL language ({', '.join(hits)}) and no PRE-APPROVED-LIVE-DDL: line cited a "
            "real, existing decision-log record or a sufficiently detailed dated note."
        ),
        recommended_action="Remove the DDL instruction from SCOPE and open a migration PR for human review instead.",
    )
    decision = emit_deny(
        source_gate=SOURCE_GATE, reason_code="ddl_authorization_required",
        detail=f"ddl_references_found={hits}", explanation=explanation, evidence=hits,
    )
    return {
        "valid": False,
        "reason": (
            "ddl_authorization_required: this prompt-file references live-DDL-executing "
            f"language ({', '.join(hits)}) with no PRE-APPROVED-LIVE-DDL: citation. "
            "Supabase schema changes (and any other live DDL) are tier2-by-definition and "
            "must be held for human sign-off before a worker is ever dispatched to run them "
            "-- this gate enforces that rule at dispatch time, not only at PR-merge time."
        ),
        "guidance": (
            "If the Owner has genuinely pre-approved this specific live DDL action out of "
            "band, add a line `PRE-APPROVED-LIVE-DDL: <real citation>` to this prompt-file -- "
            "a decision-log entry ID (KE-<date>-<time>-<hex>, or an "
            "OWNER_DECISIONS_NEEDED_<date>.yaml#<id> reference), or a dated approval note of "
            "meaningful length. A bare word like `yes` is not a citation and will not pass. "
            "Otherwise, remove the DDL instruction from SCOPE and have the worker open a "
            "migration PR for human review instead, the same as every other Supabase schema "
            "change."
        ),
        "ddl_references_found": hits,
        "policy_decision": decision.to_dict(),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"valid": True, "note": "usage: ddl_authorization_check.py <prompt_file>"}))
        sys.exit(0)
    with open(sys.argv[1]) as f:
        prompt_text = f.read()
    result = check_ddl_authorization(prompt_text)
    print(json.dumps(result))
    sys.exit(0 if result.get("valid") else 1)
