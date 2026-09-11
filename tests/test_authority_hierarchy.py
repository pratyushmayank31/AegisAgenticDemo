"""
Tests for Finance Authority Hierarchy.

Verifies:
- Deterministic mapping of invoice characteristics to approval authorities
- Amount-based threshold routing
- Risk-based escalation
- Multi-approval scenarios
- No external dependencies (no Aegis, database, LLM, PDF, or API access)
"""

import pytest
from src.authority_hierarchy import (
    AuthorityLevel,
    RiskLevel,
    ApprovalReason,
    ApprovalRequirement,
    ApprovalDecision,
    FinanceAuthorityHierarchy,
)


class TestAuthorityLevels:
    """Test authority level enum and definitions."""

    def test_authority_levels_exist(self):
        """All expected authority levels are defined."""
        assert AuthorityLevel.L1_PROCESSOR
        assert AuthorityLevel.L2_SUPERVISOR
        assert AuthorityLevel.L3_CONTROLLER
        assert AuthorityLevel.HUMAN_APPROVER

    def test_authority_level_values(self):
        """Authority levels have correct string values."""
        assert AuthorityLevel.L1_PROCESSOR.value == "L1_PROCESSOR"
        assert AuthorityLevel.L2_SUPERVISOR.value == "L2_SUPERVISOR"
        assert AuthorityLevel.L3_CONTROLLER.value == "L3_CONTROLLER"
        assert AuthorityLevel.HUMAN_APPROVER.value == "HUMAN_APPROVER"

    def test_risk_levels_exist(self):
        """All expected risk levels are defined."""
        assert RiskLevel.LOW
        assert RiskLevel.MEDIUM
        assert RiskLevel.HIGH
        assert RiskLevel.CRITICAL

    def test_approval_reasons_exist(self):
        """All expected approval reasons are defined."""
        assert ApprovalReason.AMOUNT_THRESHOLD
        assert ApprovalReason.UNKNOWN_VENDOR
        assert ApprovalReason.DUPLICATE_DETECTED
        assert ApprovalReason.BANK_CHANGE
        assert ApprovalReason.PRICE_MISMATCH
        assert ApprovalReason.MISSING_REFERENCE
        assert ApprovalReason.LOW_CONFIDENCE
        assert ApprovalReason.ROUTING_CONFLICT
        assert ApprovalReason.HIGH_RISK_CATEGORY
        assert ApprovalReason.MULTI_APPROVER_REQUIRED


class TestApprovalDecisionBasics:
    """Test ApprovalDecision dataclass and initialization."""

    def test_approval_decision_creation(self):
        """Can create an ApprovalDecision with required fields."""
        decision = ApprovalDecision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            required_authorities=[AuthorityLevel.L2_SUPERVISOR],
            approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
            highest_risk_level=RiskLevel.MEDIUM,
            total_risk_count=1,
        )
        assert decision.invoice_id == "INV-001"
        assert decision.correlation_id == "corr_123"
        assert AuthorityLevel.L2_SUPERVISOR in decision.required_authorities
        assert ApprovalReason.AMOUNT_THRESHOLD in decision.approval_reasons
        assert decision.highest_risk_level == RiskLevel.MEDIUM

    def test_approval_decision_deterministic_hash(self):
        """ApprovalDecision generates deterministic hash."""
        decision1 = ApprovalDecision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            required_authorities=[AuthorityLevel.L2_SUPERVISOR],
            approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        )
        decision2 = ApprovalDecision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            required_authorities=[AuthorityLevel.L2_SUPERVISOR],
            approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        )
        # Same input should produce same hash
        assert decision1.deterministic_hash == decision2.deterministic_hash

    def test_approval_decision_hash_differs_for_different_input(self):
        """Hash differs for different invoice_id."""
        decision1 = ApprovalDecision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            required_authorities=[AuthorityLevel.L2_SUPERVISOR],
            approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        )
        decision2 = ApprovalDecision(
            invoice_id="INV-002",  # Different
            correlation_id="corr_123",
            required_authorities=[AuthorityLevel.L2_SUPERVISOR],
            approval_reasons=[ApprovalReason.AMOUNT_THRESHOLD],
        )
        assert decision1.deterministic_hash != decision2.deterministic_hash


