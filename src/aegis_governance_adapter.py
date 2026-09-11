"""
Aegis SDK communication adapter for governance submissions and verdict checks.

Only explicitly validated governance metadata fields are submitted by this adapter.
Raw invoice text, supplier/customer free text, line-item descriptions and
bank-detail fields are not part of the adapter contract. Uses dependency injection
of the client — never reads .env or constructs credentials. Fail-closed: SDK errors
or missing data → HOLD, never ALLOW.
"""

import re
import math
from enum import Enum
from typing import Optional
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator, ConfigDict

from src.governance_contract import (
    GovernanceAction,
    GovernanceReason,
    RequestGovernanceResult,
    AggregatedGovernanceVerdict,
    map_raw_decision_to_action,
    map_raw_decision_to_reason,
    aggregate_governance_verdicts,
)


class ProposedTargetSystem(str, Enum):
    """Approved target systems for invoice routing."""

    VESON_IMOS = "VESON_IMOS"
    SMARTPAL = "SMARTPAL"
    ORACLE_FUSION = "ORACLE_FUSION"
    UNDETERMINED = "UNDETERMINED"


class GovernanceRiskFlag(str, Enum):
    """Approved risk flags for governance proposals."""

    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    BANK_DETAILS_CHANGED = "BANK_DETAILS_CHANGED"
    UNKNOWN_VENDOR = "UNKNOWN_VENDOR"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AMOUNT_THRESHOLD = "AMOUNT_THRESHOLD"
    ROUTING_CONFLICT = "ROUTING_CONFLICT"


