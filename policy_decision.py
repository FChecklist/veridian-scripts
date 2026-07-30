#!/usr/bin/env python3
"""
Shared decision schema for VERIDIAN's Policy/Rule/Decision Engines (Phase 2 of
ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml -- see
ai-os/POLICY_DECISION_SCHEMA_2026-07-24.yaml for the full design and the real,
read shapes of the 4 gates this generalizes: preflight-guard.py,
policy-enforcement-engine.ts, risk-tier.py, ai-decision-explanation.ts).

This module is imported by preflight-guard.py, risk-tier.py, and
decision-service.py -- one shared PolicyDecision shape instead of each gate
inventing its own ad hoc dict. Every field is additive to what those gates
already emit; no existing stdout/JSON contract changes.
"""
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "2026-07-24-v1"

# Verbatim from risk-tier.py -- shared here so both risk-tier.py's original
# stdout contract and decision-service.py's richer envelope classify from the
# exact same, single list of patterns (no drift between two copies).
TIER2_PATH_PATTERNS = [
    r"migrations?/",
    r"schema\.(sql|prisma)",
    r"\.sql$",
    r"(^|/)auth/",
    r"permission",
    r"payment",
    r"billing",
    r"\brls\b",
    r"security",
    r"\.env",
]


def classify_risk_tier(files: List[str], numstat_lines: List[str]) -> Dict[str, Any]:
    """Pure port of risk-tier.py's main() classification logic (unchanged heuristics): path-pattern
    match against TIER2_PATH_PATTERNS, plus a heavy-deletion heuristic. Returns
    {"tier": "tier1"|"tier2", "reasons": [str, ...]} -- risk-tier.py's CLI wraps this to keep its
    original stdout contract; decision-service.py wraps it to emit a full PolicyDecision envelope."""
    tier = "tier1"
    reasons: List[str] = []

    for f in files:
        for pat in TIER2_PATH_PATTERNS:
            if re.search(pat, f, re.IGNORECASE):
                tier = "tier2"
                reasons.append(f"path matched /{pat}/: {f}")

    total_add, total_del = 0, 0
    for line in numstat_lines:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            total_add += int(parts[0])
            total_del += int(parts[1])
    if total_del > 20 and total_del > total_add * 2:
        tier = "tier2"
        reasons.append(f"heavy deletion: -{total_del}/+{total_add}")

    return {"tier": tier, "reasons": reasons}


@dataclass
class PolicyDecision:
    source_gate: str
    decision: str  # "allow" | "deny" | "review"
    reason_code: str
    detail: str = ""
    risk_tier: Optional[str] = None  # "tier1" | "tier2" | None
    capability_match: Optional[Dict[str, Any]] = None
    explanation: Optional[Dict[str, Any]] = None
    evidence: List[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if self.decision not in ("allow", "deny", "review"):
            raise ValueError(f"decision must be allow/deny/review, got {self.decision!r}")
        if self.risk_tier not in (None, "tier1", "tier2"):
            raise ValueError(f"risk_tier must be tier1/tier2/None, got {self.risk_tier!r}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @property
    def proceed(self) -> bool:
        """preflight-guard.py-compatible view: only an outright 'allow' proceeds -- 'review' fails
        closed here, same as a 'deny', since preflight-guard.py's callers have no review/hold state."""
        return self.decision == "allow"

    @property
    def allowed(self) -> bool:
        """policy-enforcement-engine.ts-compatible view (PolicyDecision.allowed)."""
        return self.decision == "allow"


def emit_allow(source_gate: str, reason_code: str = "ok", detail: str = "", **kwargs) -> PolicyDecision:
    return PolicyDecision(source_gate=source_gate, decision="allow", reason_code=reason_code, detail=detail, **kwargs)


def emit_deny(source_gate: str, reason_code: str, detail: str = "", **kwargs) -> PolicyDecision:
    return PolicyDecision(source_gate=source_gate, decision="deny", reason_code=reason_code, detail=detail, **kwargs)


def emit_review(source_gate: str, reason_code: str, detail: str = "", **kwargs) -> PolicyDecision:
    return PolicyDecision(source_gate=source_gate, decision="review", reason_code=reason_code, detail=detail, **kwargs)


def make_explanation(summary: str, reasoning: str, recommended_action: Optional[str] = None,
                      confidence: Optional[str] = None, rejected_alternatives: Optional[List[Dict[str, str]]] = None,
                      assumptions: Optional[List[str]] = None, business_impact: Optional[str] = None) -> Dict[str, Any]:
    """Builds an AiDecisionExplanation-shaped dict (field-for-field match with
    repos/compliance-tracker/src/lib/explainability/ai-decision-explanation.ts's real type) -- the part of
    policy_decision_schema.explanation any decision (Python or TS side) can carry."""
    explanation: Dict[str, Any] = {"summary": summary, "reasoning": reasoning}
    if recommended_action is not None:
        explanation["recommendedAction"] = recommended_action
    if confidence is not None:
        if confidence not in ("low", "medium", "high"):
            raise ValueError(f"confidence must be low/medium/high, got {confidence!r}")
        explanation["confidence"] = confidence
    if rejected_alternatives is not None:
        explanation["rejectedAlternatives"] = rejected_alternatives
    if assumptions is not None:
        explanation["assumptions"] = assumptions
    if business_impact is not None:
        explanation["businessImpact"] = business_impact
    return explanation
