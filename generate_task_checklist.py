#!/usr/bin/env python3
"""
generate_task_checklist.py -- Owner directive 2026-07-20: "make a prompt
for your self use... which have the list of files, process and
instructions that needs to be updated by you or superboss for every task.
Make it via script so that software does it."

Software-generated, not hand-typed: the FILE PATHS below are pulled live
from MASTER_INDEX.yaml's own registries (so if a path ever changes,
regenerating this checklist auto-updates it instead of silently going
stale) -- only the PROCEDURAL STEPS (the order of operations, and which
of the 77 checklist items has a real backing mechanism today) are fixed
text, because a workflow sequence isn't the kind of thing that's
"in" the data to extract; it's this session's own accumulated, real
operating discipline, written down once here.

2026-07-23 (phase 2 of the "EXECUTION_RULES_AUDIT_2026-07-23" analysis's
roadmap_next_phases.pre_execution_checklist_automation -- that analysis was
never persisted as a real ai-os/EXECUTION_RULES_AUDIT_2026-07-23.yaml file on
disk; corrected 2026-07-24, task-20260724-033446, Knowledge Engine Phase 2
candidate fill_the_3_real_drift_gaps, after this citation sat as a real,
undetected PATH_MISSING drift in the knowledge_engine table since Phase 1):
restructured
from a flat 3-list ~11-item checklist into the full A-J, 77-item
structure of VERIDIAN_EXECUTION_RULES_2026-07-23.md Part 39. Every one
of the 77 source items is represented explicitly with one of three
honest statuses -- never silently dropped:
  REAL                    a real, currently-running mechanism backs this
                           item today (script/table/file cited below).
  NOT_YET_AVAILABLE        no real mechanism exists yet; a true gap.
  NOT_MECHANICALLY_TESTABLE  inherently a judgment/discipline item (same
                           honest category veridian_self_check.py already
                           uses for its own rule_5) -- no script can
                           verify it retroactively, so it is not claimed
                           as automated.
This mirrors this same audit's own methodology: a plausible-sounding
claim is not the same as a verified one (registries.ops_task_sync_bridge
.correction_2026_07_20).

Run: python3 generate_task_checklist.py MASTER_INDEX.yaml
Prints the self_use_task_checklist YAML block to stdout -- paste it back
into MASTER_INDEX.yaml (same review-before-splice discipline as
generate_quick_reference.py).
"""
import os
import sys
import yaml

GENERATED_DIR = "/opt/veridian/ai-os/generated"


def get_path(registries, reg_id, field="path"):
    for r in registries:
        if r.get("id") == reg_id:
            val = r.get(field)
            if isinstance(val, str):
                return val
    return f"UNKNOWN (registries.{reg_id}.{field} not found -- regenerate this checklist)"


