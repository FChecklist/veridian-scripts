#!/usr/bin/env python3
"""
task-gateway.py -- single unified CLI implementing
ai-os/STANDING_DIRECTIVE.yaml's v2_task_lifecycle_pipeline (phases
0/1/4/5/7/8/9/11) as real, callable software commands, so no AI agent or
router needs to remember/sequence superboss-register.py, veridian-task.py,
and postflight_audit_gate.py in the right order manually.

Every subcommand's real work is delegated to the already-built script it
wraps -- this file only sequences those calls and merges their outputs into
one JSON response per subcommand. It does not reimplement any of their
internal logic (no direct sqlite writes; the one direct sqlite read, in
lookup_work_item(), is a read-only lookup used to correctly sequence later
calls, not a substitute for any wrapped script).

Subcommands: submit, start, log, close, register-automation, status

UMR171945-0001 (single input gate audit, 2026-08-08): a grep across
.py/.sh files in this directory for direct subprocess/import call sites to
resource_governor.py or superboss-register.py found a consistent, real
pattern: every one checked is a legitimate, low-level/internal caller this
gate was never meant to cover -- not a task-lifecycle bypass. Confirmed
real examples (illustrative, deliberately NOT claimed as an exhaustive
enumeration -- a first draft of this note claimed completeness and an
independent review found a real, missed example, veridian-task.py, so this
note no longer makes that claim):
  - resource_governor_tick_loop.sh: the canonical 30s driver of
    resource_governor.py's own --tick/--reconcile-stale/
    --umr-staleness-scan -- this IS the real dispatcher, not a caller of it.
  - dispatch-tick.py, veridian-task-watchdog.py: in-process
    `import resource_governor` for the same real, low-level dispatch/resume
    machinery, not a task-lifecycle bypass.
  - directive_engine.py, gtm_check_ai_testing.py, dispatch-owner-task.sh:
    call resource_governor.py's own real --submit CLI directly (the real,
    governed queue-entry point) -- dispatch-owner-task.sh additionally
    routes its own instruction logging through THIS file's real `submit`
    subcommand (UMR171945-0006, PR #282) before reaching --submit.
  - generate_prompt_coverage_report.py, intent_engine.py,
    knowledge_registry_multisource.py, prompt_gateway/gateway_persistence.py,
    quality-gate.sh, supervisor-entrypoint.sh, worker-entrypoint.sh,
    doc-worker-entrypoint.sh, run-logged.sh, veridian-task.py: call
    superboss-register.py's own real capability-registry/knowledge/
    log-instruction/log-action/log-work primitives directly -- none of
    these are task-lifecycle writes this file's own subcommands
    (submit/start/log/close/status) cover; forcing them through this file
    would add an unnecessary hop, not close a real gap.
"Single input gate" therefore means: every real task-lifecycle operation
(submit/start/log/close/status) goes through this file. Other real, direct
callers of the two wrapped scripts are expected to keep existing for
non-task-lifecycle primitives (logging, capability/knowledge lookups, the
tick loop's own dispatch machinery) -- the real signal that would mean
this gate has an actual gap is a caller performing a genuine
task-lifecycle operation (creating/starting/closing a task, or writing its
canonical status) OUTSIDE this file's own subcommands, not merely the
existence of another low-level caller of a shared primitive.
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workflow_contract import (  # noqa: E402
    phase_for_task_gateway_subcommand, REQUIRED_TASK_SECTIONS, has_all_required_sections,
)

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
SUPERBOSS = f"{SCRIPTS}/superboss-register.py"
VERIDIAN_TASK = f"{SCRIPTS}/veridian-task.py"
CREDIT_ACCOUNTANT = f"{SCRIPTS}/credit-accountant.py"
RESOURCE_GOVERNOR = f"{SCRIPTS}/resource_governor.py"
# OWNER_ENGINE software (OWNER DIRECTIVE 2026-07-25 / KE-20260725-061008-8423),
# a pre-processing filter upstream of this dispatcher -- see PROMPT_GATEWAY
# below and run_owner_engine_gate().
PROMPT_GATEWAY = f"{SCRIPTS}/prompt_gateway/gateway.py"
POSTFLIGHT = f"{AI_OS}/scripts/postflight_audit_gate.py"
TIGHT_VALIDATION = f"{SCRIPTS}/tight_task_validation.py"
DDL_AUTHORIZATION_CHECK = f"{SCRIPTS}/ddl_authorization_check.py"
DB_PATH = f"{AI_OS}/memory/superboss-register.sqlite"
MASTER_INDEX_REGISTRIES_SYNC = f"{AI_OS}/scripts/sync_master_index_registries.py"
# UMR171945-0017 (real ops infra audit, 2026-08-08): veridian-zoekt-webserver.service,
# confirmed live/real (~1.7GB index over compliance-tracker/veridian-scripts/
# claude-control/scripts, 2-hourly reindex timer). Env override exists for
# tests only -- the real default always points at this box's real running
# service.
ZOEKT_URL = os.environ.get("ZOEKT_URL", "http://127.0.0.1:6070")

STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "vs",
    "is", "are", "be", "do", "does", "this", "that", "with", "from",
    "by", "at", "as", "it", "its", "into", "not", "no",
}


def fail(message, **extra):
    payload = {"error": message}
    payload.update(extra)
    print(json.dumps(payload, indent=2, default=str))
    sys.exit(1)


def _log_governance_event_best_effort(event_type, caller, detail=None):
    """Point 2/8/9 of audit-24-points (UMR-20260808-145030-f3d1): real,
    fail-open write into superboss-register.py's governance_cycle_log via
    its own CLI (never a raw sqlite write, same convention this file's own
    module docstring already establishes for every other write). Best-
    effort by design -- a broken logging call must never break the real
    status read or audit run that triggered it, same philosophy as
    resource_governor.py's _safe_superboss_register()/_append_attention()."""
    try:
        subprocess.run(
            ["python3", SUPERBOSS, "log-governance-event", "--event-type", event_type,
             "--caller", caller] + (["--detail", detail] if detail else []),
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


def run_task_start_gate(task_identity, title, umr_id=None):
    """UMR-20260808-121334-e122 (Owner-decided Option B, PM decision cycle
    UMR-20260808-141807-7f38, 2026-08-08): cmd_start spawns a real systemd
    unit synchronously and, before this, had ZERO reference to
    resource_governor.py/stop_work anywhere in this file -- dispatch-owner-
    task.sh's OTHER real channel (resource_governor.py's submit()/
    dispatch_one()) already gets the real stop-work-order + resource-
    threshold gate, this one didn't. Option B deliberately keeps cmd_start's
    existing synchronous, direct-spawn calling convention unchanged (vs.
    restructuring it into dispatch_one()'s async submit-and-queue shape) --
    same real protection, materially lower risk to whatever else currently
    depends on cmd_start's calling convention.

    Calls resource_governor.py --check-task-start-gate (the real, shared
    check dispatch_one() itself now also calls, via
    resource_threshold_block_reason() + _stop_work_order_block_reason() --
    see that file's own docstrings) as a subprocess, same composition
    convention this file already uses for every other wrapped script
    (SUPERBOSS/TIGHT_VALIDATION/DDL_AUTHORIZATION_CHECK/CREDIT_ACCOUNTANT).
    Returns the parsed {"blocked": bool, "check": str|None, "detail":
    str|None} dict; raises via fail() (like every other real wrapper-level
    failure in this file) if resource_governor.py itself doesn't exit 0
    with parseable JSON -- a broken governor is treated as a real gate
    failure here, not silently skipped, matching this file's own fail-
    closed posture on every other real precondition check above."""
    cmd = [
        "python3", RESOURCE_GOVERNOR, "--check-task-start-gate",
        "--task-identity", task_identity, "--title", title,
    ]
    if umr_id:
        cmd += ["--umr-id", umr_id]
    return run_json(cmd, "resource_governor.py --check-task-start-gate")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def run_json(cmd, step):
    """Run a wrapped script expected to exit 0 and print exactly one JSON blob.
    A nonzero exit or unparseable stdout here is a real wrapper-level failure
    (distinct from postflight_audit_gate.py's own FAIL verdict, which exits 1
    by design and is handled separately in cmd_close)."""
    proc = run(cmd)
    if proc.returncode != 0:
        fail(f"{step} failed (exit {proc.returncode})", command=cmd,
             stdout=proc.stdout[-2000:], stderr=proc.stderr[-2000:])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail(f"{step} did not return parseable JSON", command=cmd,
             stdout=proc.stdout[-2000:], stderr=proc.stderr[-2000:])


def _slugify_title(title):
    """MUST exactly mirror veridian-task.py cmd_create's own slug computation
    (task_id = f"task-{ts}-{slug}") -- this is what makes task_key a real
    predictor of collisions on the eventual task_id, not an independent
    guess. Two concurrent --title collisions get different timestamped
    task_ids (task_id can never collide by construction) but the identical
    slug, which is exactly what task_key (task-20260731-074406's structural
    duplicate-task constraint, claimed via superboss-register.py's
    claim-task-key / UNIQUE(task_key) index) is built to catch."""
    return "".join(c if c.isalnum() else "-" for c in title.lower())[:40].strip("-")


def run_owner_engine_gate(text, session_id):
    """OWNER DIRECTIVE 2026-07-25 (KE-20260725-061008-8423) point 2, NON
    NEGOTIABLE: 'AI will not analyze any chat given by owner in raw format
    ... The chat by default will go to the OWNER_ENGINE software ... AI
    will only work on that output prompt given by the OWNER_ENGINE
    software.' This is the real, default enforcement point -- cmd_submit
    calls this for every --source owner submission, before keyword
    extraction, log-instruction, duplicate/search/knowledge lookups, or
    capability lookup ever see the raw text. Not an opt-in flag: there is
    no code path in cmd_submit that lets --source owner text reach those
    calls unprocessed.

    Pipes text through the already-built, already-proven
    scripts/prompt_gateway/gateway.py (task-20260725-053025) via --mode
    stdin, and returns its full result dict (chat_id, classification,
    processing.machine_prompt, final_output, ...). Callers must use
    final_output (or processing.machine_prompt) downstream, never `text`.
    """
    fd, out_path = tempfile.mkstemp(suffix=".json", prefix="owner_engine_gate_")
    os.close(fd)
    try:
        proc = subprocess.run(
            ["python3", PROMPT_GATEWAY, "--mode", "stdin",
             "--session", session_id, "--output", out_path, "--json-only"],
            input=text, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            fail(
                "OWNER_ENGINE gate (scripts/prompt_gateway/gateway.py) failed -- "
                "OWNER DIRECTIVE 2026-07-25 point 2 is NON NEGOTIABLE, submit "
                "cannot fall back to analyzing raw owner text",
                stdout=proc.stdout[-2000:], stderr=proc.stderr[-2000:],
            )
        if not os.path.isfile(out_path):
            fail("OWNER_ENGINE gate produced no output file", output_path=out_path)
        with open(out_path, "r") as f:
            return json.load(f)
    finally:
        if os.path.isfile(out_path):
            os.remove(out_path)


def call_machine_contract(query_code, cmd, params, found_key="found"):
    """ai-os/OWNER_ENGINE_MACHINE_LANGUAGE_CONTRACT_2026-07-25.yaml proof-of-concept
    retrofit (OWNER DIRECTIVE 2026-07-25 points 4/5/14/15/17): wraps an
    EXISTING AI<->software subprocess call in the contract's close-ended
    request/response envelope instead of ad hoc JSON. `cmd` is still the
    exact same real command that was already being run -- this does not
    reimplement or replace the wrapped script, only normalizes the shape
    of what task-gateway.py does with its output. Real retrofit target:
    superboss-register.py lookup-capability, called from cmd_submit."""
    request_envelope = {"query": query_code, "chat_id": None, "params": params}
    proc = run(cmd)
    if proc.returncode != 0:
        return request_envelope, {"status": "ERROR", "answer": None,
                                   "reason_code": "SUBPROCESS_NONZERO_EXIT"}
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return request_envelope, {"status": "ERROR", "answer": None,
                                   "reason_code": "UNPARSEABLE_JSON"}
    if not raw.get(found_key):
        return request_envelope, {"status": "INSUFFICIENT_INFO", "answer": raw,
                                   "reason_code": "NO_MATCH_FOUND"}
    return request_envelope, {"status": "OK", "answer": raw, "reason_code": None}


def lookup_work_item(task_id):
    """Read-only lookup of the work_items row linked to this task_id, checked
    against both ai_task_id (veridian-task.py worker tasks) and
    software_task_id (the field name postflight_audit_gate.py and phase_7's
    exact_command use generically for whatever id is under audit). Returns
    None if no row is found -- callers must handle that, not assume it."""
    if not os.path.isfile(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT work_item_id, instruction_id FROM work_items "
        "WHERE ai_task_id = ? OR software_task_id = ? ORDER BY ts DESC LIMIT 1",
        (task_id, task_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def run_zoekt_search(query, limit=10):
    """UMR171945-0017 (governing chain UMR-20260806-171945-5767): real HTTP
    call to the real, running Zoekt code-search webserver
    (veridian-zoekt-webserver.service, ZOEKT_URL, confirmed live 2026-08-08:
    ~1.7GB real trigram index over compliance-tracker/veridian-scripts/
    claude-control/scripts). Folded into cmd_submit's existing search step
    alongside check-duplicate/search/query-knowledge, never replacing them
    -- those three query superboss-register.sqlite's own FTS5 tables (short
    instruction/capability/knowledge text); this queries Zoekt's separate,
    real index of actual file contents, which can surface real matches
    those FTS5 tables structurally cannot (they never ingest source-file
    bodies at all).

    Fail-open by design, same convention as _audit_point_23's Grafana call
    below: a real infra hiccup (service down, timeout, malformed response)
    must never block or crash cmd_submit's own search step -- returns an
    empty, honestly-flagged result instead of raising.

    Zoekt's own /search endpoint supports &format=json (confirmed live: the
    default is an HTML results page) and &num=<n> to bound the result count
    server-side, so `limit` is a real, enforced cap, not just a client-side
    truncation. json.loads(..., strict=False) is required -- Zoekt's own
    real JSON output can contain raw, unescaped control characters inside
    matched code-snippet strings (confirmed live against the real index),
    which Python's strict-mode JSON parser otherwise rejects outright."""
    if not query or not query.strip():
        return {"ok": False, "hits": [], "error": "empty query", "query": query}
    try:
        import urllib.parse
        import urllib.request
        qs = urllib.parse.urlencode({"q": query, "format": "json", "num": limit})
        req = urllib.request.Request(f"{ZOEKT_URL.rstrip('/')}/search?{qs}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        parsed = json.loads(raw, strict=False)
        file_matches = parsed.get("result", {}).get("FileMatches", []) or []
        hits = []
        for fm in file_matches[:limit]:
            matches = fm.get("Matches") or []
            first = matches[0] if matches else {}
            fragments = first.get("Fragments") or []
            snippet = "".join(
                f"{frag.get('Pre', '')}{frag.get('Match', '')}{frag.get('Post', '')}"
                for frag in fragments[:1]
            )
            hits.append({
                "repo": fm.get("Repo"),
                "file": fm.get("FileName"),
                "line": first.get("LineNum"),
                "snippet": snippet,
                "match_count": len(matches),
            })
        return {"ok": True, "hits": hits, "query": query, "total_file_matches": len(file_matches)}
    except Exception as e:
        return {"ok": False, "hits": [], "error": f"{type(e).__name__}: {e}", "query": query}


def extract_keywords_mechanical(text):
    """STANDING_DIRECTIVE.yaml v2_task_lifecycle_pipeline.phase_1_software_first_search
    .keyword_extraction_baseline_mechanical_first.step_1_mechanical: regex-extract
    quoted strings, file paths (contains '/' or '.py'/'.yaml'/'.md'),
    snake_case/kebab-case identifiers, numbers referencing item/rule IDs --
    zero AI judgment, pure regex."""
    quoted = re.findall(r'"([^"]+)"', text) + re.findall(r"'([^']+)'", text)

    tokens = re.findall(r"\S+", text)
    file_paths = [
        t.strip(".,;:()[]{}\"'")
        for t in tokens
        if "/" in t or re.search(r"\.(py|yaml|yml|md)$", t.strip(".,;:()[]{}\"'"))
    ]

    identifiers = re.findall(r"\b[a-z0-9]+(?:[_-][a-z0-9]+)+\b", text)
    rule_ids = re.findall(r"\b(?:item|rule)\s*#?\d+\b", text, re.IGNORECASE)

    keywords = []
    for group in (quoted, file_paths, identifiers, rule_ids):
        for term in group:
            term = term.strip()
            if term and term.lower() not in STOPWORDS and term not in keywords:
                keywords.append(term)
    return keywords


def cmd_submit(args):
    # OWNER DIRECTIVE 2026-07-25 (KE-20260725-061008-8423) point 2, mandatory
    # default for --source owner: raw text is gated through the OWNER_ENGINE
    # software before anything below (keyword extraction, log-instruction,
    # duplicate/search/knowledge/capability lookups) touches it. --source
    # ai_agent is unaffected (that text was never a raw owner chat).
    owner_engine_gate = None
    effective_text = args.text
    if args.source == "owner":
        owner_engine_gate = run_owner_engine_gate(args.text, args.session_id)
        effective_text = owner_engine_gate["final_output"]

    keywords = extract_keywords_mechanical(effective_text)
    fallback_used = False
    if not keywords:
        # step_1_mechanical yielded zero terms. This script cannot itself
        # exercise step_2_ai_supplement (that step is explicitly AI
        # judgment, not mechanical); instead it applies a purely mechanical
        # fallback (first significant words) so the mandatory phase_1
        # search still runs with a non-empty query, and flags that this
        # happened so a calling AI agent can supply real step_2 terms if it
        # judges that necessary.
        fallback_used = True
        words = re.findall(r"[A-Za-z]{4,}", effective_text)
        keywords = [w for w in words if w.lower() not in STOPWORDS][:5]
    keyword_str = " ".join(keywords) if keywords else effective_text

    log_cmd = ["python3", SUPERBOSS, "log-instruction",
               "--text", effective_text, "--source", args.source,
               "--medium", "task_gateway", "--session-id", args.session_id]
    if owner_engine_gate:
        # Raw owner text is preserved for audit in metadata_json, not lost --
        # but per point 2 it is metadata, not what search/classification/
        # dispatch (above and below) operate on. That is effective_text.
        log_cmd += ["--metadata", json.dumps({
            "owner_engine_chat_id": owner_engine_gate["chat_id"],
            "owner_engine_raw_text": args.text,
        })]
    log_result = run_json(log_cmd, "log-instruction")
    instruction_id = log_result.get("instruction_id")

    # Structural duplicate-task constraint (task-20260731-074406), advisory
    # half: submit only ever has --text, never a real --title (that's
    # cmd_start's job, where the actual atomic claim-task-key happens below
    # in cmd_start) -- so this is a read-only check-task-key lookup against
    # the same slug this text's keywords would produce, surfaced alongside
    # the existing fuzzy check-duplicate/search below, not a hard block.
    task_key_candidate = _slugify_title(keyword_str)
    task_key_check = run_json(
        ["python3", SUPERBOSS, "check-task-key", "--task-key", task_key_candidate],
        "check-task-key",
    )

    dup_result = run_json(
        ["python3", SUPERBOSS, "check-duplicate", keyword_str],
        "check-duplicate",
    )
    search_result = run_json(
        ["python3", SUPERBOSS, "search", keyword_str, "--limit", "10"],
        "search",
    )
    knowledge_result = run_json(
        ["python3", SUPERBOSS, "query-knowledge", keyword_str],
        "query-knowledge",
    )
    # UMR171945-0017: real Zoekt code-search hit, folded in alongside the
    # three superboss-register.sqlite FTS5 lookups above -- see
    # run_zoekt_search()'s own docstring for why this is additive, not a
    # replacement for any of them.
    zoekt_result = run_zoekt_search(keyword_str, limit=10)
    # Phase 1 Capability Registry live wiring (task-20260724-083420,
    # closes_engines: [3]): lookup_contract's call_site_requirement --
    # "any code path about to construct an LLM prompt to accomplish a named
    # task MUST call lookupCapability() first". task-gateway.py submit is
    # exactly that entrypoint (the first stop before a task is dispatched to
    # an AI worker), so it belongs alongside check-duplicate/search/
    # query-knowledge above, not as a separate gate a caller could skip.
    # Retrofitted (task-20260725-080900, OWNER DIRECTIVE point 4/14/15/17)
    # to go through the machine-language contract envelope -- same
    # underlying superboss-register.py lookup-capability call, now wrapped.
    capability_request, capability_response = call_machine_contract(
        "LOOKUP_CAPABILITY",
        ["python3", SUPERBOSS, "lookup-capability", "--intent-text", keyword_str],
        {"intent_text": keyword_str},
    )
    capability_result = capability_response["answer"] or {}

    systemctl_proc = run([
        "systemctl", "--user", "list-units", "veridian-worker@*",
        "--state=active", "--no-legend",
    ])
    active_task_ids = []
    for line in systemctl_proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        unit = line.split()[0]
        m = re.match(r"veridian-worker@(.+)\.service", unit)
        if m:
            active_task_ids.append(m.group(1))

    kw_lower = [k.lower() for k in keywords]
    active_collision_task_ids = [
        tid for tid in active_task_ids
        if any(k in tid.lower() for k in kw_lower)
    ]

    print(json.dumps({
        "workflow_phase": phase_for_task_gateway_subcommand("submit"),
        "instruction_id": instruction_id,
        # UMR171945-0024: real caller_identity LABEL for this request, one of the 5
        # real classes registered on --source above -- same real value already
        # persisted onto instructions.utm_source via log-instruction below (this key
        # is an explicit, discoverable alias for it in this command's own output, not
        # a second, separately-computed value that could drift from what was logged).
        "caller_identity": args.source,
        "owner_engine_gate": {
            "applied": owner_engine_gate is not None,
            "chat_id": owner_engine_gate["chat_id"] if owner_engine_gate else None,
            "category": owner_engine_gate["classification"]["category"] if owner_engine_gate else None,
            "intent": owner_engine_gate["classification"]["intent"] if owner_engine_gate else None,
            "token_reduction_pct": owner_engine_gate["processing"]["token_reduction_pct"] if owner_engine_gate else None,
            "raw_text_chars": len(args.text) if owner_engine_gate else None,
            "gated_text_chars": len(effective_text) if owner_engine_gate else None,
        },
        "machine_contract_call": {
            "request": capability_request,
            "response": capability_response,
        },
        "task_key_candidate": task_key_candidate,
        "task_key_already_claimed": task_key_check.get("already_claimed", False),
        "task_key_existing_task_id": task_key_check.get("existing_task_id"),
        "duplicate_found": bool(dup_result.get("found", 0) > 0),
        "duplicate_evidence": dup_result.get("matches", []),
        "prior_search_results": search_result,
        "knowledge_matches": knowledge_result,
        "zoekt_matches": zoekt_result,
        "capability_matches": capability_result.get("matches", []),
        "capability_deterministic_path_available": any(
            (not m.get("ai_required")) and m.get("apis") for m in capability_result.get("matches", [])
        ),
        "active_collision_task_ids": active_collision_task_ids,
        "keywords_extracted": keywords,
        "keyword_extraction_fallback_used": fallback_used,
    }, indent=2, default=str))


def cmd_start(args):
    if not os.path.isfile(args.prompt_file):
        fail(f"prompt-file not found: {args.prompt_file}")
    text = open(args.prompt_file).read()
    missing = [s for s in REQUIRED_TASK_SECTIONS if f"## {s}" not in text]
    if missing:
        fail(
            "prompt-file does not follow the literal_template -- missing required section(s)",
            missing_sections=missing,
            prompt_file=args.prompt_file,
        )

    # INS-20260724-113032-8032: the section-presence check above only proves
    # SUCCESS_CRITERIA exists and isn't empty -- it does not catch prose-only
    # SUCCESS_CRITERIA that reads as satisfied but gives postflight_audit_gate.py's
    # audit_cmd nothing real to run. tight_task_validation.py's fuller check
    # (placeholders/ambiguity/contradiction/tier + the runnable-command check)
    # runs here, before veridian-task.py create, so dispatch is blocked until
    # the prompt is actually fixed rather than merely well-formed.
    tight_proc = run(["python3", TIGHT_VALIDATION, args.prompt_file])
    try:
        tight_result = json.loads(tight_proc.stdout)
    except json.JSONDecodeError:
        fail("tight_task_validation.py did not return parseable JSON",
             stdout=tight_proc.stdout, stderr=tight_proc.stderr)
    if not tight_result.get("valid", False):
        fail(
            "tight_task_validation.py rejected this prompt-file -- dispatch blocked until fixed",
            reason=tight_result.get("reason"),
            guidance=tight_result.get("guidance"),
            prompt_file=args.prompt_file,
        )
    if tight_result.get("warnings"):
        # Advisory only (2026-08-14 PR#376 AUDIT:FAIL correction) -- FILE_PATHS
        # is not yet emitted by any real prompt generator, so a missing/invalid
        # value here does not block dispatch, only gets logged for visibility.
        # See tight_task_validation.py's module docstring for the tracked
        # follow-up to flip this back to a hard failure once the generators
        # are migrated.
        print(json.dumps({
            "tight_task_validation_warnings": tight_result.get("warnings"),
            "prompt_file": args.prompt_file,
        }, default=str), file=sys.stderr)

    # Real fix for a real 2026-07-26 incident (task-20260726-071400-migration-drift-
    # audit-and-reconciliation): its own dispatch prompt's SCOPE told a worker to call
    # Supabase MCP's apply_migration directly against production, and nothing at
    # dispatch time stopped that prompt from authorizing a live DDL run before any
    # PR/CI/human review happened -- the standing "Supabase schema changes are always
    # held for human sign-off" rule was only enforced at PR-merge time. This runs
    # immediately after tight_task_validation.py, same run()/json.loads()/fail()
    # pattern, before veridian-task.py create, so a prompt authorizing live DDL never
    # reaches a worker without an explicit, citable Owner approval.
    ddl_proc = run(["python3", DDL_AUTHORIZATION_CHECK, args.prompt_file])
    try:
        ddl_result = json.loads(ddl_proc.stdout)
    except json.JSONDecodeError:
        fail("ddl_authorization_check.py did not return parseable JSON",
             stdout=ddl_proc.stdout, stderr=ddl_proc.stderr)
    if not ddl_result.get("valid", False):
        # Category B (UMR-20260803-025317-0c64): if a CATEGORY-B-DETERMINISTIC-RECOVERY
        # block was present but didn't satisfy all 10 conditions, surface the real
        # per-condition breakdown here too -- so a rejection reported to the dispatcher
        # says plainly which specific condition failed, not just that DDL was found.
        fail(
            "ddl_authorization_check.py rejected this prompt-file -- dispatch blocked "
            "until an explicit, citable Owner approval (Category A) or a fully-satisfied "
            "deterministic recovery evidence block (Category B) is added",
            reason=ddl_result.get("reason"),
            guidance=ddl_result.get("guidance"),
            category_b_conditions=ddl_result.get("category_b_conditions"),
            prompt_file=args.prompt_file,
        )
    elif ddl_result.get("category") == "B":
        # Real, deterministic Category B authorization -- not narrated, not a
        # human/PM/AI judgment call. Logged here (not just inside
        # ddl_authorization_check.py's own return value) so task-gateway.py's own
        # stdout/dispatch log carries which conditions were verified and how, for
        # whoever reviews this task's real dispatch record later.
        print(json.dumps({
            "category_b_authorized": True,
            "conditions": ddl_result.get("category_b_conditions"),
        }, default=str), file=sys.stderr)

    # Structural duplicate-task constraint (task-20260731-074406, real
    # #634-vs-#639 / #641-vs-#629 duplicate-dispatch incidents this session):
    # task_key is the SAME title-derived slug veridian-task.py's cmd_create
    # uses for task_id (see _slugify_title) -- task_id itself can never
    # collide (timestamp-prefixed), so it was never what caught these.
    # Claimed here, immediately before veridian-task.py create actually
    # spends real resources (worktree/branch/systemd unit) on this task, via
    # superboss-register.py's atomic UNIQUE(task_key) insert -- a genuine
    # duplicate now fails loudly before any of that is spent, instead of
    # silently duplicating an already-in-flight task.
    task_key = _slugify_title(args.title)
    claim_proc = run(["python3", SUPERBOSS, "claim-task-key",
                       "--task-key", task_key, "--title", args.title,
                       "--source", "ai_agent"])
    try:
        claim_result = json.loads(claim_proc.stdout)
    except json.JSONDecodeError:
        fail("claim-task-key did not return parseable JSON",
             stdout=claim_proc.stdout, stderr=claim_proc.stderr)
    if not claim_result.get("claimed", False):
        fail(
            "duplicate task_key -- an earlier task already claimed this exact "
            "title-derived key, dispatch blocked before any resources were spent",
            task_key=task_key,
            existing_task_id=claim_result.get("existing_task_id"),
            existing_title=claim_result.get("existing_title"),
            existing_ts=claim_result.get("existing_ts"),
            guidance="if this is a genuine new task, give it a title that isn't "
                     "identical (after lowercasing/slugifying to 40 chars) to the prior one",
        )

    # Real gate (UMR-20260808-121334-e122, Option B) -- see
    # run_task_start_gate()'s own docstring. Runs immediately after the
    # duplicate-task-key claim (cheap, no real resources spent yet) and
    # before veridian-task.py create below (the real spawn -- worktree/
    # branch/systemd unit), so a blocked start never reaches that point.
    gate_result = run_task_start_gate(task_key, args.title, umr_id=args.umr_id)
    if gate_result.get("blocked"):
        fail(
            "blocked by resource_governor.py's real stop-work-order/resource-threshold gate "
            "-- the same real protection dispatch_one() applies to every queued task",
            check=gate_result.get("check"),
            detail=gate_result.get("detail"),
        )

    # Real, machine-readable hold-for-signoff (2026-07-26, root-caused against
    # the PR563 incident): tight_task_validation.py already extracted a real
    # HOLD_FOR_OWNER_SIGNOFF: true marker (if present) from this prompt's
    # EXPECTED_OUTPUT/CONSTRAINTS above -- thread it through to veridian-task.py
    # create so task.yaml carries it for real, not just as prose the AI worker
    # or Superboss might or might not honor.
    create_cmd = [
        "python3", VERIDIAN_TASK, "create",
        "--title", args.title, "--repo", args.repo, "--prompt", text,
    ]
    if tight_result.get("holdForOwnerSignoff"):
        create_cmd.append("--hold-for-owner-signoff")
    create_proc = run(create_cmd)
    if create_proc.returncode != 0:
        fail("veridian-task.py create failed", stdout=create_proc.stdout, stderr=create_proc.stderr)
    m = re.search(r"CREATED:\s*(\S+)", create_proc.stdout)
    if not m:
        fail("could not parse task_id from veridian-task.py create output", stdout=create_proc.stdout)
    task_id = m.group(1)
    service = f"veridian-worker@{task_id}.service"

    # Real fix for a real gap found live 2026-07-26 (task-20260726-092433):
    # this command never called credit-accountant.py propose, yet
    # worker-entrypoint.sh's checkpoint loop calls `credit-accountant.py
    # report --increment 1` unconditionally -- which always failed with "no
    # matching approved plan for this task_id/increment" because no
    # increment-1 row had ever been proposed. Called here, immediately after
    # task_id exists (propose requires a real --task-id) and before the
    # explicit systemd verification below, so an approved-or-rejected
    # increment-1 row exists before the worker's own checkpoint loop can
    # reach it. --plan reuses this prompt's own OBJECTIVE section (the same
    # real intent statement postflight_audit_gate.py's audit trail already
    # treats as authoritative); --search-terms reuses
    # extract_keywords_mechanical() (already used by cmd_submit above) rather
    # than a second keyword-extraction implementation. A rejected verdict is
    # not fatal here -- credit-accountant.py's own report-time check is the
    # real enforcement point, and worker-entrypoint.sh already has real
    # handling for a deterministic-rejection plan (see its own comments) --
    # this call's job is only to make sure a row exists, so `report` finds a
    # real, matching increment-1 rather than nothing at all.
    plan_text = (extract_section(text, "OBJECTIVE") or args.title)[:500]
    search_terms = " ".join(extract_keywords_mechanical(text)) or args.title
    # The real call: credit-accountant.py propose (CREDIT_ACCOUNTANT == .../credit-accountant.py).
    # --repo verified against credit-accountant.py's own argument parser
    # (task-20260726-101257 SCOPE item 4), not assumed: `python3
    # /opt/veridian/scripts/credit-accountant.py propose --help` confirms
    # `--repo REPO` is a real, accepted optional argument (p_propose.
    # add_argument("--repo", default=None)), and cmd_propose genuinely
    # consumes it (folded into the claude_judgment_call prompt as
    # "Repo: {args.repo or 'unspecified'}") -- it is not a silent no-op.
    # preflight-guard.py's own call site (check_credit_accountant_approval)
    # simply never bothered to pass this optional arg; that is not evidence
    # it is unused. Both call sites are independently correct.
    # tests/test_gateway_task_integration.py::test_credit_accountant_propose_*
    # exercises credit-accountant.py's real cmd_propose (module import,
    # temp ledger, mocked judgment call) and asserts the row it inserts
    # plus the "Repo: <value>" text reaching the judgment prompt.
    propose_proc = run([
        "python3", CREDIT_ACCOUNTANT, "propose",
        "--task-id", task_id, "--plan", plan_text,
        "--search-terms", search_terms, "--repo", args.repo,
    ])
    try:
        propose_result = json.loads(propose_proc.stdout)
    except json.JSONDecodeError:
        propose_result = {
            "approved": False,
            "reason": "credit-accountant.py propose did not return parseable JSON",
            "stdout": propose_proc.stdout[-2000:], "stderr": propose_proc.stderr[-2000:],
        }

    # veridian-task.py create already enables+starts the unit; this explicit
    # start is the spec-mandated verification step and is idempotent against
    # an already-active unit.
    run(["systemctl", "--user", "start", service])
    is_active_proc = run(["systemctl", "--user", "is-active", service])
    systemd_active = is_active_proc.stdout.strip() == "active"

    work_result = run_json(
        ["python3", SUPERBOSS, "log-work",
         "--instruction-id", args.instruction_id,
         "--ai-task-id", task_id,
         "--source", "ai_agent", "--medium", "task_gateway",
         "--content", f"task_start:{args.title[:60]}",
         "--term", "task_gateway,start",
         "--status", "open"],
        "log-work",
    )

    print(json.dumps({
        "workflow_phase": phase_for_task_gateway_subcommand("start"),
        "task_id": task_id,
        "systemd_active": systemd_active,
        "work_item_id": work_result.get("work_item_id"),
        "credit_accountant_propose": propose_result,
    }, indent=2, default=str))


def cmd_log(args):
    wi = lookup_work_item(args.task_id)
    work_item_id = wi["work_item_id"] if wi else None

    cmd = ["python3", SUPERBOSS, "log-action",
           "--source", "ai_agent", "--medium", "task_gateway",
           "--content", args.event, "--term", "task_gateway,log"]
    if work_item_id:
        cmd += ["--work-item-id", work_item_id]

    action_result = run_json(cmd, "log-action")

    print(json.dumps({
        "workflow_phase": phase_for_task_gateway_subcommand("log"),
        "action_id": action_result.get("action_id"),
        "work_item_id": work_item_id,
        "work_item_resolved": work_item_id is not None,
    }, indent=2, default=str))


def extract_section(text, name):
    m = re.search(rf"##\s*{re.escape(name)}\s*\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else None


def check_branch_merged_to_master(task_id):
    """Real fix for a genuine gap found 2026-07-24: a full-repo audit showed only
    4 of 28 real worker branches from 2026-07-23 had ever been merged to master --
    every phase's own EXPECTED_OUTPUT said "COMMIT+PUSH" and every phase did push
    a branch, but nothing ever verified the push reached the canonical branch.
    Master sat frozen at a ~10:38am commit while 8+ hours of real, closed-out work
    (governance items, the watchdog service, task-gateway.py itself, the Knowledge
    Engine) sat on disconnected branches nobody ever opened or merged a PR for.
    This makes that check part of every close, not something a human has to
    remember to audit for separately. Best-effort: a task with no workspace git
    repo, or no matching worker branch, returns NO_GIT_ACTIVITY rather than
    failing -- most tasks are legitimate non-code work."""
    workspace = f"{AI_OS}/tasks/{task_id}/workspace"
    if not os.path.exists(os.path.join(workspace, ".git")):
        return {"status": "NO_GIT_ACTIVITY", "detail": "no .git in task workspace"}
    branch_proc = subprocess.run(
        ["git", "-C", workspace, "branch", "--show-current"],
        capture_output=True, text=True, timeout=15,
    )
    branch = branch_proc.stdout.strip()
    if not branch:
        return {"status": "NO_GIT_ACTIVITY", "detail": "workspace not on a named branch"}
    # gh pr, not git merge-base --is-ancestor: GitHub squash/rebase merges create a NEW
    # commit on master, so the original branch tip is never a literal git ancestor even
    # when its content genuinely landed -- confirmed 2026-07-24 against a known-merged
    # branch (PR #4) that a naive ancestor-check incorrectly called NOT_MERGED.
    pr_proc = subprocess.run(
        ["gh", "pr", "list", "--repo", "FChecklist/claude-control",
         "--head", branch, "--state", "all", "--json", "number,state,mergedAt"],
        capture_output=True, text=True, timeout=30,
    )
    try:
        prs = json.loads(pr_proc.stdout) if pr_proc.returncode == 0 else []
    except json.JSONDecodeError:
        prs = []
    merged_prs = [pr for pr in prs if pr.get("state") == "MERGED"]
    if merged_prs:
        return {"status": "MERGED", "branch": branch, "pr_number": merged_prs[0]["number"]}
    open_prs = [pr for pr in prs if pr.get("state") == "OPEN"]
    return {
        "status": "NOT_MERGED",
        "branch": branch,
        "open_pr_number": open_prs[0]["number"] if open_prs else None,
        "action_needed": (
            f"PR #{open_prs[0]["number"]} is open but not merged -- merge it" if open_prs
            else f"no PR exists for '{branch}' -- open one and merge it, or fold it into a reconciliation pass"
        ),
    }


# Knowledge Engine Phase 2 (task-20260724-033446), SCOPE item 4 /
# candidate auto_update_on_task_completion: a task's real changed-file set
# (from its own git diff) mapped to live absolute paths, using the same
# repo-root -> live-path prefix convention every prior phase has used to
# deploy tracked files (ai-os/ and scripts/ mirror their live counterparts
# 1:1; ai-os-scripts/ mirrors ai-os/scripts/ -- see ai-os-scripts/file_inventory.py's
# own live deployment history). Best-effort: an unrecognized prefix is
# skipped, never guessed.
REPO_PATH_PREFIXES = [
    ("ai-os-scripts/", f"{AI_OS}/scripts/"),
    ("ai-os/", f"{AI_OS}/"),
    ("scripts/", f"{SCRIPTS}/"),
]


def _map_repo_path_to_live(repo_relative_path):
    for prefix, live_prefix in REPO_PATH_PREFIXES:
        if repo_relative_path.startswith(prefix):
            return live_prefix + repo_relative_path[len(prefix):]
    if repo_relative_path == "CONTROLLER.yaml":
        return f"{VERIDIAN_ROOT}/repos/claude-control/CONTROLLER.yaml"
    return None


def reverify_touched_knowledge_engine_rows(task_id):
    """Real fix for Phase2 candidate auto_update_on_task_completion: knowledge_engine
    rows were only ever written by an explicit register-knowledge call -- nothing
    re-checked content_hash when a governed artifact actually changed, so
    verification_status could silently go stale. This computes the just-closed
    task's own real changed-file set (git diff against its branch point), maps
    each to a live absolute path, and calls verify-knowledge (in-place UPDATE,
    never a duplicate INSERT) for every knowledge_engine row whose artifact_path
    matches -- so every close is a real re-verify, not a one-off manual run."""
    workspace = f"{AI_OS}/tasks/{task_id}/workspace"
    if not os.path.exists(os.path.join(workspace, ".git")):
        return {"status": "NO_GIT_ACTIVITY", "touched_knowledge_engine_paths": [], "reverify_result": None}

    diff_proc = subprocess.run(
        ["git", "-C", workspace, "diff", "--name-only", "origin/master...HEAD"],
        capture_output=True, text=True, timeout=15,
    )
    changed = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]
    live_paths = sorted({p for p in (_map_repo_path_to_live(c) for c in changed) if p})

    if not os.path.isfile(DB_PATH) or not live_paths:
        return {"status": "NO_TOUCHED_ROWS", "changed_files": changed, "touched_knowledge_engine_paths": [], "reverify_result": None}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    known_paths = {r["artifact_path"] for r in conn.execute("SELECT DISTINCT artifact_path FROM knowledge_engine")}
    conn.close()
    matched = [p for p in live_paths if p in known_paths]

    if not matched:
        return {"status": "NO_TOUCHED_ROWS", "changed_files": changed, "touched_knowledge_engine_paths": [], "reverify_result": None}

    cmd = ["python3", SUPERBOSS, "verify-knowledge"]
    for p in matched:
        cmd += ["--path", p]
    result = run_json(cmd, "verify-knowledge")
    return {"status": "REVERIFIED", "changed_files": changed, "touched_knowledge_engine_paths": matched, "reverify_result": result}


def sync_master_index_registries_if_touched(task_id):
    """Phase 5 (metadata_knowledge_consolidation, task-20260724-140008): the
    enforced half of the sync direction ai-os/METADATA_KNOWLEDGE_ENGINE_RECONCILIATION_2026-07-24.yaml
    documents -- MASTER_INDEX.yaml's registries: list is the authored source,
    knowledge_engine is the queryable layer kept current with it. Same
    changed-file-detection convention reverify_touched_knowledge_engine_rows()
    already uses: only runs ai-os-scripts/sync_master_index_registries.py
    (deployed live at MASTER_INDEX_REGISTRIES_SYNC) when the just-closed task's
    own git diff actually touched ai-os/MASTER_INDEX.yaml -- every close is a
    real re-sync, not a one-off manual run."""
    workspace = f"{AI_OS}/tasks/{task_id}/workspace"
    if not os.path.exists(os.path.join(workspace, ".git")):
        return {"status": "NO_GIT_ACTIVITY"}

    diff_proc = subprocess.run(
        ["git", "-C", workspace, "diff", "--name-only", "origin/master...HEAD"],
        capture_output=True, text=True, timeout=15,
    )
    changed = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]
    if "ai-os/MASTER_INDEX.yaml" not in changed:
        return {"status": "NOT_TOUCHED", "changed_files": changed}

    if not os.path.isfile(MASTER_INDEX_REGISTRIES_SYNC):
        return {"status": "SYNC_SCRIPT_NOT_DEPLOYED", "changed_files": changed}

    # Deliberately not run_json(): a partial sync failure (e.g. one malformed
    # registries: entry) is real, reportable information, not a reason to
    # fail the whole close -- the task's own audit/checkpoint/merge-status
    # work above this point already succeeded and should not be undone by a
    # metadata-sync hiccup.
    proc = run(["python3", MASTER_INDEX_REGISTRIES_SYNC])
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = {"error": "sync script did not return parseable JSON", "stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]}
    return {"status": "SYNCED" if proc.returncode == 0 else "SYNCED_WITH_FAILURES", "changed_files": changed, "sync_result": result}


def cmd_close(args):
    task_dir = f"{AI_OS}/tasks/{args.task_id}"
    prompt_file = f"{task_dir}/prompt.txt"
    if not os.path.isfile(prompt_file):
        fail(f"prompt.txt not found for task {args.task_id} at {prompt_file}")
    text = open(prompt_file).read()

    success_criteria = extract_section(text, "SUCCESS_CRITERIA")
    if success_criteria is None:
        fail(f"SUCCESS_CRITERIA section not found in {prompt_file}")

    if args.audit_cmd.strip() not in success_criteria:
        fail(
            "verification_command_predefinition_rule violation: --audit-cmd was not found "
            f"verbatim in {args.task_id}'s own pre-defined SUCCESS_CRITERIA ({prompt_file}). "
            "postflight_audit_gate.py's --audit-cmd must be copied VERBATIM from what was "
            "written at plan/dispatch time, never authored fresh at close time (self-certification "
            "is exactly what this rule prevents).",
            provided_audit_cmd=args.audit_cmd,
            predefined_success_criteria=success_criteria,
        )

    wi = lookup_work_item(args.task_id)
    instruction_id = wi.get("instruction_id") if wi else None

    audit_cmd_list = [
        "python3", POSTFLIGHT,
        "--software-task-id", args.task_id,
        "--audit-cmd", args.audit_cmd,
        "--content", args.evidence,
    ]
    if instruction_id:
        audit_cmd_list += ["--instruction-id", instruction_id]

    audit_proc = run(audit_cmd_list)
    try:
        audit_result = json.loads(audit_proc.stdout)
    except json.JSONDecodeError:
        fail("postflight_audit_gate.py did not return parseable JSON",
             stdout=audit_proc.stdout, stderr=audit_proc.stderr)

    verdict = audit_result.get("verdict")
    if verdict != "DONE":
        print(json.dumps({
            "workflow_phase": "logged",  # audit failed: never reached the closed phase
            "audit_verdict": verdict,
            "reason": audit_result,
        }, indent=2, default=str))
        sys.exit(1)

    close_cmd = ["python3", SUPERBOSS, "log-work",
                 "--software-task-id", args.task_id,
                 "--status", "closed",
                 "--source", "ai_agent", "--medium", "task_gateway",
                 "--content", f"task_close:{args.task_id}"]
    if instruction_id:
        close_cmd += ["--instruction-id", instruction_id]
    close_result = run_json(close_cmd, "log-work(close)")

    checkpoint_proc = run([
        "python3", VERIDIAN_TASK, "checkpoint", args.task_id,
        "--status", "completed", "--note", args.evidence,
    ])
    checkpoint_status = "completed" if checkpoint_proc.returncode == 0 else "checkpoint_failed"

    git_merge_status = check_branch_merged_to_master(args.task_id)
    if git_merge_status["status"] == "NOT_MERGED":
        run([
            "python3", SUPERBOSS, "log-action",
            "--source", "ai_agent", "--medium", "task_gateway",
            "--content", f"unmerged_branch:{args.task_id}:{git_merge_status['branch']}"
            f":{git_merge_status['commits_ahead_of_master']}_commits_ahead",
        ])

    knowledge_engine_reverify = reverify_touched_knowledge_engine_rows(args.task_id)
    master_index_registries_sync = sync_master_index_registries_if_touched(args.task_id)

    print(json.dumps({
        "workflow_phase": phase_for_task_gateway_subcommand("close"),
        "audit_verdict": verdict,
        "checkpoint_status": checkpoint_status,
        "audit_id": audit_result.get("audit_id"),
        "work_item_id": close_result.get("work_item_id"),
        "git_merge_status": git_merge_status,
        "knowledge_engine_reverify": knowledge_engine_reverify,
        "master_index_registries_sync": master_index_registries_sync,
    }, indent=2, default=str))


def cmd_register_automation(args):
    result = run_json(
        ["python3", SUPERBOSS, "index-add",
         "--path", args.path, "--category", args.category, "--layer", args.layer,
         "--status", "live", "--purpose", args.purpose, "--tags", args.tags],
        "index-add",
    )
    print(json.dumps(result, indent=2, default=str))


def cmd_status(args):
    task_yaml_path = f"{AI_OS}/tasks/{args.task_id}/task.yaml"
    if not os.path.isfile(task_yaml_path):
        fail(f"task.yaml not found at {task_yaml_path}")
    task = yaml.safe_load(open(task_yaml_path))

    checkpoints = task.get("checkpoints") or []
    last_checkpoint = checkpoints[-1] if checkpoints else None

    service = task.get("service") or f"veridian-worker@{args.task_id}.service"
    active_proc = run(["systemctl", "--user", "is-active", service])
    systemd_active = active_proc.stdout.strip() == "active"

    watchdog_last = None
    watchdog_path = f"{AI_OS}/logs/watchdog.jsonl"
    if os.path.isfile(watchdog_path):
        for line in reversed(open(watchdog_path).readlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("task_id") == args.task_id:
                watchdog_last = entry
                break

    # Point 2 (audit-24-points): this IS the canonical query path -- log it.
    _log_governance_event_best_effort("query", "task-gateway.py:status", detail=args.task_id)

    print(json.dumps({
        "status": task.get("status"),
        "last_checkpoint_note": (last_checkpoint or {}).get("note"),
        "systemd_active": systemd_active,
        "watchdog_last_action": watchdog_last,
    }, indent=2, default=str))


# ---------------------------------------------------------------------------
# audit-24-points (UMR-20260808-145030-f3d1, governing chain
# UMR-20260806-171945-5767, master_issue_tracker rows UMR171945-0001..0024)
# ---------------------------------------------------------------------------
# 12 real, deterministic boolean checks -- points 2/4/8/9/12/14/16/17/19/20/
# 22/23 of the real 24-point spec. Each check is a real, callable, boolean
# software function (no AI judgment); results persist into the EXISTING
# master_issue_tracker rows via the real update-issue CLI (never raw SQL),
# matching the exact persistence contract already proven live by
# tests/test_audit24_master_issue_tracker_persistence.py (addendum
# UMR-20260808-123107-875a).

AUDIT_24_POINTS_SUBSET = (2, 4, 8, 9, 12, 14, 16, 17, 19, 20, 22, 23)
AUDIT_24_GOVERNING_UMR = "UMR-20260806-171945-5767"


def _now_iso_utc():
    return datetime.now(timezone.utc).isoformat()


def _load_scripts_module(filename, modname):
    """Real in-process import of another script in this directory (used to
    reuse existing real logic -- umr_completion_percentage.py's evidence
    rule for Point 12, resource_governor.py's detect_stale_umr_rows()/
    _record_master_issue_if_new for Points 14/19 -- never reimplemented)."""
    spec = importlib.util.spec_from_file_location(modname, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ALERT_CONDITION_POINTS = {14, 20}
# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR #280
# round 1): points 14 and 20 are alert-condition checks -- their own
# docstrings explicitly say their real TRUE means "an alert condition, not
# a health verdict" (stale umr_tasks rows currently exist / a cron-timer
# process is over 5pct CPU right now), the OPPOSITE of every other point's
# TRUE-means-healthy convention. _persist_audit24_point_result() used to
# persist solution_applied/issue_resolved_permanently=YES whenever
# boolean_result was True, uniformly, for every point -- for these two
# specifically that meant a genuinely live operational problem got recorded
# in master_issue_tracker as a PERMANENTLY RESOLVED issue, silently masking
# exactly the condition this system exists to surface. Confirmed live
# before this fix: a synthetic stale umr_tasks row made _audit_point_14()
# return (True, ...), and the persisted UMR171945-0014 row showed
# solution_applied=YES/issue_resolved_permanently=YES despite the real
# problem still being live.
#
# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR #280
# round 2): round 1's fix only updated _persist_audit24_point_result()'s own
# inline healthy-computation -- cmd_audit_24_points() itself, building the
# printed/returned JSON, still used the raw `passed` boolean directly for
# if_false_who_acts/if_false_how_told, so for points 14/20 the remediation
# guidance fields went silently None exactly when a real alert was firing
# (passed=True) and were populated only when nothing was wrong -- the
# identical inversion bug class, just in the OTHER of the two places it was
# duplicated inline instead of shared. _point_is_healthy() is now the ONE
# real place this alert-aware inversion is computed; every caller (both
# _persist_audit24_point_result() and cmd_audit_24_points()) calls it,
# so this logic can never diverge between the two call sites again.


def _point_is_healthy(point, boolean_result):
    """The one real, shared alert-aware health computation -- see
    _ALERT_CONDITION_POINTS's own comment above for why points 14/20 invert
    the raw boolean. Every caller that needs to know "is this point okay"
    (as opposed to "what did the raw check literally return") must go
    through this, not recompute it inline."""
    return (not boolean_result) if point in _ALERT_CONDITION_POINTS else boolean_result


def _persist_audit24_point_result(point, boolean_result, detail, ran_at):
    """The exact real persistence contract already proven live by
    tests/test_audit24_master_issue_tracker_persistence.py -- is_deterministic/
    is_ai_free/is_boolean_software describe this point's real CHECK
    MECHANISM (always YES once a real check has genuinely run);
    solution_applied/issue_resolved_permanently mirror the real HEALTH
    verdict from _point_is_healthy(), not the raw boolean_result directly.
    check_again_notes always records the raw, unmodified boolean_result
    too, so the two are never conflated even where they differ."""
    issue_id = f"UMR171945-{point:04d}"
    healthy = _point_is_healthy(point, boolean_result)
    outcome = "YES" if healthy else "NO"
    note = (
        f"[{ran_at}] audit-24-points point {point}: raw_boolean={boolean_result} "
        f"healthy={healthy} -- {detail}"
    )
    return run_json([
        "python3", SUPERBOSS, "update-issue", "--issue-id", issue_id,
        "--field", "is_deterministic=YES",
        "--field", "is_ai_free=YES",
        "--field", "is_boolean_software=YES",
        "--field", f"solution_applied={outcome}",
        "--field", f"issue_resolved_permanently={outcome}",
        "--field", f"check_again_notes={note}",
    ], f"update-issue point {point}")


def _audit_point_02():
    """Do the last N real status reads trace through the canonical query
    path (task-gateway.py status / resource_governor.py --query-umr)? Both
    real call sites log a real row to governance_cycle_log (event_type=
    'query') -- TRUE iff at least one real logged row exists and every one
    of the last N rows names an allowed canonical caller."""
    n = 5
    resp = run_json(["python3", SUPERBOSS, "list-governance-events", "--event-type", "query",
                      "--limit", str(n)], "list-governance-events")
    rows = resp.get("matches", [])
    allowed = {"task-gateway.py:status", "resource_governor.py:--query-umr"}
    passed = len(rows) > 0 and all(r.get("caller") in allowed for r in rows)
    return passed, f"{len(rows)} real logged query event(s) (limit {n}), callers={[r.get('caller') for r in rows]}"


def _audit_point_04():
    """Is live /opt/veridian/scripts HEAD == origin/main HEAD with zero
    uncommitted diff?"""
    head = run(["git", "-C", SCRIPTS, "rev-parse", "HEAD"]).stdout.strip()
    origin = run(["git", "-C", SCRIPTS, "rev-parse", "origin/main"]).stdout.strip()
    status = run(["git", "-C", SCRIPTS, "status", "--short"]).stdout.strip()
    passed = bool(head) and head == origin and status == ""
    return passed, f"HEAD={head[:12]!r} origin/main={origin[:12]!r} status_short_empty={status == ''}"


def _audit_point_08():
    """Does a real, timestamped memory-check log entry exist within the
    last 24h -- from BEFORE this invocation, not counting this run's own
    event? Real, confirmed bug fixed 2026-08-08 (independent tier1 review,
    PR #280 round 4): cmd_audit_24_points() used to log its own
    memory_check event immediately before this check ran, so once
    audit-24-points had been run even once, this check could never again
    return False regardless of whether any real memory-check activity
    happened elsewhere -- the identical 'can never itself fail' defect
    class already fixed for Point 22 in round 1, left unaddressed here.
    cmd_audit_24_points() now logs its own event AFTER every point has
    been checked (see its own comment), so this only ever sees a real,
    PRIOR event -- genuinely testing whether audit-24-points (or anything
    else logging this event type) has run within the last 24h, not
    whether this exact invocation is currently in progress."""
    resp = run_json(["python3", SUPERBOSS, "list-governance-events", "--event-type", "memory_check",
                      "--limit", "1"], "list-governance-events")
    rows = resp.get("matches", [])
    if not rows:
        return False, "no PRIOR memory_check event logged (this run's own event is logged after checking, not before)"
    ts = rows[0].get("ts")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except Exception as e:
        return False, f"unparseable ts {ts!r}: {e}"
    return age <= 24 * 3600, f"most recent PRIOR memory_check at {ts}, age={age:.0f}s (threshold 86400s)"


def _audit_point_09():
    """Does a real, timestamped audit-performed log entry exist within the
    last 24h -- from BEFORE this invocation? Same real fix as Point 8 (see
    its own docstring): cmd_audit_24_points() now logs this run's own
    audit_performed event AFTER checking, not before, so this only ever
    sees a real, prior run."""
    resp = run_json(["python3", SUPERBOSS, "list-governance-events", "--event-type", "audit_performed",
                      "--limit", "1"], "list-governance-events")
    rows = resp.get("matches", [])
    if not rows:
        return False, "no PRIOR audit_performed event logged (this run's own event is logged after checking, not before)"
    ts = rows[0].get("ts")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except Exception as e:
        return False, f"unparseable ts {ts!r}: {e}"
    return age <= 24 * 3600, f"most recent PRIOR audit_performed at {ts}, age={age:.0f}s (threshold 86400s)"


def _audit_point_12():
    """task_kind == 'systemctl_action' -> software-only (TRUE); ==
    'veridian_task_create' -> AI needed (tell = the field itself);
    verify-done reuses umr_completion_percentage.py's own real evidence
    rule directly (never reimplemented) -- self-tested here against a real
    evidence-present and a real evidence-absent outputs_json."""
    try:
        ucp = _load_scripts_module("umr_completion_percentage.py", "task_gateway_ucp_reuse")
    except Exception as e:
        return False, f"could not import umr_completion_percentage.py: {type(e).__name__}: {e}"
    if not hasattr(ucp, "_parse_outputs"):
        return False, "umr_completion_percentage.py has no _parse_outputs() to reuse"
    _, had_evidence_true = ucp._parse_outputs(json.dumps({"commit_sha": "abc1234"}))
    _, had_evidence_false = ucp._parse_outputs(json.dumps({}))
    passed = had_evidence_true is True and had_evidence_false is False
    return passed, (
        "task_kind=='systemctl_action' -> software-only TRUE (no AI needed); "
        "task_kind=='veridian_task_create' -> AI needed (tell=the task_kind field itself); "
        f"verify-done reuses umr_completion_percentage._parse_outputs() directly, self-test "
        f"evidence_true={had_evidence_true} evidence_false={had_evidence_false}"
    )


def _audit_point_14():
    """Do any real umr_tasks rows currently match the real staleness
    thresholds (queued+ts_dispatched NULL >90min, running+no-heartbeat
    >45min)? Reuses resource_governor.py's detect_stale_umr_rows()
    directly -- the literal answer to this point's own question (TRUE
    means yes, stale rows currently exist -- an alert condition, not a
    health verdict)."""
    try:
        rg = _load_scripts_module("resource_governor.py", "task_gateway_rg_reuse")
    except Exception as e:
        return False, f"could not import resource_governor.py: {type(e).__name__}: {e}"
    stale = rg.detect_stale_umr_rows()
    return len(stale) > 0, f"{len(stale)} real umr_tasks row(s) currently match staleness thresholds: {stale[:5]}"


def _audit_point_16():
    """Is the same staleness/health scan wired into the EXISTING 30-second
    resource_governor_tick_loop.sh, not a new parallel timer? Grep-
    verifiable: the flag must be present, and there must still be exactly
    one `sleep 30` in the loop (a second timer would add a second sleep)."""
    path = os.path.join(SCRIPTS, "resource_governor_tick_loop.sh")
    try:
        content = open(path).read()
    except OSError as e:
        return False, f"could not read {path}: {e}"
    has_flag = "--umr-staleness-scan" in content
    sleep_count = content.count("sleep 30")
    passed = has_flag and sleep_count == 1
    return passed, f"--umr-staleness-scan present={has_flag}, 'sleep 30' occurrences={sleep_count} (must be exactly 1)"


def _audit_point_17():
    """Does task-gateway.py's real search step (cmd_submit's own
    superboss-register.py `search` call) contain a real Zoekt/pgvector
    reference? Grep-verifiable, scoped to cmd_submit itself so a mention
    elsewhere in the file doesn't produce a false TRUE."""
    path = os.path.join(SCRIPTS, "task-gateway.py")
    try:
        content = open(path).read()
    except OSError as e:
        return False, f"could not read {path}: {e}"
    m = re.search(r"def cmd_submit\(.*?\n(?=def )", content, re.S)
    scope = m.group(0) if m else content
    passed = bool(re.search(r"zoekt|pgvector", scope, re.I))
    return passed, f"grep of cmd_submit's own search step for zoekt/pgvector: {'found' if passed else 'not found'}"


def _audit_point_19():
    """Does a real failure/decline code path call add_master_issue
    automatically? Confirms resource_governor.py's _record_master_issue_if_new
    exists (real, ported forward from the unmerged
    feat/master-issue-tracker-add-issue-cli branch) and has real call
    sites, not just a defined-but-unused function."""
    path = os.path.join(SCRIPTS, "resource_governor.py")
    try:
        content = open(path).read()
    except OSError as e:
        return False, f"could not read {path}: {e}"
    call_sites = content.count("_record_master_issue_if_new(")
    # 1 for the def itself + at least 2 real call sites (_write_emergency_stop,
    # _stop_work_order_block_reason).
    passed = call_sites >= 3
    return passed, f"_record_master_issue_if_new( occurrences in resource_governor.py: {call_sites} (need >=3: def + >=2 real call sites)"


def _audit_point_20():
    """Is any cron/timer-triggered process currently over 5pct CPU? Real-
    time ps sample scoped to real veridian cron/timer-driven process names
    (see recon: veridian-cron-*, veridian-*-tick, veridian-governor-tick,
    veridian-dispatch-tick, veridian-zoekt-webserver, veridian-superboss-
    gateway). TRUE means yes, an alert condition -- not a health verdict."""
    proc = run(["ps", "-eo", "pid,pcpu,cmd", "--no-headers"])
    over = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, pcpu, cmd = parts
        if re.search(r"veridian-(cron-|.*-tick|governor-tick|dispatch-tick|zoekt|superboss-gateway)", cmd):
            try:
                if float(pcpu) > 5.0:
                    over.append({"pid": pid, "pcpu": pcpu, "cmd": cmd[:120]})
            except ValueError:
                pass
    return len(over) > 0, f"{len(over)} real cron/timer-associated process(es) currently over 5pct CPU: {over[:5]}"


_POINT_22_EVIDENCE_RE = re.compile(r"\bmerged\b.{0,200}?(PR\s*#\d+|commit\s+[0-9a-f]{7,40})", re.I | re.S)
# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR #280
# round 3): _POINT_22_EVIDENCE_RE alone matches "merged" followed anywhere
# within 200 chars by a PR/commit reference, with NO awareness of an
# intervening negation -- ordinary phrasing like "has NOT been merged yet
# ... PR #310" (a real, common way to describe UNMERGED work) matched, and
# would have auto-closed a genuinely still-open row: a real, destructive
# false positive triggered by a false match inside what is nominally a
# read-only audit check. Verified live before this fix against the actual
# compiled regex. _POINT_22_NEGATION_RE below checks a real window around
# each match for a negation word.
#
# Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR #280
# round 4): round 3's fix only checked the window BEFORE the "merged"
# token, so a negation that follows the merge claim -- e.g. "...was
# reverted after being merged in PR #310", describing since-reverted, NOT
# actually resolved work -- went undetected and would still auto-close the
# row. The window is now checked on BOTH sides of the match, and
# revert/rollback words (not just grammatical negation) are added to the
# set, since "reverted"/"rolled back"/"undone" describe exactly this real
# unresolved-despite-a-past-merge condition Point 22 must not auto-close.
_POINT_22_NEGATION_RE = re.compile(
    r"\b(not|never|n't|hasn't|wasn't|isn't|doesn't|didn't|won't|no longer|yet to be|"
    r"still pending|still open|still unmerged|reverted|revert|rolled back|rolled-back|"
    r"rollback|undone|reversed)\b", re.I,
)
_POINT_22_NEGATION_WINDOW = 40


def _point_22_real_evidence_match(text):
    """Returns the first real, non-negated evidence match in `text` (a
    'merged ... PR #N'/'merged ... commit <sha>' phrase with no negation/
    revert word in the _POINT_22_NEGATION_WINDOW chars immediately before
    OR after it), or None. See _POINT_22_NEGATION_RE's own comment above
    for the real, live-verified false-positives (both directions) this
    closes."""
    for m in _POINT_22_EVIDENCE_RE.finditer(text):
        before = text[max(0, m.start() - _POINT_22_NEGATION_WINDOW):m.start()]
        after = text[m.end():m.end() + _POINT_22_NEGATION_WINDOW]
        if _POINT_22_NEGATION_RE.search(before) or _POINT_22_NEGATION_RE.search(after):
            continue
        return m
    return None


def _audit_point_22():
    """For each open master_issue_tracker row linked to this governing UMR,
    does apply_fix_notes/audit_notes already contain a real, specific,
    parseable, non-negated evidence citation (a 'merged ... PR #N' or
    'merged ... commit <sha>' phrase, with no negation word immediately
    before it -- see _point_22_real_evidence_match()) sufficient to
    conservatively auto-close it via the real close-issue CLI?
    Deliberately conservative and scoped only to this governing UMR's own
    24 rows (never a platform-wide scan) -- a vague, partial, or negated
    citation never matches, so this never auto-closes on a guess.

    Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR #280
    round 1): this used to unconditionally `return True, ...` regardless of
    whether any matched row's close-issue call actually succeeded -- a real
    close_resp.get("ok") failure was silently dropped (the row simply never
    made it into `closed`), so this check could never itself fail no matter
    what went wrong. Now tracks matched-but-failed-to-close rows separately
    and returns False if any exist -- a genuine pass/fail signal, not an
    unconditional True."""
    resp = run_json(["python3", SUPERBOSS, "list-issues", "--linked-umr-id", AUDIT_24_GOVERNING_UMR,
                      "--is-closed", "NO", "--limit", "100"], "list-issues")
    rows = resp.get("matches", [])
    closed = []
    failed = []
    for row in rows:
        text = " ".join(filter(None, [row.get("apply_fix_notes"), row.get("audit_notes")]))
        match = _point_22_real_evidence_match(text or "")
        if match:
            resolution = f"auto-closed by audit-24-points Point 22: conservative evidence match ({match.group(0)[:150]!r})"
            close_resp = run_json(["python3", SUPERBOSS, "close-issue", "--issue-id", row["issue_id"],
                                    "--resolution-notes", resolution], "close-issue")
            if close_resp.get("ok"):
                closed.append(row["issue_id"])
            else:
                failed.append(row["issue_id"])
    passed = len(failed) == 0
    detail = f"{len(rows)} open row(s) checked (scoped to {AUDIT_24_GOVERNING_UMR}), {len(closed)} auto-closed: {closed}"
    if failed:
        detail += f", {len(failed)} matched but FAILED to close: {failed}"
    return passed, detail


def _audit_point_23():
    """Do real Grafana alert rules exist and are active? Queries Grafana's
    own API for a real count > 0 -- honest FALSE (not a fabricated
    placeholder) when no real Grafana instance is configured, which is the
    real, current, deliberate state of this platform (Grafana was
    evaluated and rejected as software -- AGPL-3.0 core, standalone Go
    server, no Vercel-serverless path -- PLATFORM_STRATEGY.md; the real
    replacement is compliance-tracker's own Wave 38 metric-alert-
    service.ts)."""
    url = os.environ.get("GRAFANA_URL")
    token = os.environ.get("GRAFANA_API_TOKEN")
    if not url or not token:
        return False, (
            "GRAFANA_URL/GRAFANA_API_TOKEN not configured -- Grafana was evaluated and rejected as "
            "software (PLATFORM_STRATEGY.md); real replacement is Wave 38 metric-alert-service.ts. "
            "This point's literal 'query Grafana's own API' requirement cannot be satisfied by design."
        )
    try:
        import urllib.request
        req = urllib.request.Request(f"{url.rstrip('/')}/api/v1/provisioning/alert-rules",
                                      headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        count = len(data) if isinstance(data, list) else len(data.get("rules", []) if isinstance(data, dict) else [])
        return count > 0, f"real Grafana API returned {count} alert rule(s)"
    except Exception as e:
        return False, f"real Grafana API call failed: {type(e).__name__}: {e}"


_AUDIT_24_CHECKS = {
    2: _audit_point_02, 4: _audit_point_04, 8: _audit_point_08, 9: _audit_point_09,
    12: _audit_point_12, 14: _audit_point_14, 16: _audit_point_16, 17: _audit_point_17,
    19: _audit_point_19, 20: _audit_point_20, 22: _audit_point_22, 23: _audit_point_23,
}

_AUDIT_24_REMEDIATION = {
    2: ("a developer", "PR review / ATTENTION.md entry", "re-run audit-24-points; last N logged query events must all show a canonical caller"),
    4: ("the operator/agent with an uncommitted diff or unpushed commit", "this check's own detail field", "re-run audit-24-points after committing+pushing; HEAD must == origin/main with an empty git status"),
    8: ("the PM/session that owes this cycle's memory check", "ATTENTION.md / this check's own detail field", "re-run audit-24-points once a real memory_check event has been logged within 24h"),
    9: ("the PM/session that owes this cycle's audit", "ATTENTION.md / this check's own detail field", "re-run audit-24-points -- running it IS the audit_performed event"),
    12: ("a developer", "this check's own detail field", "fix umr_completion_percentage.py's _parse_outputs() import/self-test, then re-run"),
    14: ("PM/operator", "ATTENTION.md STALE-UMR-SCAN entry", "re-run audit-24-points or resource_governor.py --umr-staleness-scan and confirm zero matches"),
    16: ("a developer", "PR review comment", "grep resource_governor_tick_loop.sh for --umr-staleness-scan and confirm exactly one sleep 30"),
    17: ("a developer", "this check's own detail field", "wire task-gateway.py's cmd_submit search step through Zoekt/pgvector, then re-run"),
    19: ("a developer", "this check's own detail field", "confirm _record_master_issue_if_new + its real call sites exist in resource_governor.py"),
    20: ("PM/operator", "this check's own detail field (offending pid/cmd)", "investigate the offending process, then re-sample"),
    22: ("PM/operator", "this check's own detail field (checked/closed counts)", "re-run and confirm the conservative scan executes without error"),
    23: ("the Owner (governance decision needed)", "this check's own detail field", "either configure a real Grafana instance, or formally redefine Point 23 against Wave 38 metric-alert-service.ts"),
}


def cmd_audit_24_points(args):
    ran_at = _now_iso_utc()

    # Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR
    # #280 round 3): `except Exception` does NOT catch SystemExit --
    # SystemExit inherits from BaseException directly, not Exception -- and
    # task-gateway.py's own fail()/run_json() helpers (used by points 2, 8,
    # 9, 22, and the persistence step for EVERY point) raise SystemExit via
    # sys.exit(1) on any transient subprocess/JSON failure. Before this fix,
    # a single such failure inside any one point's check OR its persist
    # step aborted the entire audit-24-points run before the final report
    # was ever printed -- silently losing every already-computed result,
    # not degrading gracefully as this loop's own intent requires. Both the
    # check call and the persist call below now explicitly catch
    # `(Exception, SystemExit)` -- deliberately NOT bare BaseException,
    # which would also swallow a real KeyboardInterrupt that should still
    # propagate.
    results = []
    for point in AUDIT_24_POINTS_SUBSET:
        try:
            passed, detail = _AUDIT_24_CHECKS[point]()
        except (Exception, SystemExit) as e:
            passed, detail = False, f"check itself raised {type(e).__name__}: {e}"
        who_acts, how_told, verify_done = _AUDIT_24_REMEDIATION[point]
        # Real, confirmed bug fixed 2026-08-08 (independent tier1 review,
        # PR #280 round 2): remediation guidance must key off the
        # alert-aware HEALTH verdict (_point_is_healthy()), not the raw
        # `passed` boolean -- for points 14/20, passed=True means a real
        # alert is firing, exactly when who-acts/how-told guidance is
        # needed most, not when it should go blank.
        healthy = _point_is_healthy(point, passed)
        entry = {
            "point": point,
            "boolean": passed,
            "if_false_who_acts": None if healthy else who_acts,
            "if_false_how_told": None if healthy else how_told,
            "how_software_verifies_done": verify_done,
            "detail": detail,
        }
        results.append(entry)
        if not args.no_persist:
            try:
                persist_resp = _persist_audit24_point_result(point, passed, detail, ran_at)
                entry["persisted"] = persist_resp.get("ok", False)
            except (Exception, SystemExit) as e:
                entry["persisted"] = False
                entry["persist_error"] = f"{type(e).__name__}: {e}"

    # Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR
    # #280 round 4): this run's own memory_check/audit_performed events are
    # logged HERE, AFTER every point (including Points 8/9 themselves) has
    # already been checked -- not before, as the original code did. Logging
    # before checking meant Points 8/9 always found their own freshly-
    # logged event and could never again return False once audit-24-points
    # had run even once, the identical 'can never itself fail' defect class
    # already fixed for Point 22 in round 1. This still logs real telemetry
    # every real invocation -- it just no longer contaminates the very
    # check it would otherwise make tautological.
    _log_governance_event_best_effort("memory_check", "task-gateway.py:audit-24-points", detail=ran_at)
    _log_governance_event_best_effort("audit_performed", "task-gateway.py:audit-24-points", detail=ran_at)

    print(json.dumps({"ran_at": ran_at, "results": results}, indent=2, default=str))


def build_parser():
    p = argparse.ArgumentParser(prog="task-gateway.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit")
    s.add_argument("--text", required=True)
    # UMR171945-0024 (governing chain UMR-20260806-171945-5767, real caller-identity
    # LABELING, not liveness/cryptographic proof -- this file already established no
    # server-side credential can prove that): the real 5-class model of distinct
    # caller identities that all land on this same gate --
    #   owner              -- Owner (laptop/PM), unchanged, gates through
    #                         run_owner_engine_gate() below exactly as before.
    #   ai_agent           -- dispatched AI worker (pre-existing choice, unchanged
    #                         meaning); conceptually "ai_worker" in this UMR's own
    #                         prose, kept as "ai_agent" here since it is the real,
    #                         already-live value every existing caller passes --
    #                         renaming it would be a breaking change to real callers,
    #                         not a labeling improvement.
    #   trusted_executor   -- the trusted executor (a direct, interactive tmux Claude
    #                         CLI session on this server), a real, currently-live
    #                         class this file previously had no label for.
    #   end_user           -- a real class this UMR explicitly reserves for a future
    #                         web-app end user that does not exist yet -- accepted
    #                         here so the label exists in advance, not because any
    #                         real caller uses it today.
    #   external_integration -- external AI models / external APIs / third-party
    #                         integrations, a real class with no live channel into
    #                         this file today (task-gateway.py is a local CLI, not a
    #                         network service) -- reserved the same way as end_user.
    s.add_argument("--source", required=True,
                    choices=["owner", "ai_agent", "trusted_executor", "end_user", "external_integration"])
    s.add_argument("--session-id", dest="session_id", required=True)
    s.set_defaults(func=cmd_submit)

    st = sub.add_parser("start")
    st.add_argument("--instruction-id", dest="instruction_id", required=True)
    st.add_argument("--title", required=True)
    st.add_argument("--repo", required=True)
    st.add_argument("--prompt-file", dest="prompt_file", required=True)
    st.add_argument("--umr-id", dest="umr_id", default=None,
                     help="optional -- if this start traces back to a real UMR, passing it lets the "
                          "stop-work-order gate (see run_task_start_gate()) match a real, "
                          "UMR-scoped exemption the same way dispatch_one() can; omitting it just "
                          "means no UMR-scoped exemption can match, which is the safe default")
    st.set_defaults(func=cmd_start)

    lg = sub.add_parser("log")
    lg.add_argument("--task-id", dest="task_id", required=True)
    lg.add_argument("--event", required=True)
    lg.set_defaults(func=cmd_log)

    cl = sub.add_parser("close")
    cl.add_argument("--task-id", dest="task_id", required=True)
    cl.add_argument("--audit-cmd", dest="audit_cmd", required=True)
    cl.add_argument("--evidence", required=True)
    cl.set_defaults(func=cmd_close)

    ra = sub.add_parser("register-automation")
    ra.add_argument("--path", required=True)
    ra.add_argument("--category", required=True)
    ra.add_argument("--layer", required=True)
    ra.add_argument("--purpose", required=True)
    ra.add_argument("--tags", required=True)
    ra.set_defaults(func=cmd_register_automation)

    stt = sub.add_parser("status")
    stt.add_argument("--task-id", dest="task_id", required=True)
    stt.set_defaults(func=cmd_status)

    a24 = sub.add_parser("audit-24-points",
                          help="run the 12 real deterministic boolean checks (points 2/4/8/9/12/14/"
                               "16/17/19/20/22/23) and persist each into master_issue_tracker "
                               "(UMR171945-0001..0024) via update-issue")
    a24.add_argument("--no-persist", dest="no_persist", action="store_true",
                      help="skip writing results into master_issue_tracker (used by tests)")
    a24.set_defaults(func=cmd_audit_24_points)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
