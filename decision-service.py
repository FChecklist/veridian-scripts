#!/usr/bin/env python3
"""
decision-service.py -- Phase 2 of VERIDIAN 20-ENGINE/10-GATEWAY architecture
(task-20260724-115041-phase2-give-policy-engine-preflight-guar),
closes_engines: [7] (Decision Engine), generalizing risk-tier.py +
ai-decision-explanation.ts into one callable service per that phase's own
objective (see ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml and
ai-os/POLICY_DECISION_SCHEMA_2026-07-24.yaml's decision_service_contract).

Every subcommand returns one policy_decision_schema-shaped JSON envelope
(see scripts/policy_decision.py) -- the same shape risk-tier.py and
preflight-guard.py now both emit, so a caller integrating any one of the 3
gets the other 2 for free.

Subcommands:
  risk-tier <workspace> <base_ref>
      Wraps risk-tier.py's real classify_risk_tier() (unchanged heuristics)
      and returns a full decision envelope with an auto-generated
      AiDecisionExplanation-shaped explanation, instead of a bare tier
      string. Does not replace risk-tier.py's own CLI contract -- this is
      the richer, additive sibling call for callers that want the full
      schema.
  capability-check <intent_text>
      Composes scripts/superboss-register.py's lookup-capability (Phase 1,
      live) with policy_decision_schema's capability_match field --
      implements the dependency_table edge "Capability Registry Engine (3)
      -> Decision Engine (7)" as real running code for the first time.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from policy_decision import (  # noqa: E402
    classify_risk_tier, emit_allow, emit_deny, emit_review, make_explanation,
)

SUPERBOSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "superboss-register.py")


def cmd_risk_tier(workspace, base_ref):
    files = subprocess.run(
        ["git", "-C", workspace, "diff", "--name-only", f"{base_ref}...HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    numstat = subprocess.run(
        ["git", "-C", workspace, "diff", "--numstat", f"{base_ref}...HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    result = classify_risk_tier(files, numstat)
    tier = result["tier"]
    reasons = result["reasons"]

    explanation = make_explanation(
        summary=f"Classified {tier} ({'autonomous merge' if tier == 'tier1' else 'holds for human sign-off'}).",
        reasoning=(
            "No path matched a tier2 pattern (migrations/auth/permission/payment/billing/rls/security/.env) "
            "and no heavy-deletion heuristic tripped." if not reasons
            else "Matched: " + "; ".join(reasons)
        ),
        assumptions=["Classification is path-pattern + diff-size based only -- does not read file content."],
    )

    if tier == "tier2":
        decision = emit_review(
            source_gate="decision-service.py:risk-tier", reason_code="tier2_requires_signoff",
            detail=f"{len(files)} files changed, base_ref={base_ref}",
            risk_tier=tier, explanation=explanation, evidence=reasons,
        )
    else:
        decision = emit_allow(
            source_gate="decision-service.py:risk-tier", reason_code="tier1_autonomous",
            detail=f"{len(files)} files changed, base_ref={base_ref}",
            risk_tier=tier, explanation=explanation, evidence=reasons,
        )
    print(decision.to_json())
    return 0


def cmd_capability_check(intent_text):
    proc = subprocess.run(
        ["python3", SUPERBOSS, "lookup-capability", "--intent-text", intent_text],
        capture_output=True, text=True,
    )
    try:
        lookup = json.loads(proc.stdout)
    except json.JSONDecodeError:
        decision = emit_review(
            source_gate="decision-service.py:capability-check", reason_code="lookup_unparseable",
            detail=f"superboss-register.py lookup-capability did not return parseable JSON: {proc.stdout[:300]}",
        )
        print(decision.to_json())
        return 1

    found = lookup.get("found", False)
    matches = lookup.get("matches", [])
    best = matches[0] if matches else None

    if found and best and not best.get("ai_required", True):
        registered = bool(best.get("business_rules"))
        capability_match = {
            "capability_name": best.get("capability_name"),
            "ai_required": False,
            "business_rules_registered": registered,
        }
        explanation = make_explanation(
            summary=f"Deterministic capability match: {best.get('capability_name')}.",
            reasoning=(
                f"lookup-capability resolved via {lookup.get('resolution_stage_used', 'unknown stage')} -- "
                f"ai_required=false and a real apis[] entry means the deterministic path must be used "
                f"instead of an AI call, per CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's lookup_contract."
            ),
            recommended_action=f"Call {best.get('capability_name')}'s existing deterministic implementation directly.",
        )
        decision = emit_deny(
            source_gate="decision-service.py:capability-check", reason_code="deterministic_capability_available",
            detail="An AI call is unnecessary -- a real, deterministic capability already exists.",
            capability_match=capability_match, explanation=explanation,
        )
    else:
        explanation = make_explanation(
            summary="No deterministic capability match found." if not found else "Match found but requires AI.",
            reasoning="lookupCapability() returned found=false or the best match is ai_required=true -- an AI call may proceed." if not (found and best and best.get("ai_required")) else "Best match requires AI (ai_required=true).",
        )
        decision = emit_allow(
            source_gate="decision-service.py:capability-check", reason_code="ai_call_permitted",
            detail="No deterministic alternative found -- caller may proceed to an AI call.",
            explanation=explanation,
        )
    print(decision.to_json())
    return 0


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: decision-service.py <risk-tier|capability-check> [args...]"}))
        sys.exit(1)

    subcommand = sys.argv[1]
    if subcommand == "risk-tier":
        if len(sys.argv) != 4:
            print(json.dumps({"error": "usage: decision-service.py risk-tier <workspace> <base_ref>"}))
            sys.exit(1)
        sys.exit(cmd_risk_tier(sys.argv[2], sys.argv[3]))
    elif subcommand == "capability-check":
        if len(sys.argv) != 3:
            print(json.dumps({"error": "usage: decision-service.py capability-check <intent_text>"}))
            sys.exit(1)
        sys.exit(cmd_capability_check(sys.argv[2]))
    else:
        print(json.dumps({"error": f"unknown subcommand: {subcommand}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