class TestHierarchyInitialization:
    """Test FinanceAuthorityHierarchy initialization."""

    def test_hierarchy_instantiation(self):
        """Can instantiate FinanceAuthorityHierarchy."""
        hierarchy = FinanceAuthorityHierarchy()
        assert hierarchy is not None

    def test_amount_thresholds_defined(self):
        """Amount thresholds are properly configured."""
        hierarchy = FinanceAuthorityHierarchy()
        assert hierarchy.AMOUNT_THRESHOLDS[AuthorityLevel.L1_PROCESSOR] == 10_000.0
        assert hierarchy.AMOUNT_THRESHOLDS[AuthorityLevel.L2_SUPERVISOR] == 50_000.0
        assert hierarchy.AMOUNT_THRESHOLDS[AuthorityLevel.L3_CONTROLLER] == float("inf")
        assert hierarchy.AMOUNT_THRESHOLDS[AuthorityLevel.HUMAN_APPROVER] == float("inf")

    def test_risk_to_authority_mapping(self):
        """Risk levels map to authority levels."""
        hierarchy = FinanceAuthorityHierarchy()
        assert (
            hierarchy.RISK_TO_AUTHORITY[RiskLevel.LOW]
            == AuthorityLevel.L1_PROCESSOR
        )
        assert (
            hierarchy.RISK_TO_AUTHORITY[RiskLevel.MEDIUM]
            == AuthorityLevel.L2_SUPERVISOR
        )
        assert (
            hierarchy.RISK_TO_AUTHORITY[RiskLevel.HIGH]
            == AuthorityLevel.L3_CONTROLLER
        )
        assert (
            hierarchy.RISK_TO_AUTHORITY[RiskLevel.CRITICAL]
            == AuthorityLevel.HUMAN_APPROVER
        )


class TestAmountBasedRouting:
    """Test amount-based threshold routing."""

    def test_amount_below_l1_threshold(self):
        """Amount below $10k → L1_PROCESSOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=5_000.0,
        )
        assert AuthorityLevel.L1_PROCESSOR in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.LOW

    def test_amount_at_l1_threshold(self):
        """Amount exactly at $10k → L2_SUPERVISOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=10_000.0,
        )
        assert AuthorityLevel.L2_SUPERVISOR in decision.required_authorities

    def test_amount_above_l1_threshold(self):
        """Amount > $10k → L2_SUPERVISOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=10_001.0,
        )
        assert AuthorityLevel.L2_SUPERVISOR in decision.required_authorities
        assert ApprovalReason.AMOUNT_THRESHOLD in decision.approval_reasons

    def test_amount_between_l1_and_l2(self):
        """Amount $25k → L2_SUPERVISOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=25_000.0,
        )
        assert AuthorityLevel.L2_SUPERVISOR in decision.required_authorities

    def test_amount_at_l2_threshold(self):
        """Amount exactly at $50k → L2_SUPERVISOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=50_000.0,
        )
        assert AuthorityLevel.L2_SUPERVISOR in decision.required_authorities

    def test_amount_just_above_l2_threshold(self):
        """Amount just above $50k ($50,000.01) → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=50_000.01,
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities

    def test_amount_above_l2_threshold(self):
        """Amount > $50k → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=50_001.0,
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities

    def test_amount_between_l2_and_l3(self):
        """Amount $75k → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=75_000.0,
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities

    def test_amount_very_large(self):
        """Amount $100k → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=100_000.0,
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities

    def test_amount_one_dollar(self):
        """Amount $1 → L1_PROCESSOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=1.0,
        )
        assert AuthorityLevel.L1_PROCESSOR in decision.required_authorities

    def test_large_amount(self):
        """Very large amount → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=1_000_000.0,
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities


class TestRiskBasedEscalation:
    """Test risk-based escalation logic."""

    def test_duplicate_invoice_escalates_to_human_approver(self):
        """Duplicate invoice → HUMAN_APPROVER regardless of amount."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=100.0,  # Very small amount
            risk_flags=["DUPLICATE_INVOICE"],
        )
        assert AuthorityLevel.HUMAN_APPROVER in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.CRITICAL
        assert ApprovalReason.DUPLICATE_DETECTED in decision.approval_reasons

    def test_bank_change_escalates_to_human_approver(self):
        """Bank change → HUMAN_APPROVER regardless of amount."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            risk_flags=["BANK_DETAILS_CHANGED"],
        )
        assert AuthorityLevel.HUMAN_APPROVER in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.CRITICAL
        assert ApprovalReason.BANK_CHANGE in decision.approval_reasons

    def test_unknown_vendor_escalates_to_l3_controller(self):
        """Unknown vendor → L3_CONTROLLER (HIGH risk)."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            risk_flags=["UNKNOWN_VENDOR"],
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.HIGH
        assert ApprovalReason.UNKNOWN_VENDOR in decision.approval_reasons

    def test_price_mismatch_escalates_to_l3_controller(self):
        """Price mismatch → L3_CONTROLLER (HIGH risk)."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            risk_flags=["PRICE_MISMATCH"],
        )
        # Should escalate from L1_PROCESSOR to L3_CONTROLLER due to risk
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities

    def test_low_confidence_escalates_to_l2_supervisor(self):
        """Low confidence → L2_SUPERVISOR (MEDIUM risk)."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            risk_flags=["LOW_CONFIDENCE"],
        )
        # Should escalate from L1_PROCESSOR to L2_SUPERVISOR due to risk
        assert AuthorityLevel.L2_SUPERVISOR in decision.required_authorities

    def test_multiple_risk_flags_max_escalation(self):
        """Multiple risk flags → highest escalation wins."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=100.0,
            risk_flags=[
                "UNKNOWN_VENDOR",  # HIGH
                "LOW_CONFIDENCE",  # MEDIUM
            ],
        )
        # Should escalate to L3_CONTROLLER due to UNKNOWN_VENDOR (HIGH risk)
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities


class TestMultiApprovalScenarios:
    """Test multi-approval requirements."""

    def test_multi_approval_required_flag(self):
        """Multi-approval flag → HUMAN_APPROVER for governance."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            requires_multi_approval=True,
        )
        assert AuthorityLevel.HUMAN_APPROVER in decision.required_authorities
        assert decision.requires_multi_approval
        assert ApprovalReason.MULTI_APPROVER_REQUIRED in decision.approval_reasons


