"""
Governance Orchestration Service for LdcDemo finance invoice workflows.

Coordinates:
- Finance routing decisions (FinanceTargetSystem)
- Authority hierarchy requirements (ApprovalDecision)
- Aegis governance adapter (submit and check verdicts)

No database access, no LLM calls, no persistence. Deterministic with fail-closed
semantics: any error or unknown result → HOLD, never ALLOW.

Implements 15 orchestration rules ensuring approval authorities never exceed
their mandate and routing conflicts are escalated.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from src.finance_router import (
    FinanceRoutingDecision,
    RoutingStatus,
    FinanceTargetSystem,
)
from src.authority_hierarchy import (
    ApprovalDecision,
    AuthorityLevel,
    ApprovalReason,
    RiskLevel,
)
from src.aegis_governance_adapter import (
    AegisGovernanceAdapter,
    GovernanceProposal,
    ProposedTargetSystem,
    GovernanceRiskFlag,
    AegisGovernanceAdapterException,
)
from src.governance_contract import (
    GovernanceAction,
    AggregatedGovernanceVerdict,
)


class GovernanceOrchestrationStatus(str, Enum):
    """Orchestration outcome status."""
    ALLOW = "ALLOW"
    HOLD = "HOLD"
    DENY = "DENY"


class GovernanceOrchestrationReason(str, Enum):
    """Reason code for orchestration decision."""
    GOVERNANCE_APPROVED = "GOVERNANCE_APPROVED"
    ROUTING_REVIEW_REQUIRED = "ROUTING_REVIEW_REQUIRED"
    AUTHORITY_ESCALATION_REQUIRED = "AUTHORITY_ESCALATION_REQUIRED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    GOVERNANCE_PENDING = "GOVERNANCE_PENDING"
    GOVERNANCE_REJECTED = "GOVERNANCE_REJECTED"
    AEGIS_UNAVAILABLE = "AEGIS_UNAVAILABLE"
    ORCHESTRATION_ERROR = "ORCHESTRATION_ERROR"


class GovernanceOrchestrationResult(BaseModel):
    """Complete orchestration decision result."""
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(..., min_length=1, max_length=64)
    correlation_id: str = Field(..., min_length=1, max_length=64)
    proposed_target_system: str  # System name as string
    evaluating_authority: str  # Current authority level
    required_authority: str  # Authority needed
    authority_outcome: str  # Whether sufficient authority exists
    governance_status: GovernanceOrchestrationStatus
    reason_code: GovernanceOrchestrationReason
    objective_id: Optional[str] = None  # From Aegis submission
    request_ids: list[str] = Field(default_factory=list)  # From Aegis
    review_decision_ids: list[str] = Field(default_factory=list)  # From Aegis
    routing_workflow_run_id: Optional[str] = None
    explanation: str = ""


class GovernanceOrchestrator:
    """
    Coordinates governance decisions across routing, authority, and Aegis layers.

    Enforces 15 rules:
    1. Routing HUMAN_REVIEW → HOLD / ROUTING_REVIEW_REQUIRED
    2. Authority HUMAN_REVIEW → HOLD / HUMAN_APPROVAL_REQUIRED
    3. Insufficient authority → HOLD / AUTHORITY_ESCALATION_REQUIRED
    4. Only valid route + sufficient authority → ALLOW
    5-7. Build privacy-safe GovernanceProposal and use Aegis adapter
    8-11. Map Aegis results and handle errors
    12. Preserve Aegis IDs
    13-15. Never persist, ensure privacy, reject extra fields
    """

    def __init__(self, adapter: AegisGovernanceAdapter):
        """
        Initialize orchestrator with injected Aegis governance adapter.

        Args:
            adapter: AegisGovernanceAdapter instance (already authenticated).
        """
        self.adapter = adapter

    async def orchestrate(
        self,
        routing_decision: FinanceRoutingDecision,
        approval_decision: ApprovalDecision,
        correlation_id: str,
        case_id: str,
    ) -> GovernanceOrchestrationResult:
        """
        Orchestrate governance decision using routing, authority, and Aegis.

        Args:
            routing_decision: FinanceRoutingDecision from router
            approval_decision: ApprovalDecision from authority hierarchy
            correlation_id: Invoice correlation ID for tracing
            case_id: Invoice/case identifier

        Returns:
            GovernanceOrchestrationResult with status and reason code

        Implements 15 orchestration rules with fail-closed semantics.
        """
        try:
            # Rule 1: Routing HUMAN_REVIEW → HOLD
            if routing_decision.routing_status == RoutingStatus.HUMAN_REVIEW:
                return GovernanceOrchestrationResult(
                    case_id=case_id,
                    correlation_id=correlation_id,
                    proposed_target_system=routing_decision.target_system.value,
                    evaluating_authority="SYSTEM",
                    required_authority="HUMAN_REVIEWER",
                    authority_outcome="insufficient",
                    governance_status=GovernanceOrchestrationStatus.HOLD,
                    reason_code=GovernanceOrchestrationReason.ROUTING_REVIEW_REQUIRED,
                    routing_workflow_run_id=routing_decision.workflow_run_id,
                    explanation=f"Router requires human review: {routing_decision.explanation}",
                )

            # Rule 2: Authority HUMAN_REVIEW → HOLD
            if (
                AuthorityLevel.HUMAN_APPROVER in approval_decision.required_authorities
                and len(approval_decision.required_authorities) == 1
            ):
                return GovernanceOrchestrationResult(
                    case_id=case_id,
                    correlation_id=correlation_id,
                    proposed_target_system=routing_decision.target_system.value,
                    evaluating_authority="HIERARCHY",
                    required_authority=AuthorityLevel.HUMAN_APPROVER.value,
                    authority_outcome="human_review",
                    governance_status=GovernanceOrchestrationStatus.HOLD,
                    reason_code=GovernanceOrchestrationReason.HUMAN_APPROVAL_REQUIRED,
                    routing_workflow_run_id=routing_decision.workflow_run_id,
                    explanation=f"Authority hierarchy requires human approval: {approval_decision.approval_chain_notes}",
                )

            # Determine evaluating authority (highest required)
            evaluating_authority = self._highest_authority(
                approval_decision.required_authorities
            )

            # Rule 3: Insufficient authority for the proposed target system
            # Authority L1 cannot approve any finance routing
            # Authority L2 can approve lower-risk routes but not high-value
            # Authority L3 can approve most routes but not critical-risk
            # Only HUMAN_APPROVER can override all
            authority_sufficient = self._check_authority_sufficient(
                evaluating_authority,
                routing_decision.route_confidence,
                approval_decision.highest_risk_level,
            )

            if not authority_sufficient:
                # Determine what authority is needed
                required_for_approval = self._determine_required_authority(
                    routing_decision.route_confidence,
                    approval_decision.highest_risk_level,
                )

                return GovernanceOrchestrationResult(
                    case_id=case_id,
                    correlation_id=correlation_id,
                    proposed_target_system=routing_decision.target_system.value,
                    evaluating_authority=evaluating_authority.value,
                    required_authority=required_for_approval.value,
                    authority_outcome="insufficient",
                    governance_status=GovernanceOrchestrationStatus.HOLD,
                    reason_code=GovernanceOrchestrationReason.AUTHORITY_ESCALATION_REQUIRED,
                    routing_workflow_run_id=routing_decision.workflow_run_id,
                    explanation=f"Authority {evaluating_authority.value} insufficient for {required_for_approval.value}. Risk: {approval_decision.highest_risk_level.value}",
                )

            # Rule 4 & 5: Valid route + sufficient authority → build proposal and submit to Aegis
            proposal = self._build_governance_proposal(
                routing_decision, approval_decision, case_id, correlation_id
            )

            # Rule 6: Submit to Aegis
            try:
                submission = await self.adapter.submit(proposal)
            except AegisGovernanceAdapterException as e:
                return GovernanceOrchestrationResult(
                    case_id=case_id,
                    correlation_id=correlation_id,
                    proposed_target_system=routing_decision.target_system.value,
                    evaluating_authority=evaluating_authority.value,
                    required_authority=evaluating_authority.value,
                    authority_outcome="sufficient",
                    governance_status=GovernanceOrchestrationStatus.HOLD,
                    reason_code=GovernanceOrchestrationReason.AEGIS_UNAVAILABLE,
                    routing_workflow_run_id=routing_decision.workflow_run_id,
                    explanation=f"Failed to submit to Aegis: {str(e)}",
                )

            # Rule 7: Check Aegis verdict (do not poll, single check only)
            try:
                verdict = await self.adapter.check_once(submission)
            except Exception as e:
                return GovernanceOrchestrationResult(
                    case_id=case_id,
                    correlation_id=correlation_id,
                    proposed_target_system=routing_decision.target_system.value,
                    evaluating_authority=evaluating_authority.value,
                    required_authority=evaluating_authority.value,
                    authority_outcome="sufficient",
                    governance_status=GovernanceOrchestrationStatus.HOLD,
                    reason_code=GovernanceOrchestrationReason.AEGIS_UNAVAILABLE,
                    objective_id=submission.objective_id,
                    routing_workflow_run_id=routing_decision.workflow_run_id,
                    explanation=f"Failed to check Aegis verdict: {str(e)}",
                )

            # Rule 8-11: Map Aegis verdict to orchestration result
            return self._map_aegis_verdict_to_result(
                verdict,
                case_id,
                correlation_id,
                routing_decision.target_system.value,
                evaluating_authority.value,
                routing_decision.workflow_run_id,
            )

        except Exception as e:
            # Catch-all for unexpected errors → HOLD / ORCHESTRATION_ERROR
            return GovernanceOrchestrationResult(
                case_id=case_id,
                correlation_id=correlation_id,
                proposed_target_system="UNKNOWN",
                evaluating_authority="UNKNOWN",
                required_authority="UNKNOWN",
                authority_outcome="error",
                governance_status=GovernanceOrchestrationStatus.HOLD,
                reason_code=GovernanceOrchestrationReason.ORCHESTRATION_ERROR,
                explanation=f"Unexpected orchestration error: {str(e)}",
            )

    def _highest_authority(
        self, authorities: list[AuthorityLevel]
    ) -> AuthorityLevel:
        """Return highest authority from list."""
        if not authorities:
            return AuthorityLevel.L1_PROCESSOR

        authority_rank = {
            AuthorityLevel.L1_PROCESSOR: 1,
            AuthorityLevel.L2_SUPERVISOR: 2,
            AuthorityLevel.L3_CONTROLLER: 3,
            AuthorityLevel.HUMAN_APPROVER: 4,
        }

        return max(authorities, key=lambda a: authority_rank.get(a, 0))

    def _check_authority_sufficient(
        self,
        evaluating_authority: AuthorityLevel,
        route_confidence: float,
        risk_level: RiskLevel,
    ) -> bool:
        """
        Determine if evaluating authority is sufficient for this invoice.

        Rules:
        - HUMAN_APPROVER: always sufficient
        - L3_CONTROLLER: sufficient for most (not CRITICAL risk)
        - L2_SUPERVISOR: sufficient for LOW/MEDIUM risk with decent confidence
        - L1_PROCESSOR: sufficient for LOW risk only with high confidence
        """
        if evaluating_authority == AuthorityLevel.HUMAN_APPROVER:
            return True

        if evaluating_authority == AuthorityLevel.L3_CONTROLLER:
            # L3 can handle anything except CRITICAL risk
            return risk_level != RiskLevel.CRITICAL

        if evaluating_authority == AuthorityLevel.L2_SUPERVISOR:
            # L2 can handle LOW/MEDIUM risk with reasonable confidence
            return (
                risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM)
                and route_confidence >= 0.75
            )

        if evaluating_authority == AuthorityLevel.L1_PROCESSOR:
            # L1 can handle LOW risk only with high confidence
            return risk_level == RiskLevel.LOW and route_confidence >= 0.80

        return False

    def _determine_required_authority(
        self,
        route_confidence: float,
        risk_level: RiskLevel,
    ) -> AuthorityLevel:
        """Determine what authority level is needed for this invoice."""
        if risk_level == RiskLevel.CRITICAL:
            return AuthorityLevel.HUMAN_APPROVER

        if risk_level == RiskLevel.HIGH:
            return AuthorityLevel.L3_CONTROLLER

        if risk_level == RiskLevel.MEDIUM:
            # Medium risk needs at least L2, or L3 if low confidence
            if route_confidence < 0.75:
                return AuthorityLevel.L3_CONTROLLER
            return AuthorityLevel.L2_SUPERVISOR

        # Low risk with good confidence can be L1 or L2
        return AuthorityLevel.L1_PROCESSOR

    def _build_governance_proposal(
        self,
        routing_decision: FinanceRoutingDecision,
        approval_decision: ApprovalDecision,
        case_id: str,
        correlation_id: str,
    ) -> GovernanceProposal:
        """
        Build privacy-safe GovernanceProposal for Aegis submission.

        Only includes:
        - Identifiers (invoice_id, correlation_id)
        - Target system
        - Amounts and confidence
        - Risk flags
        - Agent ID (orchestrator)

        Never includes:
        - Supplier/customer names
        - Invoice descriptions
        - Line items
        - Bank details
        """
        # Map risk levels to risk flags
        risk_flags = []

        for reason in approval_decision.approval_reasons:
            if reason == ApprovalReason.DUPLICATE_DETECTED:
                risk_flags.append(GovernanceRiskFlag.DUPLICATE_INVOICE)
            elif reason == ApprovalReason.BANK_CHANGE:
                risk_flags.append(GovernanceRiskFlag.BANK_DETAILS_CHANGED)
            elif reason == ApprovalReason.UNKNOWN_VENDOR:
                risk_flags.append(GovernanceRiskFlag.UNKNOWN_VENDOR)
            elif reason == ApprovalReason.MISSING_REFERENCE:
                risk_flags.append(GovernanceRiskFlag.MISSING_REFERENCE)
            elif reason == ApprovalReason.LOW_CONFIDENCE:
                risk_flags.append(GovernanceRiskFlag.LOW_CONFIDENCE)
            elif reason == ApprovalReason.AMOUNT_THRESHOLD:
                risk_flags.append(GovernanceRiskFlag.AMOUNT_THRESHOLD)
            elif reason == ApprovalReason.ROUTING_CONFLICT:
                risk_flags.append(GovernanceRiskFlag.ROUTING_CONFLICT)

        # Map target system
        system_map = {
            FinanceTargetSystem.VESON_IMOS: ProposedTargetSystem.VESON_IMOS,
            FinanceTargetSystem.SMARTPAL: ProposedTargetSystem.SMARTPAL,
            FinanceTargetSystem.ORACLE_FUSION: ProposedTargetSystem.ORACLE_FUSION,
            FinanceTargetSystem.UNDETERMINED: ProposedTargetSystem.UNDETERMINED,
        }

        proposed_system = system_map.get(
            routing_decision.target_system, ProposedTargetSystem.UNDETERMINED
        )

        return GovernanceProposal(
            invoice_id=case_id,
            correlation_id=correlation_id,
            agent_id="governance_orchestrator_ldc_v1",
            proposed_target_system=proposed_system,
            extraction_confidence=routing_decision.route_confidence,
            risk_flags=risk_flags,
        )

    def _map_aegis_verdict_to_result(
        self,
        verdict: AggregatedGovernanceVerdict,
        case_id: str,
        correlation_id: str,
        target_system: str,
        evaluating_authority: str,
        routing_workflow_run_id: Optional[str],
    ) -> GovernanceOrchestrationResult:
        """
        Map Aegis AggregatedGovernanceVerdict to GovernanceOrchestrationResult.

        Rule 8: ALLOW → ALLOW / GOVERNANCE_APPROVED
        Rule 9: DENY → DENY / GOVERNANCE_REJECTED
        Rule 10-11: HOLD/pending/unknown → HOLD / GOVERNANCE_PENDING or other
        """
        # Rule 12: Preserve genuine Aegis IDs
        objective_id = verdict.objective_id
        request_ids = [r.request_id for r in verdict.request_results]
        review_decision_ids = [
            r.review_decision_id
            for r in verdict.request_results
            if r.review_decision_id
        ]

        # Map Aegis action to orchestration status and reason
        if verdict.action == GovernanceAction.ALLOW:
            return GovernanceOrchestrationResult(
                case_id=case_id,
                correlation_id=correlation_id,
                proposed_target_system=target_system,
                evaluating_authority=evaluating_authority,
                required_authority=evaluating_authority,
                authority_outcome="sufficient",
                governance_status=GovernanceOrchestrationStatus.ALLOW,
                reason_code=GovernanceOrchestrationReason.GOVERNANCE_APPROVED,
                objective_id=objective_id,
                request_ids=request_ids,
                review_decision_ids=review_decision_ids,
                routing_workflow_run_id=routing_workflow_run_id,
                explanation="Aegis approved: all requests explicitly approved",
            )

        elif verdict.action == GovernanceAction.DENY:
            return GovernanceOrchestrationResult(
                case_id=case_id,
                correlation_id=correlation_id,
                proposed_target_system=target_system,
                evaluating_authority=evaluating_authority,
                required_authority=evaluating_authority,
                authority_outcome="sufficient",
                governance_status=GovernanceOrchestrationStatus.DENY,
                reason_code=GovernanceOrchestrationReason.GOVERNANCE_REJECTED,
                objective_id=objective_id,
                request_ids=request_ids,
                review_decision_ids=review_decision_ids,
                routing_workflow_run_id=routing_workflow_run_id,
                explanation=f"Aegis rejected: {verdict.reason_detail}",
            )

        else:  # GovernanceAction.HOLD
            # Map HOLD reason to our reason codes
            reason_code = GovernanceOrchestrationReason.GOVERNANCE_PENDING

            return GovernanceOrchestrationResult(
                case_id=case_id,
                correlation_id=correlation_id,
                proposed_target_system=target_system,
                evaluating_authority=evaluating_authority,
                required_authority=evaluating_authority,
                authority_outcome="sufficient",
                governance_status=GovernanceOrchestrationStatus.HOLD,
                reason_code=reason_code,
                objective_id=objective_id,
                request_ids=request_ids,
                review_decision_ids=review_decision_ids,
                routing_workflow_run_id=routing_workflow_run_id,
                explanation=f"Aegis holding: {verdict.reason_detail}",
            )
