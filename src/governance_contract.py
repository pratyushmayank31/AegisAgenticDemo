"""
Governance contract for converting raw Aegis review decisions into safe LdcDemo outcomes.

Provides deterministic aggregation of multiple review decisions with fail-closed semantics:
- Unknown decisions → HOLD (human review), never DENY
- Unanimous explicit approval required for ALLOW
- Any explicit rejection → DENY
- Partial/pending requests → HOLD
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, ConfigDict


class GovernanceAction(str, Enum):
    """Governance action outcome."""

    ALLOW = "ALLOW"
    HOLD = "HOLD"
    DENY = "DENY"


class GovernanceReason(str, Enum):
    """Reason code for governance decision."""

    ALL_REQUESTS_APPROVED = "ALL_REQUESTS_APPROVED"
    CONDITIONAL_APPROVAL = "CONDITIONAL_APPROVAL"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    EXPLICIT_REJECTION = "EXPLICIT_REJECTION"
    DECISION_PENDING = "DECISION_PENDING"
    REQUEST_PENDING = "REQUEST_PENDING"
    NO_REQUESTS = "NO_REQUESTS"
    UNKNOWN_DECISION_TYPE = "UNKNOWN_DECISION_TYPE"
    GOVERNANCE_TIMEOUT = "GOVERNANCE_TIMEOUT"
    AEGIS_UNAVAILABLE = "AEGIS_UNAVAILABLE"


class RequestGovernanceResult(BaseModel):
    """Single request decision outcome."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1)
    raw_decision_type: Optional[str] = None
    action: GovernanceAction
    reason_code: GovernanceReason
    reason_detail: str = ""
    conditions_json: Optional[str] = None
    review_decision_id: Optional[str] = None


class AggregatedGovernanceVerdict(BaseModel):
    """Aggregated governance decision across multiple requests."""

    model_config = ConfigDict(extra="forbid")

    action: GovernanceAction
    reason_code: GovernanceReason
    reason_detail: str = ""
    objective_id: str = Field(..., min_length=1)
    request_results: List[RequestGovernanceResult] = Field(default_factory=list)
    all_requests_final: bool = False
    expected_request_count: int = 0
    observed_request_count: int = 0

    @field_validator("expected_request_count", "observed_request_count")
    @classmethod
    def non_negative(cls, v):
        """Counts must be non-negative."""
        if v < 0:
            raise ValueError("Count must be non-negative")
        return v


def map_raw_decision_to_action(raw_decision_type: Optional[str]) -> GovernanceAction:
    """
    Map raw Aegis decision type to governance action.

    - "approved" → ALLOW
    - "conditional_approval" → HOLD
    - "revision_requested" → HOLD
    - "rejected" → DENY
    - None, empty, pending, unknown → HOLD (not DENY)

    Unknown always maps to HOLD to ensure fail-closed (human review),
    not DENY (explicit rejection).
    """
    if not raw_decision_type:
        return GovernanceAction.HOLD

    normalized = raw_decision_type.lower().strip()

    if normalized == "approved":
        return GovernanceAction.ALLOW
    elif normalized == "conditional_approval":
        return GovernanceAction.HOLD
    elif normalized == "revision_requested":
        return GovernanceAction.HOLD
    elif normalized == "rejected":
        return GovernanceAction.DENY
    else:
        # Unknown maps to HOLD (human review), not DENY
        return GovernanceAction.HOLD


def map_raw_decision_to_reason(raw_decision_type: Optional[str]) -> GovernanceReason:
    """Map raw decision type to reason code for individual request."""
    if not raw_decision_type:
        return GovernanceReason.DECISION_PENDING

    normalized = raw_decision_type.lower().strip()

    if normalized == "approved":
        return GovernanceReason.ALL_REQUESTS_APPROVED
    elif normalized == "conditional_approval":
        return GovernanceReason.CONDITIONAL_APPROVAL
    elif normalized == "revision_requested":
        return GovernanceReason.REVISION_REQUESTED
    elif normalized == "rejected":
        return GovernanceReason.EXPLICIT_REJECTION
    else:
        return GovernanceReason.UNKNOWN_DECISION_TYPE