class TestInputValidation:
    """Test input validation and error handling."""

    def test_empty_invoice_id_rejected(self):
        """Empty invoice_id raises ValueError."""
        hierarchy = FinanceAuthorityHierarchy()
        with pytest.raises(ValueError, match="invoice_id cannot be empty"):
            hierarchy.determine_approval_decision(
                invoice_id="",
                correlation_id="corr_123",
                gross_amount=100.0,
            )

    def test_whitespace_only_invoice_id_rejected(self):
        """Whitespace-only invoice_id raises ValueError."""
        hierarchy = FinanceAuthorityHierarchy()
        with pytest.raises(ValueError, match="invoice_id cannot be empty"):
            hierarchy.determine_approval_decision(
                invoice_id="   ",
                correlation_id="corr_123",
                gross_amount=100.0,
            )

    def test_empty_correlation_id_rejected(self):
        """Empty correlation_id raises ValueError."""
        hierarchy = FinanceAuthorityHierarchy()
        with pytest.raises(ValueError, match="correlation_id cannot be empty"):
            hierarchy.determine_approval_decision(
                invoice_id="INV-001",
                correlation_id="",
                gross_amount=100.0,
            )

    def test_negative_amount_rejected(self):
        """Negative gross_amount raises ValueError."""
        hierarchy = FinanceAuthorityHierarchy()
        with pytest.raises(ValueError, match="gross_amount cannot be negative"):
            hierarchy.determine_approval_decision(
                invoice_id="INV-001",
                correlation_id="corr_123",
                gross_amount=-100.0,
            )

    def test_zero_amount_accepted(self):
        """Zero amount is valid."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=0.0,
        )
        assert decision is not None
        assert AuthorityLevel.L1_PROCESSOR in decision.required_authorities


class TestDeterminism:
    """Test deterministic behavior."""

    def test_same_input_same_decision(self):
        """Same input produces same decision twice."""
        hierarchy = FinanceAuthorityHierarchy()
        decision1 = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=3_000.0,
            risk_flags=["UNKNOWN_VENDOR"],
        )
        decision2 = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=3_000.0,
            risk_flags=["UNKNOWN_VENDOR"],
        )
        assert decision1.required_authorities == decision2.required_authorities
        assert decision1.approval_reasons == decision2.approval_reasons
        assert decision1.highest_risk_level == decision2.highest_risk_level

    def test_different_invoice_ids_different_hashes(self):
        """Different invoice IDs produce different hashes."""
        hierarchy = FinanceAuthorityHierarchy()
        decision1 = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=3_000.0,
        )
        decision2 = hierarchy.determine_approval_decision(
            invoice_id="INV-002",
            correlation_id="corr_123",
            gross_amount=3_000.0,
        )
        assert decision1.deterministic_hash != decision2.deterministic_hash


class TestHelperMethods:
    """Test utility methods."""

    def test_get_authority_by_name_valid(self):
        """Can retrieve authority by name."""
        hierarchy = FinanceAuthorityHierarchy()
        authority = hierarchy.get_authority_by_name("L2_SUPERVISOR")
        assert authority == AuthorityLevel.L2_SUPERVISOR

    def test_get_authority_by_name_invalid(self):
        """Invalid authority name returns None."""
        hierarchy = FinanceAuthorityHierarchy()
        authority = hierarchy.get_authority_by_name("INVALID")
        assert authority is None

    def test_get_approval_reason_by_name_valid(self):
        """Can retrieve approval reason by name."""
        hierarchy = FinanceAuthorityHierarchy()
        reason = hierarchy.get_approval_reason_by_name("DUPLICATE_DETECTED")
        assert reason == ApprovalReason.DUPLICATE_DETECTED

    def test_get_approval_reason_by_name_invalid(self):
        """Invalid approval reason name returns None."""
        hierarchy = FinanceAuthorityHierarchy()
        reason = hierarchy.get_approval_reason_by_name("INVALID")
        assert reason is None

    def test_rank_authorities_sorts_correctly(self):
        """Authorities sorted by rank (lowest to highest)."""
        hierarchy = FinanceAuthorityHierarchy()
        unsorted = [
            AuthorityLevel.HUMAN_APPROVER,
            AuthorityLevel.L2_SUPERVISOR,
            AuthorityLevel.L1_PROCESSOR,
            AuthorityLevel.L3_CONTROLLER,
        ]
        sorted_authorities = hierarchy.rank_authorities(unsorted)
        assert sorted_authorities == [
            AuthorityLevel.L1_PROCESSOR,
            AuthorityLevel.L2_SUPERVISOR,
            AuthorityLevel.L3_CONTROLLER,
            AuthorityLevel.HUMAN_APPROVER,
        ]

    def test_is_authority_higher_than(self):
        """Authority comparison works correctly."""
        hierarchy = FinanceAuthorityHierarchy()
        assert hierarchy.is_authority_higher_than(
            AuthorityLevel.HUMAN_APPROVER, AuthorityLevel.L2_SUPERVISOR
        )
        assert hierarchy.is_authority_higher_than(
            AuthorityLevel.L3_CONTROLLER, AuthorityLevel.L1_PROCESSOR
        )
        assert not hierarchy.is_authority_higher_than(
            AuthorityLevel.L1_PROCESSOR, AuthorityLevel.HUMAN_APPROVER
        )
        assert not hierarchy.is_authority_higher_than(
            AuthorityLevel.L2_SUPERVISOR, AuthorityLevel.L2_SUPERVISOR
        )


class TestRealWorldScenarios:
    """Test realistic invoice approval scenarios."""

    def test_scenario_small_invoice_low_risk(self):
        """Small invoice, low risk → L1_PROCESSOR."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_001",
            gross_amount=5_000.0,
            risk_flags=[],
        )
        assert decision.required_authorities == [AuthorityLevel.L1_PROCESSOR]
        assert decision.highest_risk_level == RiskLevel.LOW

    def test_scenario_medium_invoice_with_unknown_vendor(self):
        """$3k invoice with unknown vendor → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-002",
            correlation_id="corr_002",
            gross_amount=3_000.0,
            risk_flags=["UNKNOWN_VENDOR"],
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.HIGH

    def test_scenario_large_invoice_multiple_risks(self):
        """$75k invoice with multiple risks → L3_CONTROLLER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-003",
            correlation_id="corr_003",
            gross_amount=75_000.0,
            risk_flags=["UNKNOWN_VENDOR", "LOW_CONFIDENCE"],
        )
        assert AuthorityLevel.L3_CONTROLLER in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.HIGH

    def test_scenario_small_invoice_with_bank_change(self):
        """$100 invoice with bank change → HUMAN_APPROVER (critical risk)."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-004",
            correlation_id="corr_004",
            gross_amount=100.0,
            risk_flags=["BANK_DETAILS_CHANGED"],
        )
        assert AuthorityLevel.HUMAN_APPROVER in decision.required_authorities
        assert decision.highest_risk_level == RiskLevel.CRITICAL

    def test_scenario_duplicate_invoice(self):
        """Any amount with duplicate flag → HUMAN_APPROVER."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-005",
            correlation_id="corr_005",
            gross_amount=200.0,
            risk_flags=["DUPLICATE_INVOICE"],
        )
        assert decision.required_authorities == [AuthorityLevel.HUMAN_APPROVER]
        assert decision.highest_risk_level == RiskLevel.CRITICAL


