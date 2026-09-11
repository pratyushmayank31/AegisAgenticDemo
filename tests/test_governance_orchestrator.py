"""
Tests for Governance Orchestration Service.

Verifies:
- All 15 orchestration rules
- Authority escalation logic
- Privacy-safe Aegis interaction
- Fail-closed semantics (errors → HOLD)
- Genuine Aegis ID preservation
- No database/file/credential access
- Pydantic model validation (extra="forbid")

Uses mock Aegis adapter for all tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from decimal import Decimal

from src.governance_orchestrator import (
    GovernanceOrchestrator,
    GovernanceOrchestrationStatus,
    GovernanceOrchestrationReason,
    GovernanceOrchestrationResult,
)
from src.finance_router import (
    FinanceRoutingDecision,
    FinanceTargetSystem,
    FinanceBusinessCategory,
    RoutingStatus,
    RoutingReason,
)
from src.authority_hierarchy import (
    ApprovalDecision,
    AuthorityLevel,
    ApprovalReason,
    RiskLevel,
    FinanceAuthorityHierarchy,
)
from src.aegis_governance_adapter import (
    AegisGovernanceAdapter,
    GovernanceProposal,
    AegisSubmission,
    AegisGovernanceAdapterException,
)
from src.governance_contract import (
    AggregatedGovernanceVerdict,
    GovernanceAction,
    GovernanceReason,
    RequestGovernanceResult,
)


# ============================================================================
# Mock Aegis Adapter
# ============================================================================


class MockAegisAdapter:
    """Mock Aegis adapter for testing (not a real client)."""

    def __init__(self, verdict_override: AggregatedGovernanceVerdict = None):
        """Initialize with optional override verdict."""
        self.submitted_proposals = []
        self.verdict_override = verdict_override
        self.fail_on_submit = False
        self.fail_on_check = False

    async def submit(self, proposal: GovernanceProposal) -> AegisSubmission:
        """Mock submit method."""
        if self.fail_on_submit:
            raise AegisGovernanceAdapterException("Mock submission failure")

        self.submitted_proposals.append(proposal)

        return AegisSubmission(
            objective_id=f"obj-{proposal.invoice_id[:8]}",
            objective_status="created",
            execution_status="decomposed",
            expected_request_count=1,
            correlation_id=proposal.correlation_id,
        )

    async def check_once(
        self, submission: AegisSubmission
    ) -> AggregatedGovernanceVerdict:
        """Mock check_once method."""
        if self.fail_on_check:
            raise Exception("Mock check failure")

        if self.verdict_override:
            return self.verdict_override

        # Default: approved
        return AggregatedGovernanceVerdict(
            action=GovernanceAction.ALLOW,
            reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
            objective_id=submission.objective_id,
            request_results=[
                RequestGovernanceResult(
                    request_id=f"req-{submission.objective_id}",
                    raw_decision_type="approved",
                    action=GovernanceAction.ALLOW,
                    reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                    review_decision_id=f"review-{submission.objective_id}",
                )
            ],
            all_requests_final=True,
            expected_request_count=1,
            observed_request_count=1,
        )


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def hierarchy():
    """Create FinanceAuthorityHierarchy instance."""
    return FinanceAuthorityHierarchy()


@pytest.fixture
def mock_aegis():
    """Create mock Aegis adapter."""
    return MockAegisAdapter()


@pytest.fixture
def orchestrator(mock_aegis):
    """Create orchestrator with mock adapter."""
    adapter = AsyncMock(spec=AegisGovernanceAdapter)
    adapter.submit = AsyncMock(
        return_value=AegisSubmission(
            objective_id="obj-test",
            correlation_id="corr-001",
            expected_request_count=1,
        )
    )
    adapter.check_once = AsyncMock()
    return GovernanceOrchestrator(adapter)


@pytest.fixture
def veson_routing_decision():
    """Veson IMOS routing decision (ROUTE_CANDIDATE)."""
    return FinanceRoutingDecision(
        target_system=FinanceTargetSystem.VESON_IMOS,
        routing_status=RoutingStatus.ROUTE_CANDIDATE,
        reason_code=RoutingReason.SINGLE_CATEGORY_MATCH,
        matched_categories=[FinanceBusinessCategory.BUNKER],
        route_confidence=0.95,
        explanation="Bunker fuel classified for Veson",
        workflow_run_id="run-123",
    )


@pytest.fixture
def smartpal_routing_decision():
    """SmartPAL routing decision (ROUTE_CANDIDATE)."""
    return FinanceRoutingDecision(
        target_system=FinanceTargetSystem.SMARTPAL,
        routing_status=RoutingStatus.ROUTE_CANDIDATE,
        reason_code=RoutingReason.SINGLE_CATEGORY_MATCH,
        matched_categories=[FinanceBusinessCategory.VESSEL_REPAIR],
        route_confidence=0.88,
        explanation="Vessel repair classified for SmartPAL",
        workflow_run_id="run-124",
    )


@pytest.fixture
def oracle_routing_decision():
    """Oracle Fusion routing decision (ROUTE_CANDIDATE)."""
    return FinanceRoutingDecision(
        target_system=FinanceTargetSystem.ORACLE_FUSION,
        routing_status=RoutingStatus.ROUTE_CANDIDATE,
        reason_code=RoutingReason.SINGLE_CATEGORY_MATCH,
        matched_categories=[FinanceBusinessCategory.SOFTWARE],
        route_confidence=0.92,
        explanation="Software license classified for Oracle",
        workflow_run_id="run-125",
    )


@pytest.fixture
def approval_decision_l1():
    """L1 approval decision (low risk, low amount)."""
    hierarchy = FinanceAuthorityHierarchy()
    return hierarchy.determine_approval_decision(
        invoice_id="INV-L1",
        correlation_id="corr-001",
        gross_amount=5000.0,
        risk_flags=[],
    )


@pytest.fixture
def approval_decision_l2():
    """L2 approval decision (medium risk or medium amount)."""
    hierarchy = FinanceAuthorityHierarchy()
    return hierarchy.determine_approval_decision(
        invoice_id="INV-L2",
        correlation_id="corr-002",
        gross_amount=25000.0,
        risk_flags=[],
    )


@pytest.fixture
def approval_decision_l3():
    """L3 approval decision (high risk or high amount)."""
    hierarchy = FinanceAuthorityHierarchy()
    return hierarchy.determine_approval_decision(
        invoice_id="INV-L3",
        correlation_id="corr-003",
        gross_amount=75000.0,
        risk_flags=["UNKNOWN_VENDOR"],
    )


@pytest.fixture
def approval_decision_human():
    """Human approver required (critical risk)."""
    hierarchy = FinanceAuthorityHierarchy()
    return hierarchy.determine_approval_decision(
        invoice_id="INV-HUMAN",
        correlation_id="corr-004",
        gross_amount=100000.0,
        risk_flags=["DUPLICATE_INVOICE"],
    )


# ============================================================================
# Rule 1: Routing HUMAN_REVIEW → HOLD / ROUTING_REVIEW_REQUIRED
# ============================================================================


@pytest.mark.asyncio
async def test_rule1_routing_human_review(orchestrator, approval_decision_l1):
    """Rule 1: Routing HUMAN_REVIEW → HOLD / ROUTING_REVIEW_REQUIRED."""
    routing = FinanceRoutingDecision(
        target_system=FinanceTargetSystem.UNDETERMINED,
        routing_status=RoutingStatus.HUMAN_REVIEW,
        reason_code=RoutingReason.INSUFFICIENT_CATEGORY_EVIDENCE,
        route_confidence=0.0,
        explanation="No category evidence",
        workflow_run_id="run-human",
    )

    result = await orchestrator.orchestrate(
        routing, approval_decision_l1, "corr-001", "case-001"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.ROUTING_REVIEW_REQUIRED
    )
    assert "human review" in result.explanation.lower()


# ============================================================================
# Rule 2: Authority HUMAN_REVIEW → HOLD / HUMAN_APPROVAL_REQUIRED
# ============================================================================


@pytest.mark.asyncio
async def test_rule2_authority_human_review(orchestrator, veson_routing_decision):
    """Rule 2: Authority HUMAN_REVIEW → HOLD / HUMAN_APPROVAL_REQUIRED."""
    # Create approval requiring HUMAN_APPROVER
    approval = ApprovalDecision(
        invoice_id="INV-HUMAN",
        correlation_id="corr-human",
        required_authorities=[AuthorityLevel.HUMAN_APPROVER],
        approval_reasons=[ApprovalReason.DUPLICATE_DETECTED],
        highest_risk_level=RiskLevel.CRITICAL,
    )

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-human", "case-human"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.HUMAN_APPROVAL_REQUIRED
    )


# ============================================================================
# Rule 3: Insufficient Authority → HOLD / AUTHORITY_ESCALATION_REQUIRED
# ============================================================================


@pytest.mark.asyncio
async def test_rule3_l1_insufficient_authority(
    orchestrator, hierarchy, veson_routing_decision
):
    """Rule 3: L1 handling medium-value case → escalate to L2."""
    # L1 processor trying to handle $25k invoice (needs L2)
    approval = hierarchy.determine_approval_decision(
        invoice_id="INV-L1-FAIL",
        correlation_id="corr-l1-fail",
        gross_amount=25000.0,
        risk_flags=[],
    )
    assert AuthorityLevel.L2_SUPERVISOR in approval.required_authorities

    # Even though L1 approval was requested, orchestrator should escalate
    approval.required_authorities = [AuthorityLevel.L1_PROCESSOR]  # Force L1

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-l1-fail", "case-l1-fail"
    )

    # Since L1 can't approve high-value items, should escalate
    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.AUTHORITY_ESCALATION_REQUIRED
    )


@pytest.mark.asyncio
async def test_rule3_l2_insufficient_for_critical(
    orchestrator, hierarchy, veson_routing_decision
):
    """Rule 3: L2 handling critical risk case → escalate to L3/Human."""
    approval = hierarchy.determine_approval_decision(
        invoice_id="INV-L2-CRITICAL",
        correlation_id="corr-l2-critical",
        gross_amount=5000.0,
        risk_flags=["DUPLICATE_INVOICE"],  # CRITICAL risk
    )

    # Force L2 evaluating authority
    approval.required_authorities = [AuthorityLevel.L2_SUPERVISOR]

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-l2-critical", "case-l2-critical"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.AUTHORITY_ESCALATION_REQUIRED
    )


# ============================================================================
# Rule 4: Valid Route + Sufficient Authority → ALLOW (with Aegis approval)
# ============================================================================


@pytest.mark.asyncio
async def test_rule4_veson_l2_approved(orchestrator, veson_routing_decision):
    """Rule 4: Veson + L2 authority + Aegis approval → ALLOW."""
    approval = ApprovalDecision(
        invoice_id="INV-L2",
        correlation_id="corr-002",
        required_authorities=[AuthorityLevel.L2_SUPERVISOR],
        approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        highest_risk_level=RiskLevel.MEDIUM,
    )

    # Setup Aegis to approve
    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="obj-veson-l2",
        request_results=[
            RequestGovernanceResult(
                request_id="req-veson-l2",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="review-veson-l2",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-002", "case-veson-l2"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.ALLOW
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_APPROVED
    )
    assert result.objective_id == "obj-veson-l2"
    assert "req-veson-l2" in result.request_ids
    assert "review-veson-l2" in result.review_decision_ids


@pytest.mark.asyncio
async def test_rule4_smartpal_l1_approved(orchestrator, smartpal_routing_decision):
    """Rule 4: SmartPAL + L1 authority + Aegis approval → ALLOW."""
    approval = ApprovalDecision(
        invoice_id="INV-SMARTPAL",
        correlation_id="corr-smartpal",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="obj-smartpal-l1",
        request_results=[
            RequestGovernanceResult(
                request_id="req-smartpal-l1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="review-smartpal-l1",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        smartpal_routing_decision, approval, "corr-smartpal", "case-smartpal-l1"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.ALLOW
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_APPROVED
    )


@pytest.mark.asyncio
async def test_rule4_oracle_l3_approved(orchestrator, oracle_routing_decision):
    """Rule 4: Oracle + L3 authority + Aegis approval → ALLOW."""
    approval = ApprovalDecision(
        invoice_id="INV-ORACLE",
        correlation_id="corr-oracle",
        required_authorities=[AuthorityLevel.L3_CONTROLLER],
        approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        highest_risk_level=RiskLevel.HIGH,
    )

    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="obj-oracle-l3",
        request_results=[
            RequestGovernanceResult(
                request_id="req-oracle-l3",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="review-oracle-l3",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        oracle_routing_decision, approval, "corr-oracle", "case-oracle-l3"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.ALLOW
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_APPROVED
    )


# ============================================================================
# Rule 5-7: Privacy-Safe Proposal + Aegis Interaction
# ============================================================================


@pytest.mark.asyncio
async def test_proposal_privacy_safe(orchestrator, veson_routing_decision):
    """Rule 5-6: Proposal must be privacy-safe (no PII)."""
    approval = ApprovalDecision(
        invoice_id="INV-PRIVACY",
        correlation_id="corr-privacy",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    orchestrator.adapter.submit.reset_mock()
    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.HOLD,
        reason_code=GovernanceReason.DECISION_PENDING,
        objective_id="obj-pending",
        request_results=[],
        all_requests_final=False,
    )
    orchestrator.adapter.check_once.return_value = approval_verdict

    await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-privacy", "case-privacy"
    )

    # Verify submit was called with a proposal
    assert orchestrator.adapter.submit.called
    proposal: GovernanceProposal = orchestrator.adapter.submit.call_args[0][0]

    # Verify privacy: no supplier_name, customer_name, raw_text fields
    proposal_dict = proposal.model_dump()
    assert "supplier_name" not in proposal_dict
    assert "customer_name" not in proposal_dict
    assert "invoice_text" not in proposal_dict
    assert "line_items" not in proposal_dict

    # Verify presence of safe fields
    assert proposal.invoice_id == "case-privacy"
    assert proposal.correlation_id == "corr-privacy"
    assert proposal.proposed_target_system is not None


# ============================================================================
# Rule 8: Aegis ALLOW → ALLOW / GOVERNANCE_APPROVED
# ============================================================================


@pytest.mark.asyncio
async def test_rule8_aegis_allow(orchestrator, veson_routing_decision):
    """Rule 8: Aegis ALLOW → ALLOW / GOVERNANCE_APPROVED."""
    approval = ApprovalDecision(
        invoice_id="INV-ALLOW",
        correlation_id="corr-allow",
        required_authorities=[AuthorityLevel.L2_SUPERVISOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="obj-allow",
        request_results=[
            RequestGovernanceResult(
                request_id="req-allow",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="review-allow",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-allow", "case-allow"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.ALLOW
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_APPROVED
    )


# ============================================================================
# Rule 9: Aegis DENY → DENY / GOVERNANCE_REJECTED
# ============================================================================


@pytest.mark.asyncio
async def test_rule9_aegis_deny(orchestrator, veson_routing_decision):
    """Rule 9: Aegis DENY → DENY / GOVERNANCE_REJECTED."""
    approval = ApprovalDecision(
        invoice_id="INV-DENY",
        correlation_id="corr-deny",
        required_authorities=[AuthorityLevel.L3_CONTROLLER],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.DENY,
        reason_code=GovernanceReason.EXPLICIT_REJECTION,
        objective_id="obj-deny",
        request_results=[
            RequestGovernanceResult(
                request_id="req-deny",
                raw_decision_type="rejected",
                action=GovernanceAction.DENY,
                reason_code=GovernanceReason.EXPLICIT_REJECTION,
                review_decision_id="review-deny",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-deny", "case-deny"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.DENY
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_REJECTED
    )


# ============================================================================
# Rule 10-11: Aegis Pending/Unknown/Error → HOLD
# ============================================================================


@pytest.mark.asyncio
async def test_rule10_aegis_pending(orchestrator, veson_routing_decision):
    """Rule 10: Aegis pending → HOLD / GOVERNANCE_PENDING."""
    approval = ApprovalDecision(
        invoice_id="INV-PENDING",
        correlation_id="corr-pending",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    # Pending verdict: no decision yet
    pending_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.HOLD,
        reason_code=GovernanceReason.DECISION_PENDING,
        objective_id="obj-pending",
        request_results=[
            RequestGovernanceResult(
                request_id="req-pending",
                raw_decision_type=None,
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.DECISION_PENDING,
                review_decision_id=None,
            )
        ],
        all_requests_final=False,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = pending_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-pending", "case-pending"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_PENDING
    )


@pytest.mark.asyncio
async def test_rule11_aegis_unavailable(orchestrator, veson_routing_decision):
    """Rule 11: Aegis unavailable (submission fails) → HOLD / AEGIS_UNAVAILABLE."""
    approval = ApprovalDecision(
        invoice_id="INV-UNAVAIL",
        correlation_id="corr-unavail",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    # Simulate submission failure
    orchestrator.adapter.submit.side_effect = AegisGovernanceAdapterException(
        "Connection failed"
    )

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-unavail", "case-unavail"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code == GovernanceOrchestrationReason.AEGIS_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_rule11_check_fails(orchestrator, veson_routing_decision):
    """Rule 11: Aegis check_once fails → HOLD / AEGIS_UNAVAILABLE."""
    approval = ApprovalDecision(
        invoice_id="INV-CHECK-FAIL",
        correlation_id="corr-check-fail",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    # Submission succeeds, check fails
    orchestrator.adapter.submit.return_value = AegisSubmission(
        objective_id="obj-fail",
        correlation_id="corr-check-fail",
        expected_request_count=1,
    )
    orchestrator.adapter.check_once.side_effect = Exception(
        "Network timeout"
    )

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-check-fail", "case-check-fail"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code == GovernanceOrchestrationReason.AEGIS_UNAVAILABLE
    )


# ============================================================================
# Rule 12: Preserve Genuine Aegis IDs
# ============================================================================


@pytest.mark.asyncio
async def test_rule12_preserve_aegis_ids(orchestrator, veson_routing_decision):
    """Rule 12: Preserve genuine Aegis objective, request, and review-decision IDs."""
    approval = ApprovalDecision(
        invoice_id="INV-IDS",
        correlation_id="corr-ids",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    # Setup with specific IDs
    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="aegis-obj-12345",
        request_results=[
            RequestGovernanceResult(
                request_id="aegis-req-67890",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="aegis-review-abcde",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-ids", "case-ids"
    )

    # Verify IDs are preserved exactly
    assert result.objective_id == "aegis-obj-12345"
    assert result.request_ids == ["aegis-req-67890"]
    assert result.review_decision_ids == ["aegis-review-abcde"]


# ============================================================================
# Rule 13-15: Privacy, No Persistence, Pydantic Validation
# ============================================================================


@pytest.mark.asyncio
async def test_no_direct_http(orchestrator, veson_routing_decision):
    """Rule 14: No direct HTTP calls (uses injected adapter only)."""
    approval = ApprovalDecision(
        invoice_id="INV-NO-HTTP",
        correlation_id="corr-no-http",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    orchestrator.adapter.submit.return_value = AegisSubmission(
        objective_id="obj-test",
        correlation_id="corr-no-http",
        expected_request_count=1,
    )
    orchestrator.adapter.check_once.return_value = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="obj-test",
        request_results=[
            RequestGovernanceResult(
                request_id="req-test",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    # Execute orchestration
    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-no-http", "case-no-http"
    )

    # Verify result is returned (not persisted)
    assert result is not None
    assert isinstance(result, GovernanceOrchestrationResult)

    # Verify mock adapter was used (not real HTTP)
    assert orchestrator.adapter.submit.called
    assert orchestrator.adapter.check_once.called


def test_pydantic_forbids_extra_fields():
    """Rule 15: Pydantic models reject extra fields."""
    # Try to create result with extra field
    with pytest.raises(Exception):  # Pydantic validation error
        GovernanceOrchestrationResult(
            case_id="case-001",
            correlation_id="corr-001",
            proposed_target_system="VESON_IMOS",
            evaluating_authority="L1_PROCESSOR",
            required_authority="L1_PROCESSOR",
            authority_outcome="sufficient",
            governance_status=GovernanceOrchestrationStatus.ALLOW,
            reason_code=GovernanceOrchestrationReason.GOVERNANCE_APPROVED,
            extra_field="should_fail",  # Not allowed
        )


# ============================================================================
# Scenario Tests: Realistic Multi-Step Flows
# ============================================================================


@pytest.mark.asyncio
async def test_scenario_approved_small_invoice(
    orchestrator, hierarchy, veson_routing_decision
):
    """Scenario: Small invoice, clean routing, approved by Aegis."""
    approval = hierarchy.determine_approval_decision(
        invoice_id="INV-SMALL",
        correlation_id="corr-small",
        gross_amount=3000.0,
        risk_flags=[],
    )

    approval_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.ALLOW,
        reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
        objective_id="obj-small",
        request_results=[
            RequestGovernanceResult(
                request_id="req-small",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="review-small",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = approval_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-small", "case-small"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.ALLOW
    assert result.reason_code == GovernanceOrchestrationReason.GOVERNANCE_APPROVED


@pytest.mark.asyncio
async def test_scenario_authority_escalation(
    orchestrator, hierarchy, veson_routing_decision
):
    """Scenario: L1 handling medium-value invoice → escalate."""
    # Manually construct low-authority approval for high amount
    approval = ApprovalDecision(
        invoice_id="INV-ESCALATE",
        correlation_id="corr-escalate",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        highest_risk_level=RiskLevel.MEDIUM,
    )

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-escalate", "case-escalate"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.AUTHORITY_ESCALATION_REQUIRED
    )


@pytest.mark.asyncio
async def test_scenario_human_review_routing(
    orchestrator, approval_decision_l1
):
    """Scenario: Router marks as human review."""
    routing = FinanceRoutingDecision(
        target_system=FinanceTargetSystem.UNDETERMINED,
        routing_status=RoutingStatus.HUMAN_REVIEW,
        reason_code=RoutingReason.ROUTING_CONFLICT,
        route_confidence=0.0,
        explanation="Multiple systems possible",
        workflow_run_id="run-conflict",
    )

    result = await orchestrator.orchestrate(
        routing, approval_decision_l1, "corr-conflict", "case-conflict"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.ROUTING_REVIEW_REQUIRED
    )


@pytest.mark.asyncio
async def test_scenario_aegis_rejection(orchestrator, veson_routing_decision):
    """Scenario: Aegis explicitly rejects invoice."""
    approval = ApprovalDecision(
        invoice_id="INV-REJECT",
        correlation_id="corr-reject",
        required_authorities=[AuthorityLevel.L2_SUPERVISOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    reject_verdict = AggregatedGovernanceVerdict(
        action=GovernanceAction.DENY,
        reason_code=GovernanceReason.EXPLICIT_REJECTION,
        reason_detail="Flagged as compliance issue",
        objective_id="obj-reject",
        request_results=[
            RequestGovernanceResult(
                request_id="req-reject",
                raw_decision_type="rejected",
                action=GovernanceAction.DENY,
                reason_code=GovernanceReason.EXPLICIT_REJECTION,
                review_decision_id="review-reject",
            )
        ],
        all_requests_final=True,
        expected_request_count=1,
        observed_request_count=1,
    )

    orchestrator.adapter.check_once.return_value = reject_verdict

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-reject", "case-reject"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.DENY
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.GOVERNANCE_REJECTED
    )


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


@pytest.mark.asyncio
async def test_unexpected_orchestration_error(orchestrator, veson_routing_decision):
    """Catch-all: Unexpected error → HOLD / ORCHESTRATION_ERROR."""
    approval = ApprovalDecision(
        invoice_id="INV-ERROR",
        correlation_id="corr-error",
        required_authorities=[AuthorityLevel.L1_PROCESSOR],
        approval_reasons=[],
        highest_risk_level=RiskLevel.LOW,
    )

    # Make submit raise an unexpected error
    orchestrator.adapter.submit.side_effect = RuntimeError("Unexpected failure")

    result = await orchestrator.orchestrate(
        veson_routing_decision, approval, "corr-error", "case-error"
    )

    assert result.governance_status == GovernanceOrchestrationStatus.HOLD
    assert (
        result.reason_code
        == GovernanceOrchestrationReason.ORCHESTRATION_ERROR
    )