def build_sections(registries):
    """Returns the A-J section list, all 77 Part-39 items, each with a
    status and real evidence (or an honest reason it has none yet)."""
    sysidx = get_path(registries, "system_index")
    sb = get_path(registries, "session_bootstrap_script")
    pf = get_path(registries, "preflight_guard_py")
    pag = get_path(registries, "postflight_audit_gate_script")
    csg = "registries.credit_spend_governance"
    ssm = get_path(registries, "system_sync_mechanism")
    fog = get_path(registries, "file_edit_guard_script")
    register_db = "ai-os/memory/superboss-register.sqlite"

    def item(n, text, status, evidence):
        return {"n": n, "text": text, "status": status, "evidence": evidence}

    sections = [
        {
            "letter": "A",
            "title": "Recover Context",
            "items": [
                item(1, "Load the complete task context.", "REAL",
                     f"{sb} prints the last N instructions/work_items/actions from {register_db}"),
                item(2, "Load the Owner, Organization, and End User memory.", "NOT_YET_AVAILABLE",
                     "no such memory store exists yet -- Parts 33-35 of the (never-persisted-to-disk) "
                     "\"EXECUTION_RULES_AUDIT_2026-07-23\" analysis named this MISSING, explicitly out of "
                     "scope for that phase (roadmap deferred it to a future task; corrected 2026-07-24, "
                     "task-20260724-033446, to stop citing a file that was never actually written)"),
                item(3, "Load previous conversations.", "REAL",
                     f"{sb} prints instructions.raw_text rows (closest real analog to 'conversations' in this system)"),
                item(4, "Load previous commitments.", "REAL",
                     f"{sb} queries rca_open WHERE rca_status='OPEN_NEEDS_RCA' from {register_db}"),
                item(5, "Load unfinished tasks.", "REAL",
                     f"{sb} queries work_items + unregistered_mentions WHERE status='NEEDS_REGISTRATION'"),
                item(6, "Load pending approvals.", "NOT_YET_AVAILABLE",
                     "no dedicated pending-approvals store exists distinct from work_items.status / gap_queue.yaml status"),
                item(7, "Load previous decisions.", "REAL",
                     f"{sb} prints work_items/actions history rows (each carries a status/result already decided)"),
                item(8, "Load previous execution plans.", "NOT_YET_AVAILABLE",
                     "no persisted execution-plan artifact store exists; AI planning-mode output is not written back to the register DB"),
            ],
        },
        {
            "letter": "B",
            "title": "Search System Knowledge",
            "items": [
                item(9, "Search metadata.", "REAL", f"superboss-register.py search / check-duplicate against {sysidx}"),
                item(10, "Search YAML files.", "REAL", f"{sb} loads MASTER_INDEX.yaml/SYSTEM_MAP.yaml/STANDING_DIRECTIVE.yaml/MASTER_ARCHITECTURE_2026-07-22.yaml"),
                item(11, "Search entity relationships.", "REAL", "system_index.calls / called_by columns (superboss-register.py search)"),
                item(12, "Search dependency relationships.", "REAL", "system_index.calls / called_by columns (same mechanism as item 11)"),
                item(13, "Search business rules.", "REAL", f"{sb} prints STANDING_DIRECTIVE.yaml assistant_working_protocol rule_* keys; ai-os/RULES_ARTICLES_198.json"),
                item(14, "Search workflows.", "REAL", "ai-os/queues/*.yaml structured task records (grep / check-duplicate)"),
                item(15, "Search documentation.", "REAL", f"{sb} prints MASTER_ARCHITECTURE_2026-07-22.yaml open_questions_for_owner"),
                item(16, "Search scripts.", "REAL", f"system_index (category/path fields) via check-duplicate -- see {sysidx}"),
                item(17, "Search automation.", "REAL", "system_index rows with category in (dispatch_entrypoint, monitor, audit) + crontab -l"),
                item(18, "Search Software modules.", "REAL", "MASTER_INDEX.yaml registries: section + system_index"),
                item(19, "Search APIs.", "NOT_YET_AVAILABLE", "no ai-os-layer API registry exists; app-layer API routes are compliance-tracker/projexa product code, out of this task's scope"),
                item(20, "Search configuration files.", "REAL", "STANDING_DIRECTIVE.yaml / AI_ENGINEERING_POLICY.yaml loaded and printed"),
                item(21, "Search cache.", "REAL", "work_items.cache_id / ai_cache_id reference the existing glm-response-cache.sqlite L1 cache (superboss-register.py search)"),
                item(22, "Search logs.", "REAL", f"{sb} reads ATTENTION.md-adjacent state + runs file_inventory.py for a real diff"),
                item(23, "Search audit records.", "REAL", f"{sb} queries task_audits (new this phase -- see {pag})"),
                item(24, "Search history.", "REAL", f"{sb} queries directive_compliance_runs + work_items/actions history"),
            ],
        },
        {
            "letter": "C",
            "title": "Reuse Before Create",
            "items": [
                item(25, "Check if Software already exists.", "REAL", "superboss-register.py check-duplicate <term>"),
                item(26, "Check if a script already exists.", "REAL", "superboss-register.py check-duplicate --category <script category>"),
                item(27, "Check if automation already exists.", "REAL", f"check-duplicate + {ssm}'s own do_not_build_a_new_sync_script precedent"),
                item(28, "Check if workflow already exists.", "REAL", "check-duplicate + grep across ai-os/queues/*.yaml"),
                item(29, "Check if business logic already exists.", "NOT_YET_AVAILABLE", "system_index has no distinct 'business_logic' category yet -- only a same-category or keyword proxy check is possible today"),
                item(30, "Check if API already exists.", "NOT_YET_AVAILABLE", "same gap as item 19 -- no ai-os-layer API registry"),
                item(31, "Check if the solution already exists.", "REAL", "check-duplicate (general free-text form)"),
                item(32, "Reuse existing assets whenever possible.", "REAL", "check-duplicate's own verdict field ('STOP -- existing mechanism(s) found, review before building') is the real, machine-produced trigger for this"),
            ],
        },
        {
            "letter": "D",
            "title": "Understand the Task",
            "items": [
                item(33, "Understand the business objective.", "REAL", "every ai-os task's prompt.txt has a structured OBJECTIVE field (see this task's own prompt.txt)"),
                item(34, "Understand the expected outcome.", "REAL", "prompt.txt's SUCCESS_CRITERIA + EXPECTED_OUTPUT fields"),
                item(35, "Identify required inputs.", "REAL", "prompt.txt's KNOWN_CONTEXT field"),
                item(36, "Identify required outputs.", "REAL", "prompt.txt's EXPECTED_OUTPUT field"),
                item(37, "Identify dependencies.", "REAL", "prompt.txt's KNOWN_CONTEXT field (e.g. this phase's own prior-phase commit references)"),
                item(38, "Identify risks.", "NOT_YET_AVAILABLE", "the task prompt schema has no dedicated RISKS field"),
                item(39, "Identify constraints.", "REAL", "prompt.txt's CONSTRAINTS field"),
                item(40, "Identify edge cases.", "NOT_YET_AVAILABLE", "the task prompt schema has no dedicated EDGE_CASES field"),
            ],
        },
        {
            "letter": "E",
            "title": "Break Down the Task",
            "items": [
                item(41, "Break the task into small steps.", "NOT_YET_AVAILABLE", "no general mid-task step-decomposition mechanism -- master-decompose.py exists but is scoped to the Owner-directive-to-task dispatch pipeline only (Part 38a), not a reusable per-task tool"),
                item(42, "Classify each step as: Software / Script / Automation / Workflow / AI / Human Approval.", "NOT_YET_AVAILABLE", "no coded per-step taxonomy exists; PROGRESS.md is free text, not a schema"),
                item(43, "Minimise AI work.", "REAL", f"{csg} gates every AI-costing action ('software first, AI second' rule enforced before spend)"),
                item(44, "Maximise Software execution.", "REAL", f"same mechanism as item 43 ({csg})"),
            ],
        },
        {
            "letter": "F",
            "title": "Create Execution Plan",
            "items": [
                item(45, "Prepare the execution plan.", "REAL", "PROGRESS.md (## Completed / ## Remaining) is this session's real, git-committed execution-plan record, per phase0/phase1 precedent"),
                item(46, "Validate the execution plan.", "NOT_YET_AVAILABLE", "no automated plan-validation script exists"),
                item(47, "Verify required resources.", "NOT_YET_AVAILABLE", "no automated resource-verification mechanism exists"),
                item(48, "Verify permissions.", "REAL", f"{pf} hard-gates on disk/mem/circuit-breaker before invoking the model"),
                item(49, "Verify dependencies.", "REAL", "system_index.calls/called_by (item 11/12) + scope-check.py's file-ownership.yaml cross-check"),
                item(50, "Verify configurations.", "NOT_YET_AVAILABLE", "STANDING_DIRECTIVE.yaml is searched/read (item 20) but not verified against a schema"),
            ],
        },
        {
            "letter": "G",
            "title": "AI Decision",
            "items": [
                item(51, "Can Software complete the task? If YES -> Execute through Software. If NO -> Build reusable Software first -> Execute through Software -> Call AI only if required.",
                     "REAL", f"{csg}'s own 'software first, AI second' rule is exactly this gate"),
            ],
        },
        {
            "letter": "H",
            "title": "Verification Before Execution",
            "items": [
                item(52, "Verify no previous commitment is forgotten.", "REAL", "rca_open check (same mechanism as item 4)"),
                item(53, "Verify no existing work is duplicated.", "REAL", "check-duplicate (mandatory, same mechanism as item 25)"),
                item(54, "Verify no business rule is violated.", "NOT_YET_AVAILABLE", "ai-os/audit198/run-audit.mjs checks RULES_ARTICLES_198.json post-hoc, not as a pre-execution gate"),
                item(55, "Verify all required context is loaded.", "REAL", f"completion of {sb} (sections A+B above)"),
                item(56, "Verify execution is safe.", "REAL", f"{pf} hard gate (disk/mem/circuit-breaker) before model invocation"),
                item(57, "Verify approval is available if required.", "NOT_YET_AVAILABLE", "same gap as item 6 -- no approval-queue mechanism"),
            ],
        },
        {
            "letter": "I",
            "title": "Execute",
            "items": [
                item(58, "Execute through Software.", "REAL", f"{csg} + this session's own scripts"),
                item(59, "Execute AI only where required.", "REAL", f"same mechanism as item 58 ({csg})"),
                item(60, "Validate outputs.", "REAL", f"{pag}'s audit_cmd (real command, real exit code, never trusted-by-claim)"),
                item(61, "Handle failures.", "REAL", f"{pag} writes a FAILED verdict + a real rca_open row for later investigation (logs and flags; does not auto-remediate)"),
                item(62, "Retry when appropriate.", "REAL", "worker-entrypoint.sh's own checkpoint/resume mechanism (registries.auto_execution_wrapper)"),
            ],
        },
        {
            "letter": "J",
            "title": "Post Execution",
            "items": [
                item(63, "Update Software.", "REAL", f"{ssm} --check mirror + system_index index-add"),
                item(64, "Update scripts.", "REAL", "superboss-register.py index-add (re-verifies path, refreshes verified_ts)"),
                item(65, "Update automation.", "NOT_YET_AVAILABLE", "no automated crontab/systemd diff-checker exists; changes are verified manually (crontab -l / systemctl)"),
                item(66, "Update workflows.", "NOT_YET_AVAILABLE", "ai-os/queues/*.yaml is hand/script-edited case-by-case, no generic workflow-update mechanism"),
                item(67, "Update metadata.", "REAL", "superboss-register.py index-add"),
                item(68, "Update YAML.", "REAL", "MASTER_INDEX.yaml registries: section edit + generate_quick_reference.py re-splice"),
                item(69, "Update relationships.", "REAL", "system_index.calls/called_by fields (same mechanism as items 11/12)"),
                item(70, "Update documentation.", "REAL", "commit message (WHY not just WHAT) + PROGRESS.md convention"),
                item(71, "Update logs.", "REAL", "superboss-register.py log-work / log-action"),
                item(72, "Update audit.", "REAL", f"{pag}'s task_audits row + this phase's new execution_log row"),
                item(73, "Update history.", "REAL", "work_items/actions rows (superboss-register.py)"),
                item(74, "Update memory.", "REAL", f"{register_db} writes (all of the above)"),
                item(75, "Save reusable knowledge.", "REAL", "system_index registration + MASTER_INDEX.yaml registries entry"),
                item(76, "Convert repeatable AI work into Software.", "REAL", "this exact phase is one instance: generate_task_checklist.py/superboss-register.py extended so future tasks reuse a script instead of re-deriving this checklist by prose"),
                item(77, "Reduce future AI dependency.", "REAL", "same evidence as item 76"),
            ],
        },
    ]
    return sections