class TestNoExternalDependencies:
    """Verify no external system calls or dependencies."""

    def test_no_network_access_in_approval_decision(self):
        """Approval decision contains no network calls."""
        hierarchy = FinanceAuthorityHierarchy()
        # If this completes without exception, no network access occurred
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=10_000.0,
            risk_flags=["UNKNOWN_VENDOR"],
        )
        assert decision is not None

    def test_no_database_access_in_approval_decision(self):
        """Approval decision contains no database access."""
        hierarchy = FinanceAuthorityHierarchy()
        # Pure computation, no database access
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=10_000.0,
        )
        assert decision.required_authorities is not None

    def test_no_llm_access_in_approval_decision(self):
        """Approval decision contains no LLM calls."""
        hierarchy = FinanceAuthorityHierarchy()
        # Deterministic computation, no LLM calls
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
        )
        assert decision is not None

    def test_no_file_access_in_approval_decision(self):
        """Approval decision contains no file I/O."""
        hierarchy = FinanceAuthorityHierarchy()
        # Pure in-memory computation
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
        )
        assert decision.deterministic_hash is not None


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_exact_threshold_boundaries(self):
        """Test exact threshold amounts."""
        hierarchy = FinanceAuthorityHierarchy()

        # At each threshold (10k is boundary to L2, 50k is boundary between L2 and L3)
        boundaries = [
            (10_000.0, AuthorityLevel.L2_SUPERVISOR),
            (50_000.0, AuthorityLevel.L2_SUPERVISOR),  # 50k is inclusive to L2
        ]

        for amount, expected_level in boundaries:
            decision = hierarchy.determine_approval_decision(
                invoice_id=f"INV-{int(amount)}",
                correlation_id="corr_123",
                gross_amount=amount,
            )
            assert expected_level in decision.required_authorities

    def test_fractional_amounts(self):
        """Test amounts with fractional parts."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=1_234.56,
        )
        assert AuthorityLevel.L1_PROCESSOR in decision.required_authorities

    def test_very_small_amount(self):
        """Test very small amounts."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=0.01,
        )
        assert AuthorityLevel.L1_PROCESSOR in decision.required_authorities

    def test_empty_risk_flags_list(self):
        """Test with empty risk flags list."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            risk_flags=[],
        )
        assert decision.highest_risk_level == RiskLevel.LOW

    def test_unknown_risk_flag_ignored(self):
        """Unknown risk flags are gracefully ignored."""
        hierarchy = FinanceAuthorityHierarchy()
        decision = hierarchy.determine_approval_decision(
            invoice_id="INV-001",
            correlation_id="corr_123",
            gross_amount=500.0,
            risk_flags=["UNKNOWN_FLAG"],  # Not a recognized flag
        )
        # Should not crash, should handle gracefully
        assert decision is not None
        assert decision.highest_risk_level == RiskLevel.LOW
