#!/usr/bin/env python3
"""
Deterministic (non-AI) risk-tier classifier for a task's diff against its
base branch. tier1 = server-side Superboss may merge autonomously.
tier2 = Superboss may approve but must hold for human sign-off.
Classification must stay simple and auditable — do not let the AI reviewer
override it; this is the trust boundary, not a suggestion.

Phase 2 (ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml,
policy_rule_decision_unification): classification itself now lives in
policy_decision.classify_risk_tier() so decision-service.py's richer
PolicyDecision envelope classifies from the exact same logic, not a second
copy. This CLI's own stdout/stderr contract is UNCHANGED -- supervisor-
entrypoint.sh's `TIER=$(python3 risk-tier.py ...)` still captures a bare
tier1/tier2 string on stdout, reasons on stderr, same as before this refactor.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from policy_decision import classify_risk_tier  # noqa: E402


def main():
    workspace, base_ref = sys.argv[1], sys.argv[2]

    files = subprocess.run(
        ["git", "-C", workspace, "diff", "--name-only", f"{base_ref}...HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    numstat = subprocess.run(
        ["git", "-C", workspace, "diff", "--numstat", f"{base_ref}...HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    result = classify_risk_tier(files, numstat)

    print(result["tier"])
    if result["reasons"]:
        print("\n".join(result["reasons"]), file=sys.stderr)


if __name__ == "__main__":
    main()