def aggregate_governance_verdicts(
    request_results: List[RequestGovernanceResult],
    objective_id: str,
    expected_request_count: int,
) -> AggregatedGovernanceVerdict:
    """
    Aggregate individual request decisions into single verdict.

    Deterministic precedence (first match wins):
    1. No requests → HOLD / NO_REQUESTS
    2. Any rejected → DENY / EXPLICIT_REJECTION
    3. observed < expected → HOLD / REQUEST_PENDING
    4. Any pending → HOLD / DECISION_PENDING
    5. Any unknown → HOLD / UNKNOWN_DECISION_TYPE
    6. Any revision_requested → HOLD / REVISION_REQUESTED
    7. Any conditional_approval → HOLD / CONDITIONAL_APPROVAL
    8. All approved → ALLOW / ALL_REQUESTS_APPROVED

    ALLOW requires:
    - expected_request_count > 0
    - observed_request_count == expected_request_count
    - every request explicitly approved
    """
    observed_count = len(request_results)

    # Check for duplicate request IDs
    request_ids = [r.request_id for r in request_results]
    if len(request_ids) != len(set(request_ids)):
        return AggregatedGovernanceVerdict(
            action=GovernanceAction.HOLD,
            reason_code=GovernanceReason.DECISION_PENDING,
            reason_detail="Duplicate request IDs detected",
            objective_id=objective_id,
            request_results=request_results,
            all_requests_final=False,
            expected_request_count=expected_request_count,
            observed_request_count=observed_count,
        )

    # 1. No requests
    if observed_count == 0:
        return AggregatedGovernanceVerdict(
            action=GovernanceAction.HOLD,
            reason_code=GovernanceReason.NO_REQUESTS,
            reason_detail="No requests to aggregate",
            objective_id=objective_id,
            request_results=request_results,
            all_requests_final=False,
            expected_request_count=expected_request_count,
            observed_request_count=observed_count,
        )

    # 2. Any rejected
    for result in request_results:
        if result.action == GovernanceAction.DENY:
            return AggregatedGovernanceVerdict(
                action=GovernanceAction.DENY,
                reason_code=GovernanceReason.EXPLICIT_REJECTION,
                reason_detail=f"Request {result.request_id} was explicitly rejected",
                objective_id=objective_id,
                request_results=request_results,
                all_requests_final=False,
                expected_request_count=expected_request_count,
                observed_request_count=observed_count,
            )

    # 3. Observed != expected (count mismatch)
    if observed_count != expected_request_count:
        return AggregatedGovernanceVerdict(
            action=GovernanceAction.HOLD,
            reason_code=GovernanceReason.REQUEST_PENDING,
            reason_detail=f"Expected {expected_request_count} requests, observed {observed_count}",
            objective_id=objective_id,
            request_results=request_results,
            all_requests_final=False,
            expected_request_count=expected_request_count,
            observed_request_count=observed_count,
        )

    # 4. Any pending decision
    for result in request_results:
        if result.action == GovernanceAction.HOLD and (
            result.reason_code == GovernanceReason.DECISION_PENDING
        ):
            return AggregatedGovernanceVerdict(
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.DECISION_PENDING,
                reason_detail=f"Request {result.request_id} has pending decision",
                objective_id=objective_id,
                request_results=request_results,
                all_requests_final=False,
                expected_request_count=expected_request_count,
                observed_request_count=observed_count,
            )

    # 5. Any unknown decision type
    for result in request_results:
        if result.reason_code == GovernanceReason.UNKNOWN_DECISION_TYPE:
            return AggregatedGovernanceVerdict(
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.UNKNOWN_DECISION_TYPE,
                reason_detail=f"Request {result.request_id} has unknown decision type: {result.raw_decision_type}",
                objective_id=objective_id,
                request_results=request_results,
                all_requests_final=False,
                expected_request_count=expected_request_count,
                observed_request_count=observed_count,
            )

    # 6. Any revision_requested
    for result in request_results:
        if result.reason_code == GovernanceReason.REVISION_REQUESTED:
            return AggregatedGovernanceVerdict(
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.REVISION_REQUESTED,
                reason_detail=f"Request {result.request_id} requested revision",
                objective_id=objective_id,
                request_results=request_results,
                all_requests_final=False,
                expected_request_count=expected_request_count,
                observed_request_count=observed_count,
            )

    # 7. Any conditional_approval
    for result in request_results:
        if result.reason_code == GovernanceReason.CONDITIONAL_APPROVAL:
            return AggregatedGovernanceVerdict(
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.CONDITIONAL_APPROVAL,
                reason_detail=f"Request {result.request_id} has conditional approval",
                objective_id=objective_id,
                request_results=request_results,
                all_requests_final=False,
                expected_request_count=expected_request_count,
                observed_request_count=observed_count,
            )

    # 8. All approved (only reached if all checks above passed)
    if expected_request_count == 0:
        # Cannot ALLOW with zero expected requests
        return AggregatedGovernanceVerdict(
            action=GovernanceAction.HOLD,
            reason_code=GovernanceReason.NO_REQUESTS,
            reason_detail="Expected count is zero, cannot grant approval",
            objective_id=objective_id,
            request_results=request_results,
            all_requests_final=True,
            expected_request_count=expected_request_count,
            observed_request_count=observed_count,
        )

    # Verify all are explicitly approved
    all_approved = all(
        result.action == GovernanceAction.ALLOW
        and result.reason_code == GovernanceReason.ALL_REQUESTS_APPROVED
        for result in request_results
    )

    if not all_approved:
        # This shouldn't happen given the checks above, but safeguard
        return AggregatedGovernanceVerdict(
            action=GovernanceAction.HOLD,
            reason_code=GovernanceReason.DECISION_PENDING,
            reason_detail="Not all requests are explicitly approved",
            objective_id=objective_id,
            request_results=request_results,
            all_requests_final=False,
            expected_request_count=expected_request_count,
            observed_request_count=observed_count,
        )

    return AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        reason_detail=f"All {observed_count} requests explicitly approved",
        objective_id=objective_id,
        request_results=request_results,
        all_requests_final=True,
        expected_request_count=expected_request_count,
        observed_request_count=observed_count,
    )
