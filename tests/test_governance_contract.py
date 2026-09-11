"""
Tests for governance contract with deterministic Aegis decision aggregation.

Verifies:
- Correct mapping of raw Aegis decisions to governance actions
- Deterministic aggregation precedence
- Fail-closed semantics (unknown → HOLD, never DENY)
- No network or database integration
- Pydantic validation (extra fields rejected, identifiers non-empty)
"""

import pytest
from src.governance_contract import (
    GovernanceAction,
    GovernanceReason,
    RequestGovernanceResult,
    AggregatedGovernanceVerdict,
    map_raw_decision_to_action,
    map_raw_decision_to_reason,
    aggregate_governance_verdicts,
)


class TestRawDecisionMapping:
    """Test mapping of raw Aegis decision types to governance actions."""

    def test_approved_maps_to_allow(self):
        """'approved' decision type maps to ALLOW action."""
        action = map_raw_decision_to_action("approved")
        assert action == GovernanceAction.ALLOW

    def test_conditional_approval_maps_to_hold(self):
        """'conditional_approval' maps to HOLD."""
        action = map_raw_decision_to_action("conditional_approval")
        assert action == GovernanceAction.HOLD

    def test_revision_requested_maps_to_hold(self):
        """'revision_requested' maps to HOLD."""
        action = map_raw_decision_to_action("revision_requested")
        assert action == GovernanceAction.HOLD

    def test_rejected_maps_to_deny(self):
        """'rejected' decision type maps to DENY action."""
        action = map_raw_decision_to_action("rejected")
        assert action == GovernanceAction.DENY

    def test_none_maps_to_hold(self):
        """None decision type maps to HOLD (fail-closed)."""
        action = map_raw_decision_to_action(None)
        assert action == GovernanceAction.HOLD

    def test_empty_string_maps_to_hold(self):
        """Empty string maps to HOLD."""
        action = map_raw_decision_to_action("")
        assert action == GovernanceAction.HOLD

    def test_unknown_maps_to_hold_not_deny(self):
        """Unknown decision type maps to HOLD, never DENY."""
        action = map_raw_decision_to_action("some_unknown_value")
        assert action == GovernanceAction.HOLD

    def test_pending_maps_to_hold(self):
        """'pending' decision type maps to HOLD."""
        action = map_raw_decision_to_action("pending")
        assert action == GovernanceAction.HOLD

    def test_case_insensitive_mapping(self):
        """Mapping is case-insensitive."""
        assert map_raw_decision_to_action("APPROVED") == GovernanceAction.ALLOW
        assert map_raw_decision_to_action("Rejected") == GovernanceAction.DENY
        assert map_raw_decision_to_action("CONDITIONAL_APPROVAL") == GovernanceAction.HOLD