def coverage_summary(sections):
    total = sum(len(s["items"]) for s in sections)
    real = sum(1 for s in sections for i in s["items"] if i["status"] == "REAL")
    nya = sum(1 for s in sections for i in s["items"] if i["status"] == "NOT_YET_AVAILABLE")
    nmt = sum(1 for s in sections for i in s["items"] if i["status"] == "NOT_MECHANICALLY_TESTABLE")
    return {
        "sections_covered": len(sections),
        "sections_total_in_part_39": 10,
        "items_total": total,
        "items_real": real,
        "items_not_yet_available": nya,
        "items_not_mechanically_testable": nmt,
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "MASTER_INDEX.yaml"
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    registries = doc.get("registries", [])
    sections = build_sections(registries)

    checklist = {
        "self_use_task_checklist": {
            "purpose": (
                "The one prompt this session (or superboss) reads before "
                "and after every task, regardless of size. Generated by "
                "ai-os/scripts/generate_task_checklist.py -- re-run after "
                "any registries: edit that changes one of the paths cited "
                "in the evidence fields below, do not hand-edit this block."
            ),
            "structure_note": (
                "Restructured 2026-07-23 (per the \"EXECUTION_RULES_AUDIT_2026-07-23\" "
                "analysis's roadmap_next_phases.pre_execution_checklist_automation -- that "
                "analysis was never persisted as a real file on disk, corrected 2026-07-24 "
                "task-20260724-033446) to mirror "
                "VERIDIAN_EXECUTION_RULES_2026-07-23.md Part 39's A-J, 77-item structure "
                "one-for-one. Every item carries a status: REAL (a real mechanism backs it "
                "today, cited in evidence), NOT_YET_AVAILABLE (a real, honestly-documented "
                "gap), or NOT_MECHANICALLY_TESTABLE (an inherent judgment/discipline item, "
                "same honest category ai-os/scripts/veridian_self_check.py already uses for "
                "its own rule_5). Nothing is silently dropped."
            ),
            "sections": sections,
            "coverage_summary": coverage_summary(sections),
            "credit_spend_reminder": (
                "Every AI-costing action referenced above (items 43/44/51/58/59) is gated by "
                "registries.credit_spend_governance -- see that entry for the full mechanism. "
                "This checklist does not duplicate that gate's own rules, only points to it."
            ),
        }
    }

    out_text = yaml.safe_dump(checklist, default_flow_style=False, sort_keys=False, allow_unicode=True, width=110)
    print(out_text, end="")

    # Governance item 36 (documentation_auto_generation, 2026-07-23): also persist
    # to a real file, in addition to the stdout print above (kept as-is -- other
    # callers may still paste it in manually).
    os.makedirs(GENERATED_DIR, exist_ok=True)
    with open(os.path.join(GENERATED_DIR, "generate_task_checklist-latest.yaml"), "w", encoding="utf-8") as f:
        f.write(out_text)


if __name__ == "__main__":
    main()
