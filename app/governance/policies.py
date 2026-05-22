from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class GovernancePolicy:
    policy_id: str
    title: str
    description: str
    enabled: bool = True
    threshold: Optional[float] = None
    applies_to: Optional[List[str]] = None
    mode: str = "warn"  # warn|block|require_approval

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyDecision:
    policy_id: str
    passed: bool
    mode: str
    reason: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_POLICIES: List[GovernancePolicy] = [
    GovernancePolicy(
        policy_id="gov.confidence.gate.v1",
        title="Low-confidence gating",
        description="If pipeline confidence is below threshold, require human approval before exporting/locking outputs.",
        threshold=0.65,
        applies_to=["accountancy", "pensions", "core"],
        mode="require_approval",
    ),
    GovernancePolicy(
        policy_id="gov.high_severity.second_review.v1",
        title="Second reviewer for high severity",
        description="If CRITICAL findings exist, require second reviewer sign-off.",
        applies_to=["accountancy", "pensions"],
        mode="require_approval",
    ),
    GovernancePolicy(
        policy_id="gov.export.block_on_missing_evidence.v1",
        title="Block export if statutory evidence missing",
        description="If statutory requirement missing findings exist, block export until resolved or exemption documented.",
        applies_to=["accountancy"],
        mode="block",
    ),
]


def evaluate_policies(
    *,
    policies: Optional[List[GovernancePolicy]],
    ctx: Dict[str, Any],
    findings: List[Dict[str, Any]],
    vertical: str,
) -> List[Dict[str, Any]]:
    policies = policies or DEFAULT_POLICIES
    out: List[Dict[str, Any]] = []

    confidence = float(ctx.get("pipeline_confidence") or ctx.get("confidence") or 1.0)
    has_critical = any((f.get("severity") == "CRITICAL") for f in findings)
    has_stat_missing = any((f.get("code") == "STATUTORY_REQUIREMENT_MISSING") for f in findings)

    for p in policies:
        if not p.enabled:
            continue
        if p.applies_to and vertical not in p.applies_to:
            continue

        if p.policy_id == "gov.confidence.gate.v1":
            thr = float(p.threshold or 0.0)
            passed = confidence >= thr
            out.append(
                PolicyDecision(
                    policy_id=p.policy_id,
                    passed=passed,
                    mode=p.mode,
                    reason=f"confidence={confidence:.3f} threshold={thr:.3f}",
                    meta={"confidence": confidence, "threshold": thr},
                ).to_dict()
            )

        elif p.policy_id == "gov.high_severity.second_review.v1":
            passed = not has_critical
            out.append(
                PolicyDecision(
                    policy_id=p.policy_id,
                    passed=passed,
                    mode=p.mode,
                    reason="critical_findings_present" if has_critical else "no_critical_findings",
                    meta={"has_critical": has_critical},
                ).to_dict()
            )

        elif p.policy_id == "gov.export.block_on_missing_evidence.v1":
            passed = not has_stat_missing
            out.append(
                PolicyDecision(
                    policy_id=p.policy_id,
                    passed=passed,
                    mode=p.mode,
                    reason="statutory_missing_present" if has_stat_missing else "no_statutory_missing",
                    meta={"has_statutory_missing": has_stat_missing},
                ).to_dict()
            )

        else:
            out.append(
                PolicyDecision(
                    policy_id=p.policy_id,
                    passed=True,
                    mode=p.mode,
                    reason="unknown_policy_noop",
                    meta={},
                ).to_dict()
            )

    return out