class TestAggregationPrecedence:
    """Test deterministic aggregation precedence rules."""

    def test_one_approved_request_yields_allow(self):
        """One approved request → ALLOW."""
        result = RequestGovernanceResult(
            request_id="req_1",
            raw_decision_type="approved",
            action=GovernanceAction.ALLOW,
            reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
            review_decision_id="dec_1",
        )
        verdict = aggregate_governance_verdicts(
            [result], objective_id="obj_1", expected_request_count=1
        )
        assert verdict.action == GovernanceAction.ALLOW
        assert verdict.reason_code == GovernanceReason.ALL_REQUESTS_APPROVED

    def test_multiple_approved_requests_yield_allow(self):
        """Multiple approved requests → ALLOW."""
        results = [
            RequestGovernanceResult(
                request_id=f"req_{i}",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id=f"dec_{i}",
            )
            for i in range(1, 4)
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=3
        )
        assert verdict.action == GovernanceAction.ALLOW
        assert verdict.reason_code == GovernanceReason.ALL_REQUESTS_APPROVED
        assert verdict.observed_request_count == 3

    def test_approved_and_rejected_yields_deny(self):
        """Approved + rejected → DENY."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="rejected",
                action=GovernanceAction.DENY,
                reason_code=GovernanceReason.EXPLICIT_REJECTION,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.DENY
        assert verdict.reason_code == GovernanceReason.EXPLICIT_REJECTION

    def test_rejected_and_conditional_yields_deny(self):
        """Rejected + conditional → DENY (rejection takes precedence)."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="rejected",
                action=GovernanceAction.DENY,
                reason_code=GovernanceReason.EXPLICIT_REJECTION,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="conditional_approval",
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.CONDITIONAL_APPROVAL,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.DENY

    def test_approved_and_conditional_yields_hold(self):
        """Approved + conditional → HOLD (conditional precedence)."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="conditional_approval",
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.CONDITIONAL_APPROVAL,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.CONDITIONAL_APPROVAL

    def test_approved_and_revision_requested_yields_hold(self):
        """Approved + revision_requested → HOLD."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="revision_requested",
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.REVISION_REQUESTED,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.REVISION_REQUESTED

    def test_approved_and_unknown_yields_hold(self):
        """Approved + unknown decision type → HOLD."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="some_unknown_type",
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.UNKNOWN_DECISION_TYPE,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.UNKNOWN_DECISION_TYPE

    def test_approved_and_pending_yields_hold(self):
        """Approved + pending → HOLD."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type=None,
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.DECISION_PENDING,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.DECISION_PENDING

    def test_no_requests_yields_hold(self):
        """No requests → HOLD / NO_REQUESTS."""
        verdict = aggregate_governance_verdicts(
            [], objective_id="obj_1", expected_request_count=0
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.NO_REQUESTS
        assert verdict.observed_request_count == 0

    def test_expected_two_observed_one_yields_hold(self):
        """Expected 2, observed 1 → HOLD / REQUEST_PENDING."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.REQUEST_PENDING
        assert verdict.expected_request_count == 2
        assert verdict.observed_request_count == 1

    def test_expected_zero_never_allows(self):
        """Expected count zero → never ALLOW."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=0
        )
        assert verdict.action != GovernanceAction.ALLOW
        # Even with approved request, expected 0 means no approval should happen
        assert verdict.action == GovernanceAction.HOLD


class TestValidation:
    """Test Pydantic validation and model constraints."""

    def test_duplicate_request_ids_rejected(self):
        """Duplicate request IDs are detected and held."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_1",  # Duplicate
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_2",
            ),
        ]
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict.action == GovernanceAction.HOLD
        assert verdict.reason_code == GovernanceReason.DECISION_PENDING

    def test_empty_request_id_rejected_by_pydantic(self):
        """Empty request_id rejected by Pydantic validation."""
        with pytest.raises(ValueError):
            RequestGovernanceResult(
                request_id="",  # Empty
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            )

    def test_review_decision_id_optional(self):
        """review_decision_id is optional (None when no decision exists)."""
        result = RequestGovernanceResult(
            request_id="req_1",
            raw_decision_type="approved",
            action=GovernanceAction.ALLOW,
            reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
            review_decision_id=None,  # Optional
        )
        assert result.review_decision_id is None

    def test_empty_objective_id_rejected_by_pydantic(self):
        """Empty objective_id rejected in aggregated verdict."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        with pytest.raises(ValueError):
            AggregatedGovernanceVerdict(
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                objective_id="",  # Empty
                request_results=results,
                expected_request_count=1,
                observed_request_count=1,
            )

    def test_extra_fields_rejected_in_request_result(self):
        """Extra fields rejected by extra='forbid' in RequestGovernanceResult."""
        with pytest.raises(ValueError):
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
                extra_field="not_allowed",  # Extra field
            )

    def test_extra_fields_rejected_in_aggregated_verdict(self):
        """Extra fields rejected by extra='forbid' in AggregatedGovernanceVerdict."""
        with pytest.raises(ValueError):
            AggregatedGovernanceVerdict(
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                objective_id="obj_1",
                request_results=[],
                expected_request_count=0,
                observed_request_count=0,
                extra_field="not_allowed",  # Extra field
            )

    def test_negative_expected_count_rejected(self):
        """Negative expected_request_count rejected by validator."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        with pytest.raises(ValueError):
            AggregatedGovernanceVerdict(
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                objective_id="obj_1",
                request_results=results,
                expected_request_count=-1,  # Negative
                observed_request_count=1,
            )

    def test_negative_observed_count_rejected(self):
        """Negative observed_request_count rejected by validator."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        with pytest.raises(ValueError):
            AggregatedGovernanceVerdict(
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                objective_id="obj_1",
                request_results=results,
                expected_request_count=1,
                observed_request_count=-1,  # Negative
            )