class GovernanceProposal(BaseModel):
    """Governance metadata for Aegis submission.

    Contains only validated governance metadata: identifiers, amounts, confidence,
    target system, and risk flags. Title and description are constructed internally
    by the adapter, never supplied by caller. No raw invoice text, supplier/customer
    names, line-item descriptions, or bank details fields.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_id: str = Field(..., min_length=1, max_length=64)
    correlation_id: str = Field(..., min_length=1, max_length=64)
    agent_id: str = Field(..., min_length=1, max_length=64)
    proposed_target_system: ProposedTargetSystem
    proposed_accounting_code: Optional[str] = None
    gross_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    extraction_confidence: float = Field(..., ge=0.0, le=1.0)
    risk_flags: list[GovernanceRiskFlag] = Field(default_factory=list)

    @field_validator("invoice_id", "correlation_id", "agent_id")
    @classmethod
    def validate_identifier_format(cls, v):
        """Validate identifier format: alphanumeric, underscore, hyphen, period only."""
        # Check for control characters and newlines
        if any(ord(c) < 32 for c in v):
            raise ValueError(
                f"Identifier cannot contain control characters: {repr(v)}"
            )
        if not re.match(r"^[A-Za-z0-9_\-\.]+$", v):
            raise ValueError(
                f"Identifier must contain only alphanumeric, underscore, hyphen, or period: {v}"
            )
        return v

    @field_validator("proposed_accounting_code")
    @classmethod
    def validate_accounting_code(cls, v):
        """Validate accounting code: digits, optional hyphens/periods, LdcDemo GL format."""
        if v is None:
            return v
        if len(v) > 32:
            raise ValueError(f"Accounting code must not exceed 32 characters: {v}")
        if not re.match(r"^[0-9\-\.]+$", v):
            raise ValueError(
                f"Accounting code must contain only digits, hyphens, or periods (LdcDemo GL format): {v}"
            )
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v):
        """Validate currency: uppercase ISO 3-letter code (optional)."""
        if v is None:
            return v
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError(f"Currency must be uppercase ISO 3-letter code: {v}")
        return v

    @field_validator("gross_amount")
    @classmethod
    def validate_gross_amount(cls, v):
        """Validate gross amount: non-negative, no NaN or infinite values."""
        if v is None:
            return v
        if math.isnan(float(v)) or math.isinf(float(v)):
            raise ValueError(f"Gross amount cannot be NaN or infinite: {v}")
        if v < 0:
            raise ValueError(f"Gross amount must be non-negative: {v}")
        return v


class AegisSubmission(BaseModel):
    """Result of submitting a proposal to Aegis."""

    model_config = ConfigDict(extra="forbid")

    objective_id: str = Field(..., min_length=1)
    objective_status: str = ""
    execution_status: str = ""
    expected_request_count: int = 0
    correlation_id: str = Field(..., min_length=1)

    @field_validator("expected_request_count")
    @classmethod
    def non_negative(cls, v):
        """Expected count must be non-negative."""
        if v < 0:
            raise ValueError("expected_request_count must be non-negative")
        return v


class AegisGovernanceAdapterException(Exception):
    """Adapter-level exception for SDK errors."""

    pass


class AegisGovernanceAdapter:
    """
    Adapter for submitting governance proposals to Aegis and checking verdicts.

    Communicates with:
    - client.objectives.create() — submit a new objective
    - client.objectives.trigger_execution() — request decomposition into requests
    - client.requests.list(objective_id=...) — retrieve all requests
    - client.review_decisions.get_latest(request_id) — fetch latest decision

    All errors (auth, transport, parsing) result in HOLD/AEGIS_UNAVAILABLE, never ALLOW.
    """

    def __init__(self, client):
        """
        Initialize adapter with injected Aegis client.

        Args:
            client: Already-authenticated AgenticOSClient instance.
                   Must have objectives, requests, and review_decisions modules.
        """
        self.client = client

    async def submit(self, proposal: GovernanceProposal) -> AegisSubmission:
        """
        Submit a governance proposal to Aegis.

        Constructs internal title and description from validated proposal fields,
        then creates an objective with privacy-safe governance metadata and triggers execution.

        Args:
            proposal: GovernanceProposal with safe metadata (title/description omitted)

        Returns:
            AegisSubmission with objective_id and expected request count

        Raises:
            AegisGovernanceAdapterException: If objective creation or trigger fails
        """
        try:
            # Construct curated title and description internally (never from caller)
            internal_title = f"Finance governance request {proposal.invoice_id}"
            internal_description = (
                f"Evaluate proposed route {proposal.proposed_target_system.value} "
                f"for invoice {proposal.invoice_id}."
            )

            # Build explicit privacy-safe metadata (only 5 approved keys, no PII)
            # Sort and deduplicate risk flags for deterministic output
            risk_flags_sorted = sorted(
                set(flag.value for flag in proposal.risk_flags)
            )

            approved_metadata = {
                "invoice_id": proposal.invoice_id,
                "correlation_id": proposal.correlation_id,
                "proposed_target_system": proposal.proposed_target_system.value,
                "risk_flags": risk_flags_sorted,
                "governance_contract_version": "ldc-finance-governance-v1",
            }

            # Create objective with internally-constructed metadata and governance context
            objective = await self.client.objectives.create(
                title=internal_title,
                description=internal_description,
                agent_id=proposal.agent_id,
                metadata=approved_metadata,
            )

            # Trigger execution to decompose into requests
            trigger_result = await self.client.objectives.trigger_execution(
                objective.id
            )

            # Extract expected request count from trigger result
            # trigger_result may have "task_count" field if already decomposed
            expected_count = 0
            if isinstance(trigger_result, dict):
                task_count = trigger_result.get("task_count")
                if isinstance(task_count, int) and task_count >= 0:
                    expected_count = task_count

            # Ensure all fields are strings
            obj_status = getattr(objective, "status", None)
            if not isinstance(obj_status, str):
                obj_status = str(obj_status) if obj_status else "unknown"

            exec_status = "unknown"
            if isinstance(trigger_result, dict):
                exec_status = str(trigger_result.get("status", "unknown"))

            return AegisSubmission(
                objective_id=str(objective.id),
                objective_status=obj_status,
                execution_status=exec_status,
                expected_request_count=expected_count,
                correlation_id=proposal.correlation_id,
            )
        except Exception as e:
            raise AegisGovernanceAdapterException(
                f"Failed to submit governance proposal: {str(e)}"
            ) from e

    async def check_once(
        self, submission: AegisSubmission
    ) -> AggregatedGovernanceVerdict:
        """
        Check the current governance verdict for a submitted objective.

        Lists all requests for the objective, fetches the latest review decision
        for each, and aggregates using deterministic precedence.

        Args:
            submission: AegisSubmission with objective_id and expected request count

        Returns:
            AggregatedGovernanceVerdict with fail-closed semantics

        Note:
            Does not poll or sleep. Returns current state as a single snapshot.
            SDK errors result in HOLD/AEGIS_UNAVAILABLE, not exceptions.
        """
        try:
            # List all requests for the objective
            requests_response = await self.client.requests.list(
                objective_id=submission.objective_id
            )

            if not hasattr(requests_response, "items"):
                # Unexpected response format
                return AggregatedGovernanceVerdict(
                    action=GovernanceAction.HOLD,
                    reason_code=GovernanceReason.AEGIS_UNAVAILABLE,
                    reason_detail="Unexpected requests response format",
                    objective_id=submission.objective_id,
                    request_results=[],
                    all_requests_final=False,
                    expected_request_count=submission.expected_request_count,
                    observed_request_count=0,
                )

            requests_list = requests_response.items or []
            request_results = []

            # Process each request
            for request in requests_list:
                request_id = getattr(request, "id", None)
                if not request_id:
                    # Malformed request, hold
                    return AggregatedGovernanceVerdict(
                        action=GovernanceAction.HOLD,
                        reason_code=GovernanceReason.AEGIS_UNAVAILABLE,
                        reason_detail="Request missing ID",
                        objective_id=submission.objective_id,
                        request_results=request_results,
                        all_requests_final=False,
                        expected_request_count=submission.expected_request_count,
                        observed_request_count=len(requests_list),
                    )

                # Fetch latest review decision for this request
                try:
                    review_decision = (
                        await self.client.review_decisions.get_latest(request_id)
                    )
                except Exception:
                    # Decision fetch failed, treat as pending
                    review_decision = None

                if review_decision is None:
                    # No decision yet → pending
                    result = RequestGovernanceResult(
                        request_id=request_id,
                        raw_decision_type=None,
                        action=GovernanceAction.HOLD,
                        reason_code=GovernanceReason.DECISION_PENDING,
                        review_decision_id=None,
                    )
                else:
                    # Convert Aegis decision to governance action
                    decision_type = getattr(review_decision, "decision_type", None)
                    action = map_raw_decision_to_action(decision_type)
                    reason = map_raw_decision_to_reason(decision_type)

                    conditions_json = getattr(review_decision, "conditions_json", None)
                    if conditions_json is not None and not isinstance(conditions_json, str):
                        conditions_json = str(conditions_json) if conditions_json else None

                    review_decision_id = getattr(review_decision, "id", "")
                    if not isinstance(review_decision_id, str):
                        review_decision_id = str(review_decision_id) if review_decision_id else ""

                    result = RequestGovernanceResult(
                        request_id=request_id,
                        raw_decision_type=decision_type,
                        action=action,
                        reason_code=reason,
                        conditions_json=conditions_json,
                        review_decision_id=review_decision_id,
                    )

                request_results.append(result)

            # Aggregate all requests into final verdict
            verdict = aggregate_governance_verdicts(
                request_results,
                objective_id=submission.objective_id,
                expected_request_count=submission.expected_request_count,
            )

            return verdict

        except Exception as e:
            # Any SDK error → HOLD / AEGIS_UNAVAILABLE
            return AggregatedGovernanceVerdict(
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.AEGIS_UNAVAILABLE,
                reason_detail=f"SDK error during check: {str(e)}",
                objective_id=submission.objective_id,
                request_results=[],
                all_requests_final=False,
                expected_request_count=submission.expected_request_count,
                observed_request_count=0,
            )
