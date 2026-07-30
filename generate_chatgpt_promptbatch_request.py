#!/usr/bin/env python3
"""
generate_chatgpt_promptbatch_request.py -- manual-bridge request generator
for the VERIDIAN ChatGPT Prompt Library (INS-20260724-214122-f0ed,
task-20260724-214215-chatgpt-manual-bridge-workflow).

Owner decision this task implements: no OPENAI_API_KEY will be supplied (see
ai-os/CHATGPT_PROMPTLIB_BLOCKER_2026-07-24.yaml's prior awaiting_owner_decision
state). Instead of an API call, this script produces ONE real, complete,
ready-to-copy text file, the exact ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml
column contract, and the <Entity.Attribute> placeholder rule (reused from
ai-os/VARIABLE_DICTIONARY_2026-07-24.yaml, never reinvented) with an explicit
instruction to never use a hardcoded company/person name.

Two modes (task-20260725-180624-end-user-prompt-library-generation added the
second one):
  --mode capability_gap (default, original behavior): covers N real
    currently-uncovered capabilities from the live capability_registry (via
    scripts/generate_prompt_coverage_report.py's own loaders -- never a
    second, divergent reader of that data).
  --mode enduser_domain: covers a real (End User Role x Business Domain)
    matrix instead -- the Owner's own new requirement ("various end users...
    owners, head of departments... employees, vendors, customers") needs
    breadth capability_registry's 10 rows cannot give. Business Domain here
    is derived from ENGINE_MODULE_DOMAIN_MAP, a real, disk-verified mapping
    of compliance-tracker/src/lib/engines/*.ts filenames to domains (see
    ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml's anchor_decision_2026-07-25
    for why capability_registry and the previously-assumed "VCEL 247-engine
    registry" were both rejected as the primary anchor). Capability is filled
    from a real capability_registry row when one's `owner` field references
    the module (substring match, e.g. "src/lib/engines/hr-engine.ts"), else
    the documented "<module_slug>_unregistered" fallback -- never invented.

The Owner copies this into a free ChatGPT web session by hand, then saves the
reply for scripts/ingest_chatgpt_promptbatch_response.py to parse, validate,
and write into CSV/.

Nothing here calls any LLM API. The only write this script performs is the
request .txt file itself, under
/opt/veridian/chatgpt-prompt-library/_pending_requests/, still routed through
scripts/chatgpt_promptlib_guard.py's guarded_write.

Run:
  python3 scripts/generate_chatgpt_promptbatch_request.py --count 10
  python3 scripts/generate_chatgpt_promptbatch_request.py --count 3 --prompts-per-capability 8
  python3 scripts/generate_chatgpt_promptbatch_request.py --mode enduser_domain --combos 30 --prompts-per-combo 8
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from chatgpt_promptlib_guard import SANDBOX_ROOT, guarded_write  # noqa: E402
from generate_prompt_coverage_report import (  # noqa: E402
    load_real_capabilities, load_real_prompts, compute_coverage, DEFAULT_CSV_DIR,
)

import yaml  # noqa: E402

REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SCHEMA_PATH = os.path.join(REPO_ROOT, "ai-os", "CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml")
VARIABLE_DICTIONARY_PATH = os.path.join(REPO_ROOT, "ai-os", "VARIABLE_DICTIONARY_2026-07-24.yaml")
PENDING_REQUESTS_DIR = os.path.join(SANDBOX_ROOT, "_pending_requests")

DEFAULT_PROMPTS_PER_CAPABILITY = 5
DEFAULT_PLACEHOLDER_ENTITIES_PER_CAPABILITY = 3
DEFAULT_ATTRS_PER_ENTITY = 6

# ─── enduser_domain mode: real Module/Business Domain anchor ────────────────
# Real, disk-verified per task-20260725-180624 (see
# ai-os/CHATGPT_PROMPT_SCHEMA_2026-07-24.yaml's anchor_decision_2026-07-25).
# Every relpath below is checked to actually exist under ENGINE_REPO_ROOT at
# load time (load_real_engine_modules) -- a stale entry is dropped and
# reported, never silently kept.
ENGINE_REPO_ROOT = "/opt/veridian/repos/compliance-tracker/src/lib/engines"
ENGINE_MODULE_DOMAIN_MAP = {
    "accounting-engine.ts": "Finance & Accounting",
    "ai-support-engine.ts": "AI & Support Operations",
    "analytics-engine.ts": "Analytics & Reporting",
    "audit-engine.ts": "Audit & Assurance",
    "banking-engine.ts": "Banking & Treasury",
    "compliance-engine.ts": "Compliance & Regulatory",
    "costing-engine.ts": "Costing & Manufacturing",
    "crm-engine.ts": "Sales & CRM",
    "data-quality-engine.ts": "Data Quality & Governance",
    "document-processing-engine.ts": "Document Management",
    "fixed-asset-engine.ts": "Fixed Assets",
    "grc-workflow-engine.ts": "Governance, Risk & Compliance (GRC)",
    "hr-engine.ts": "HR & Payroll",
    "inventory-engine.ts": "Inventory & Warehouse",
    "logistics-engine.ts": "Logistics & Supply Chain",
    "marketing-engine.ts": "Marketing",
    "mathematical-engine.ts": "Mathematical & Computation Utilities",
    "payroll-engine.ts": "HR & Payroll",
    "procurement-engine.ts": "Procurement & Vendor Management",
    "project-management-engine.ts": "Project Management",
    "sales-engine.ts": "Sales & CRM",
    "security-engine.ts": "Security & Access Management",
    "in/gst-engine.ts": "India GST Compliance",
    "in/income-tax-engine.ts": "India Income Tax Compliance",
    "in/tds-engine.ts": "India TDS Compliance",
}
DEFAULT_INDUSTRIES = [
    "Generic / Cross-Industry",
    "Generic / Cross-Industry",
    "Generic / Cross-Industry",
    "Banking & NBFC",
    "Capital Markets / Listed Company",
    "Insurance",
]  # weighted: Generic dominant per this task's own CONSTRAINTS, regulated sectors real but rarer
DEFAULT_COMBOS_PER_BATCH = 30
DEFAULT_PROMPTS_PER_COMBO = 8


def load_schema_enum(schema, column_name):
    for col in schema["csv_schema"]["columns"]:
        if col["name"] == column_name:
            return col["values"]
    raise KeyError(f"schema has no column named {column_name!r}")


def load_real_engine_modules():
    """Returns [{module, module_slug, domain}] for every ENGINE_MODULE_DOMAIN_MAP
    entry that actually exists on disk right now -- a stale map entry is
    dropped (reported via the 'dropped' key), never silently trusted."""
    modules, dropped = [], []
    for relpath, domain in ENGINE_MODULE_DOMAIN_MAP.items():
        if os.path.isfile(os.path.join(ENGINE_REPO_ROOT, relpath)):
            filename = os.path.basename(relpath)
            modules.append({
                "module": filename,
                "module_slug": filename[:-3].replace("-", "_"),
                "relpath": relpath,
                "domain": domain,
            })
        else:
            dropped.append(relpath)
    return modules, dropped


def match_capability_for_module(module_entry, capabilities):
    """A capability_registry row is 'for' this module if its own `owner`
    field mentions the module's real relpath (substring match against real
    data, same discipline generate_chatgpt_promptbatch_request.py's sibling
    capability_gap mode already uses for entity matching) -- never invented.
    Falls back to the documented '<module_slug>_unregistered' value."""
    needle = f"src/lib/engines/{module_entry['relpath']}"
    matches = [c["capability_name"] for c in capabilities if needle in str(c.get("owner") or "")]
    if matches:
        return matches
    return [f"{module_entry['module_slug']}_unregistered"]


def compute_enduser_domain_coverage(prompts, roles, modules):
    """Real coverage over the (End User Role x Business Domain) matrix --
    parallel in spirit to generate_prompt_coverage_report.compute_coverage,
    but a different axis per this task's SCOPE (targeting shifts from
    capability-gap-only to role x domain). Existing rows written before this
    task's schema extension have no 'End User Role'/'Industry' columns and
    correctly count as uncovered for every combo -- that is the honest
    baseline, not a bug."""
    domains = sorted({m["domain"] for m in modules})
    counts = {(role, domain): 0 for role in roles for domain in domains}
    for row in prompts:
        role = (row.get("End User Role") or "").strip()
        domain = (row.get("Business Domain") or "").strip()
        if (role, domain) in counts:
            counts[(role, domain)] += 1
    missing = [combo for combo, n in counts.items() if n == 0]
    return {
        "total_combos": len(counts),
        "covered_combos": len(counts) - len(missing),
        "missing_combos": missing,
        "counts": counts,
    }


def _now_compact():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_variable_dictionary():
    with open(VARIABLE_DICTIONARY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def group_placeholders_by_entity(var_dict):
    by_entity = {}
    for entry in var_dict.get("entries", []):
        by_entity.setdefault(entry["entity"], []).append(entry["placeholder"])
    return by_entity


def pick_relevant_entities(capability, by_entity, max_entities):
    """Real, non-fabricated matching: an entity is 'relevant' to a capability
    if its lowercase name appears as a substring of the capability's own
    owner/workflow/capability_name text, or (fallback) it's one of the two
    highest-reference-count entities (Clients, Users) that appear in nearly
    every business prompt. No semantic guessing beyond substring matching."""
    haystack = " ".join(str(capability.get(k) or "") for k in ("owner", "workflow", "capability_name")).lower()
    matched = [entity for entity in by_entity if entity.lower() in haystack]
    for fallback in ("Users", "Clients"):
        if fallback in by_entity and fallback not in matched:
            matched.append(fallback)
    return matched[:max_entities]


def render_placeholder_appendix(capabilities, by_entity, max_entities, max_attrs):
    lines = []
    seen_entities = set()
    for cap in capabilities:
        entities = pick_relevant_entities(cap, by_entity, max_entities)
        lines.append(f"  {cap['capability_name']}: relevant entities -> {entities or '(none matched, use Users/Clients generically)'}")
        for entity in entities:
            if entity in seen_entities:
                continue
            seen_entities.add(entity)
            tokens = by_entity.get(entity, [])[:max_attrs]
            lines.append(f"    <{entity}.*> registered placeholders (sample): {', '.join(tokens)}")
    return "\n".join(lines)


def render_schema_columns(schema):
    lines = []
    for col in schema["csv_schema"]["columns"]:
        lines.append(f"  - {col['name']} ({col['type']}, required={col['required']}): {col['description']}")
    return "\n".join(lines)


def build_request_text(selected_capabilities, all_capabilities_by_name, prompts_per_capability,
                        schema, var_dict, requested_count, missing_count):
    by_entity = group_placeholders_by_entity(var_dict)
    columns_text = render_schema_columns(schema)
    placeholder_rule = schema["placeholder_variable_convention"]["rule"]
    placeholder_example = schema["placeholder_variable_convention"]["example"]
    placeholder_appendix = render_placeholder_appendix(
        selected_capabilities, by_entity, DEFAULT_PLACEHOLDER_ENTITIES_PER_CAPABILITY, DEFAULT_ATTRS_PER_ENTITY
    )

    shortfall_note = ""
    if requested_count > missing_count:
        shortfall_note = (
            f"\nNOTE: {requested_count} capabilities were requested but only {missing_count} real "
            f"capabilities are currently uncovered (0 real prompt rows) -- this batch covers all "
            f"{missing_count} of them, not {requested_count}. No capability is fabricated to make up "
            f"the count.\n"
        )

    capability_blocks = []
    for cap in selected_capabilities:
        capability_blocks.append(
            f"### Capability: {cap['capability_name']}\n"
            f"  owner: {cap.get('owner')}\n"
            f"  ai_required: {cap.get('ai_required')}\n"
            f"  confidence: {cap.get('confidence')}\n"
            f"  workflow: {cap.get('workflow')}\n"
            f"  inputs: {json.dumps(cap.get('inputs'), default=str)}\n"
            f"  business_rules: {json.dumps(cap.get('business_rules'), default=str)}\n"
        )

    parts = [
        "SYSTEM: You are generating real prompts for the VERIDIAN ChatGPT Prompt Library. Each prompt "
        "is a realistic, natural-language request a VERIDIAN user would type, that the named capability "
        "should be able to answer.",
        "",
        f"Output EXACTLY {prompts_per_capability} prompt rows per capability listed below, as CSV with "
        "this exact header row (15 columns, in this order):",
        ",".join(c["name"] for c in schema["csv_schema"]["columns"]),
        "",
        "Column contract:",
        columns_text,
        "",
        "PLACEHOLDER RULE (mandatory, reused from ai-os/VARIABLE_DICTIONARY_2026-07-24.yaml -- do not "
        "invent a different placeholder syntax):",
        f"  {placeholder_rule}",
        f"  Example: {placeholder_example}",
        "",
        "HARD REJECTION RULE: Any example data inside a Prompt (company name, person name, email, "
        "date, ID number) MUST use one of the registered <Entity.Attribute> placeholder tokens listed "
        "below -- NEVER a literal/hardcoded company name (e.g. 'Acme Corp', 'ABC Pvt Ltd'), person name "
        "(e.g. 'John Doe', 'Jane Smith'), or any other invented literal. A row using a hardcoded name "
        "instead of a placeholder will be automatically rejected at ingestion, not silently accepted.",
        "",
        "Registered placeholder tokens relevant to each capability below (sample -- the full dictionary "
        "has 894 entries across 60 entities in ai-os/VARIABLE_DICTIONARY_2026-07-24.yaml; if you need an "
        "entity/attribute not listed here, prefer Users/Clients/Tasks generic attributes over inventing one):",
        placeholder_appendix,
        shortfall_note,
        "CAPABILITIES FOR THIS BATCH (real, live capability_registry rows -- Capability column MUST "
        "exactly match one of these capability_name values):",
        "\n".join(capability_blocks),
        "",
        "Respond with ONLY the CSV -- header row first, then the prompt rows, no prose before or after, "
        "no markdown code fence.",
    ]
    return "\n".join(parts)


def select_diverse_combos(missing_combos, roles, modules_by_domain, n):
    """Round-robin across domains (cycling roles within each domain) so a
    batch is never all-one-role or all-one-domain -- SCOPE explicitly asks
    for 'a real, deliberately diverse spread of roles/domains/industries'."""
    domains = sorted(modules_by_domain.keys())
    by_domain = {}
    for role, domain in missing_combos:
        by_domain.setdefault(domain, []).append(role)
    for domain in by_domain:
        by_domain[domain].sort(key=roles.index)

    selected = []
    while len(selected) < n and any(by_domain.get(d) for d in domains):
        for domain in domains:
            if len(selected) >= n:
                break
            queue = by_domain.get(domain) or []
            if queue:
                role = queue.pop(0)
                selected.append((role, domain))
    return selected


def build_enduser_request_text(selected_combos, modules_by_domain, capabilities, prompts_per_combo,
                                schema, var_dict, roles, requested_combos, available_combos):
    by_entity = group_placeholders_by_entity(var_dict)
    columns_text = render_schema_columns(schema)
    placeholder_rule = schema["placeholder_variable_convention"]["rule"]
    placeholder_example = schema["placeholder_variable_convention"]["example"]

    shortfall_note = ""
    if requested_combos > available_combos:
        shortfall_note = (
            f"\nNOTE: {requested_combos} (role x domain) combos were requested but only "
            f"{available_combos} are currently uncovered (0 real prompt rows) -- this batch covers all "
            f"{available_combos} of them, not {requested_combos}. No combo is fabricated to make up the "
            f"count.\n"
        )

    combo_blocks = []
    seen_entities_appendix = set()
    placeholder_lines = []
    for role, domain in selected_combos:
        modules = modules_by_domain[domain]
        module_lines = []
        for m in modules:
            cap_options = match_capability_for_module(m, capabilities)
            module_lines.append(f"    - module {m['module']}: Capability MUST be one of {cap_options}")
            entities = pick_relevant_entities(
                {"owner": m["relpath"], "workflow": "", "capability_name": m["module_slug"]},
                by_entity, DEFAULT_PLACEHOLDER_ENTITIES_PER_CAPABILITY,
            )
            for entity in entities:
                if entity in seen_entities_appendix:
                    continue
                seen_entities_appendix.add(entity)
                tokens = by_entity.get(entity, [])[:DEFAULT_ATTRS_PER_ENTITY]
                placeholder_lines.append(f"  <{entity}.*> registered placeholders (sample): {', '.join(tokens)}")
        combo_blocks.append(
            f"### End User Role: {role} | Business Domain: {domain}\n"
            f"  real modules in this domain (pick whichever fits each prompt):\n" + "\n".join(module_lines) + "\n"
            f"  Industry: use `Generic / Cross-Industry` unless a prompt is genuinely industry-specific "
            f"(e.g. real regulated-sector values: Banking & NBFC, Capital Markets / Listed Company, "
            f"Insurance) -- never force industry-specificity that isn't real.\n"
        )

    parts = [
        "SYSTEM: You are generating real prompts for the VERIDIAN ChatGPT Prompt Library. Each prompt is "
        "a realistic, natural-language ERP request the named End User Role would actually type -- natural, "
        "slightly noisy human phrasing, NOT formal API-call syntax. Worked examples of the target style "
        "(verbatim from the Owner): \"complete all invoices for march\", \"give latest dso update {DSO = "
        "DAILY SALES OUTSTANDING}\", \"give status of material\", \"give vendor payment\", \"employee on "
        "leave\".",
        "",
        f"Output EXACTLY {prompts_per_combo} prompt rows per (End User Role x Business Domain) combo "
        "listed below, as CSV with this exact header row (columns, in this order):",
        ",".join(c["name"] for c in schema["csv_schema"]["columns"]),
        "",
        "Column contract:",
        columns_text,
        "",
        f"End User Role MUST be exactly one of: {roles} (do not invent a different role name).",
        "",
        "PLACEHOLDER RULE (mandatory, reused from ai-os/VARIABLE_DICTIONARY_2026-07-24.yaml -- do not "
        "invent a different placeholder syntax):",
        f"  {placeholder_rule}",
        f"  Example: {placeholder_example}",
        "",
        "HARD REJECTION RULE: Any example data inside a Prompt (company name, person name, email, date, "
        "ID number) MUST use one of the registered <Entity.Attribute> placeholder tokens listed below -- "
        "NEVER a literal/hardcoded company name (e.g. 'Acme Corp', 'ABC Pvt Ltd'), person name (e.g. "
        "'John Doe', 'Jane Smith'), or any other invented literal. A row using a hardcoded name instead of "
        "a placeholder will be automatically rejected at ingestion, not silently accepted.",
        "",
        "Registered placeholder tokens relevant to the modules below (sample -- the full dictionary has "
        "894 entries across 60 entities in ai-os/VARIABLE_DICTIONARY_2026-07-24.yaml; if you need an "
        "entity/attribute not listed here, prefer Users/Clients/Tasks generic attributes over inventing one):",
        "\n".join(placeholder_lines),
        shortfall_note,
        "(END USER ROLE x BUSINESS DOMAIN) COMBOS FOR THIS BATCH (deliberately diverse -- not all one "
        "role or one domain):",
        "\n".join(combo_blocks),
        "",
        "Respond with ONLY the CSV -- header row first, then the prompt rows, no prose before or after, "
        "no markdown code fence.",
    ]
    return "\n".join(parts)


def write_request_file(batch_id, selected_summary, request_text, extra_meta):
    ts = _now_compact()
    request_filename = f"{batch_id}-request-{ts}.txt"
    request_path = os.path.join(PENDING_REQUESTS_DIR, request_filename)

    header = (
        "=== VERIDIAN ChatGPT Prompt Library Batch Request (manual-bridge) ===\n"
        f"Batch: {batch_id}\n"
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n"
        "Generated by: scripts/generate_chatgpt_promptbatch_request.py "
        "(task-20260724-214215-chatgpt-manual-bridge-workflow"
        + (", extended task-20260725-180624-end-user-prompt-library-generation" if extra_meta else "") + ")\n"
        f"{selected_summary}\n"
        "\n"
        "INSTRUCTIONS FOR THE OWNER:\n"
        "  1. Copy everything below the '===== COPY BELOW THIS LINE =====' marker into a new,\n"
        "     free ChatGPT web session (no API key needed).\n"
        "  2. Save ChatGPT's full CSV response to a file.\n"
        "  3. Run: python3 scripts/ingest_chatgpt_promptbatch_response.py --file <response-file>\n"
        "\n"
        "===== COPY BELOW THIS LINE =====\n"
    )
    full_text = header + request_text
    guarded_write(request_path, full_text)
    return request_path


def run_capability_gap_mode(args):
    try:
        capabilities = load_real_capabilities()
    except (RuntimeError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": "capability_registry_unreachable", "detail": str(e)}, indent=2))
        sys.exit(1)

    prompts, _ = load_real_prompts(args.csv_dir)
    coverage = compute_coverage(capabilities, prompts)
    missing_names = coverage["missing_capabilities"]

    if not missing_names:
        print(json.dumps({
            "ok": False,
            "error": "no_uncovered_capabilities",
            "detail": "every real capability already has >=1 real prompt row -- nothing to batch. "
                      "Consider a targeted --count against a specific gap instead.",
        }, indent=2))
        sys.exit(1)

    selected_names = missing_names[:args.count]
    by_name = {c["capability_name"]: c for c in capabilities}
    selected_capabilities = [by_name[name] for name in selected_names]

    schema = load_schema()
    var_dict = load_variable_dictionary()

    request_text = build_request_text(
        selected_capabilities, by_name, args.prompts_per_capability, schema, var_dict,
        requested_count=args.count, missing_count=len(missing_names),
    )

    batch_id = f"BATCH-{_now_compact()}"
    request_path = write_request_file(
        batch_id, f"Capabilities in this batch: {selected_names}", request_text, extra_meta=False,
    )

    print(json.dumps({
        "ok": True,
        "mode": "capability_gap",
        "request_path": request_path,
        "batch_id": batch_id,
        "capabilities_selected": selected_names,
        "requested_count": args.count,
        "available_uncovered_count": len(missing_names),
        "prompts_per_capability": args.prompts_per_capability,
        "next_step": "python3 scripts/ingest_chatgpt_promptbatch_response.py --file <response-file>",
    }, indent=2))
    sys.exit(0)


def run_enduser_domain_mode(args):
    try:
        capabilities = load_real_capabilities()
    except (RuntimeError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": "capability_registry_unreachable", "detail": str(e)}, indent=2))
        sys.exit(1)

    modules, dropped_modules = load_real_engine_modules()
    if not modules:
        print(json.dumps({"ok": False, "error": "no_real_engine_modules_found"}, indent=2))
        sys.exit(1)

    modules_by_domain = {}
    for m in modules:
        modules_by_domain.setdefault(m["domain"], []).append(m)

    schema = load_schema()
    roles = load_schema_enum(schema, "End User Role")

    prompts, _ = load_real_prompts(args.csv_dir)
    coverage = compute_enduser_domain_coverage(prompts, roles, modules)
    missing_combos = coverage["missing_combos"]

    if not missing_combos:
        print(json.dumps({
            "ok": False,
            "error": "no_uncovered_combos",
            "detail": "every (End User Role x Business Domain) combo already has >=1 real prompt row.",
        }, indent=2))
        sys.exit(1)

    selected_combos = select_diverse_combos(missing_combos, roles, modules_by_domain, args.combos)
    var_dict = load_variable_dictionary()

    request_text = build_enduser_request_text(
        selected_combos, modules_by_domain, capabilities, args.prompts_per_combo,
        schema, var_dict, roles, requested_combos=args.combos, available_combos=len(missing_combos),
    )

    batch_id = f"BATCH-ENDUSER-{_now_compact()}"
    request_path = write_request_file(
        batch_id, f"(Role x Domain) combos in this batch: {selected_combos}", request_text, extra_meta=True,
    )

    expected_prompt_count = len(selected_combos) * args.prompts_per_combo
    print(json.dumps({
        "ok": True,
        "mode": "enduser_domain",
        "request_path": request_path,
        "request_bytes": os.path.getsize(request_path),
        "batch_id": batch_id,
        "combos_selected": selected_combos,
        "combos_requested": args.combos,
        "available_uncovered_combos": len(missing_combos),
        "total_possible_combos": coverage["total_combos"],
        "prompts_per_combo": args.prompts_per_combo,
        "expected_prompt_count_this_batch": expected_prompt_count,
        "real_modules_used": len(modules),
        "real_modules_dropped_stale": dropped_modules,
        "next_step": "python3 scripts/ingest_chatgpt_promptbatch_response.py --file <response-file>",
    }, indent=2))
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["capability_gap", "enduser_domain"], default="capability_gap")
    parser.add_argument("--count", type=int, default=10, help="[capability_gap] number of currently-uncovered capabilities to include")
    parser.add_argument("--prompts-per-capability", type=int, default=DEFAULT_PROMPTS_PER_CAPABILITY)
    parser.add_argument("--combos", type=int, default=DEFAULT_COMBOS_PER_BATCH, help="[enduser_domain] number of (role x domain) combos to include")
    parser.add_argument("--prompts-per-combo", type=int, default=DEFAULT_PROMPTS_PER_COMBO)
    parser.add_argument("--csv-dir", default=DEFAULT_CSV_DIR)
    args = parser.parse_args()

    if args.mode == "capability_gap":
        run_capability_gap_mode(args)
    else:
        run_enduser_domain_mode(args)


if __name__ == "__main__":
    main()