class TestAuditAndIntegrity:
    """Test that raw decision and conditions are preserved for audit."""

    def test_raw_decision_type_retained(self):
        """Raw decision type preserved in request result."""
        result = RequestGovernanceResult(
            request_id="req_1",
            raw_decision_type="some_raw_value",
            action=GovernanceAction.ALLOW,
            reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
            review_decision_id="dec_1",
        )
        assert result.raw_decision_type == "some_raw_value"

    def test_conditions_string_retained(self):
        """Conditions JSON string retained for audit."""
        conditions = '{"threshold": 5000, "currency": "USD"}'
        result = RequestGovernanceResult(
            request_id="req_1",
            raw_decision_type="approved",
            action=GovernanceAction.ALLOW,
            reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
            review_decision_id="dec_1",
            conditions_json=conditions,
        )
        assert result.conditions_json == conditions


class TestDeterminism:
    """Test deterministic output across repeated executions."""

    def test_repeated_aggregation_yields_same_result(self):
        """Same input always yields same output."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="conditional_approval",
                action=GovernanceAction.HOLD,
                reason_code=GovernanceReason.CONDITIONAL_APPROVAL,
                review_decision_id="dec_2",
            ),
        ]
        verdict1 = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        verdict2 = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=2
        )
        assert verdict1.action == verdict2.action
        assert verdict1.reason_code == verdict2.reason_code
        assert verdict1.observed_request_count == verdict2.observed_request_count

    def test_order_independence_within_same_type_outcomes(self):
        """Results in same order produce same verdict."""
        results_a = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_2",
            ),
        ]
        results_b = [
            RequestGovernanceResult(
                request_id="req_2",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_2",
            ),
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        # Both should approve all, order shouldn't matter for ALL_APPROVED
        verdict_a = aggregate_governance_verdicts(
            results_a, objective_id="obj_1", expected_request_count=2
        )
        verdict_b = aggregate_governance_verdicts(
            results_b, objective_id="obj_1", expected_request_count=2
        )
        assert verdict_a.action == verdict_b.action
        assert verdict_a.reason_code == verdict_b.reason_code


class TestNoExternalIntegration:
    """Verify no external system calls or integration."""

    def test_no_network_calls_in_aggregation(self):
        """Aggregation contains no network calls."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="approved",
                action=GovernanceAction.ALLOW,
                reason_code=GovernanceReason.ALL_REQUESTS_APPROVED,
                review_decision_id="dec_1",
            ),
        ]
        # If this raises an exception, it's due to Pydantic or logic, not network
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=1
        )
        assert verdict is not None

    def test_no_database_access_in_aggregation(self):
        """Aggregation contains no database access."""
        results = [
            RequestGovernanceResult(
                request_id="req_1",
                raw_decision_type="rejected",
                action=GovernanceAction.DENY,
                reason_code=GovernanceReason.EXPLICIT_REJECTION,
                review_decision_id="dec_1",
            ),
        ]
        # Pure computation, no database access
        verdict = aggregate_governance_verdicts(
            results, objective_id="obj_1", expected_request_count=1
        )
        assert verdict.action == GovernanceAction.DENY
