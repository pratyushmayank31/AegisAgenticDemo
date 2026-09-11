"""
Tests for Aegis governance adapter with injected client and offline contracts.

Verifies:
- Title/description never accepted from caller, constructed internally only
- Strict enums for target systems and risk flags
- Format/length validation on identifiers and accounting codes
- Currency and numeric field validation
- Safe proposal mapping to Aegis objectives
- SDK method delegation verified with mocks
- Fail-closed semantics (SDK errors → HOLD)
- All requests evaluated (not just first)
- Deterministic aggregation
- Metadata built explicitly (not model_dump)
- No database or direct HTTP integration
- No arbitrary invoice/bank content crosses boundary
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from decimal import Decimal
import math

from src.governance_contract import (
    GovernanceAction,
    GovernanceReason,
)
from src.aegis_governance_adapter import (
    GovernanceProposal,
    AegisSubmission,
    AegisGovernanceAdapter,
    AegisGovernanceAdapterException,
    ProposedTargetSystem,
    GovernanceRiskFlag,
)


def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.run(coro)


@pytest.fixture
def mock_client():
    """Create a mock Aegis client with all required modules."""
    client = MagicMock()
    client.objectives = AsyncMock()
    client.requests = AsyncMock()
    client.review_decisions = AsyncMock()
    return client


@pytest.fixture
def sample_proposal():
    """Create a sample governance proposal."""
    return GovernanceProposal(
        invoice_id="inv_001",
        correlation_id="corr_001",
        agent_id="agent_governance",
        proposed_target_system=ProposedTargetSystem.ORACLE_FUSION,
        proposed_accounting_code="5100-10",
        gross_amount=Decimal("5000.00"),
        currency="USD",
        extraction_confidence=0.95,
        risk_flags=[GovernanceRiskFlag.AMOUNT_THRESHOLD],
    )


class TestGovernanceProposal:
    """Test GovernanceProposal model validation."""

    def test_proposal_creation_valid(self, sample_proposal):
        """Valid proposal creates successfully."""
        assert sample_proposal.invoice_id == "inv_001"
        assert sample_proposal.extraction_confidence == 0.95
        assert sample_proposal.proposed_target_system == ProposedTargetSystem.ORACLE_FUSION

    def test_proposal_title_field_not_accepted(self):
        """Title field cannot be supplied by caller."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                title="Arbitrary Title",
            )

    def test_proposal_description_field_not_accepted(self):
        """Description field cannot be supplied by caller."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                description="Arbitrary Description",
            )

    def test_proposal_empty_invoice_id_rejected(self):
        """Empty invoice_id rejected by Pydantic."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

    def test_proposal_empty_correlation_id_rejected(self):
        """Empty correlation_id rejected by Pydantic."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

    def test_proposal_invoice_id_with_newline_rejected(self):
        """Invoice ID containing newline rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001\n",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

    def test_proposal_correlation_id_with_slash_rejected(self):
        """Correlation ID containing slash rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr/001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

    def test_proposal_agent_id_with_spaces_rejected(self):
        """Agent ID containing spaces rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent 001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

    def test_proposal_invoice_id_exceeds_max_length_rejected(self):
        """Invoice ID exceeding 64 characters rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="x" * 65,
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

    def test_proposal_unknown_target_system_rejected(self):
        """Unknown target system value rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system="SAP",  # Not in enum
                extraction_confidence=0.95,
            )

    def test_proposal_unknown_risk_flag_rejected(self):
        """Unknown risk flag value rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                risk_flags=["unknown_risk"],  # Not in enum
            )

    def test_proposal_free_text_accounting_code_rejected(self):
        """Free-text accounting code (with letters) rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                proposed_accounting_code="GL5100",  # Contains letters
            )

    def test_proposal_iban_like_accounting_code_rejected(self):
        """IBAN-like accounting code (alphanumeric) rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                proposed_accounting_code="DE89370400440532013000",  # IBAN-like
            )

    def test_proposal_accounting_code_exceeds_max_length_rejected(self):
        """Accounting code exceeding 32 characters rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                proposed_accounting_code="1234567890123456789012345678901234",  # 34 chars, > 32
            )

    def test_proposal_lowercase_currency_rejected(self):
        """Lowercase currency code rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                currency="usd",  # Must be uppercase
            )

    def test_proposal_invalid_currency_format_rejected(self):
        """Invalid currency format (not 3 letters) rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                currency="USDA",  # 4 letters
            )

    def test_proposal_negative_gross_amount_rejected(self):
        """Negative gross amount rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                gross_amount=Decimal("-100.00"),
            )

    def test_proposal_nan_gross_amount_rejected(self):
        """NaN gross amount rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                gross_amount=Decimal("NaN"),
            )

    def test_proposal_infinite_gross_amount_rejected(self):
        """Infinite gross amount rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                gross_amount=Decimal("Infinity"),
            )

    def test_proposal_confidence_out_of_range_rejected(self):
        """Confidence outside [0, 1] rejected."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=1.5,
            )

    def test_proposal_extra_fields_rejected(self):
        """Extra fields rejected by extra='forbid'."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                extra_field="not_allowed",
            )

    def test_proposal_valid_accounting_code_formats(self):
        """Valid accounting code formats accepted."""
        # Numeric only
        p1 = GovernanceProposal(
            invoice_id="inv_001",
            correlation_id="corr_001",
            agent_id="agent_001",
            proposed_target_system=ProposedTargetSystem.VESON_IMOS,
            extraction_confidence=0.95,
            proposed_accounting_code="5100",
        )
        assert p1.proposed_accounting_code == "5100"

        # With hyphens
        p2 = GovernanceProposal(
            invoice_id="inv_001",
            correlation_id="corr_001",
            agent_id="agent_001",
            proposed_target_system=ProposedTargetSystem.VESON_IMOS,
            extraction_confidence=0.95,
            proposed_accounting_code="5100-10",
        )
        assert p2.proposed_accounting_code == "5100-10"

        # With periods
        p3 = GovernanceProposal(
            invoice_id="inv_001",
            correlation_id="corr_001",
            agent_id="agent_001",
            proposed_target_system=ProposedTargetSystem.VESON_IMOS,
            extraction_confidence=0.95,
            proposed_accounting_code="5100.10",
        )
        assert p3.proposed_accounting_code == "5100.10"

    def test_proposal_valid_identifier_formats(self):
        """Valid identifier formats with alphanumeric, hyphen, underscore, period."""
        p = GovernanceProposal(
            invoice_id="inv-001_v2.1",
            correlation_id="corr-001.x",
            agent_id="agent_gov-01",
            proposed_target_system=ProposedTargetSystem.VESON_IMOS,
            extraction_confidence=0.95,
        )
        assert p.invoice_id == "inv-001_v2.1"
        assert p.correlation_id == "corr-001.x"
        assert p.agent_id == "agent_gov-01"


class TestAegisSubmission:
    """Test AegisSubmission model."""

    def test_submission_creation_valid(self):
        """Valid submission creates successfully."""
        submission = AegisSubmission(
            objective_id="obj_001",
            objective_status="pending",
            execution_status="processing",
            expected_request_count=3,
            correlation_id="corr_001",
        )
        assert submission.objective_id == "obj_001"
        assert submission.expected_request_count == 3

    def test_submission_empty_objective_id_rejected(self):
        """Empty objective_id rejected."""
        with pytest.raises(ValueError):
            AegisSubmission(
                objective_id="",
                correlation_id="corr_001",
                expected_request_count=1,
            )

    def test_submission_negative_expected_count_rejected(self):
        """Negative expected_request_count rejected."""
        with pytest.raises(ValueError):
            AegisSubmission(
                objective_id="obj_001",
                correlation_id="corr_001",
                expected_request_count=-1,
            )

    def test_submission_extra_fields_rejected(self):
        """Extra fields rejected by extra='forbid'."""
        with pytest.raises(ValueError):
            AegisSubmission(
                objective_id="obj_001",
                correlation_id="corr_001",
                expected_request_count=1,
                extra_field="not_allowed",
            )


class TestSubmitMethod:
    """Test adapter.submit() method."""

    def test_submit_creates_objective(self, mock_client, sample_proposal):
        """submit() calls client.objectives.create() with internally-generated title/description."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {
                "status": "processing",
                "task_count": 3,
            }

            adapter = AegisGovernanceAdapter(mock_client)
            submission = await adapter.submit(sample_proposal)

            mock_client.objectives.create.assert_called_once()
            call_args = mock_client.objectives.create.call_args
            # Verify title/description are internally generated, not from proposal
            assert call_args[1]["title"] == f"Finance governance request {sample_proposal.invoice_id}"
            assert "Evaluate proposed route" in call_args[1]["description"]
            assert sample_proposal.invoice_id in call_args[1]["description"]
            assert call_args[1]["agent_id"] == sample_proposal.agent_id

        run_async(async_test())

    def test_submit_internal_title_format(self, mock_client, sample_proposal):
        """Generated title follows strict format: 'Finance governance request <invoice_id>'."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            title = call_args[1]["title"]
            # Verify format and that no arbitrary caller text is present
            assert title == "Finance governance request inv_001"
            assert "supplier" not in title.lower()
            assert "bank" not in title.lower()

        run_async(async_test())

    def test_submit_internal_description_format(self, mock_client, sample_proposal):
        """Generated description includes target system and invoice_id, no arbitrary text."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            description = call_args[1]["description"]
            # Verify format
            assert "Evaluate proposed route" in description
            assert sample_proposal.proposed_target_system.value in description
            assert sample_proposal.invoice_id in description
            # Verify no arbitrary text
            assert "supplier" not in description.lower()
            assert "bank" not in description.lower()
            assert "account" not in description.lower()

        run_async(async_test())

    def test_submit_enums_serialized_correctly(self, mock_client, sample_proposal):
        """Risk flags are enums and properly managed."""
        async def async_test():
            proposal = GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.SMARTPAL,
                extraction_confidence=0.95,
                risk_flags=[
                    GovernanceRiskFlag.DUPLICATE_INVOICE,
                    GovernanceRiskFlag.LOW_CONFIDENCE,
                ],
            )

            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            submission = await adapter.submit(proposal)

            # Verify proposal has enums, not strings
            assert isinstance(proposal.proposed_target_system, ProposedTargetSystem)
            assert all(isinstance(f, GovernanceRiskFlag) for f in proposal.risk_flags)
            assert proposal.proposed_target_system == ProposedTargetSystem.SMARTPAL

        run_async(async_test())

    def test_submit_triggers_execution(self, mock_client, sample_proposal):
        """submit() calls client.objectives.trigger_execution()."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {
                "status": "processing",
                "task_count": 2,
            }

            adapter = AegisGovernanceAdapter(mock_client)
            submission = await adapter.submit(sample_proposal)

            mock_client.objectives.trigger_execution.assert_called_once_with("obj_001")

        run_async(async_test())

    def test_submit_captures_task_count(self, mock_client, sample_proposal):
        """submit() extracts expected_request_count from trigger result."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {
                "status": "already_decomposed",
                "task_count": 5,
            }

            adapter = AegisGovernanceAdapter(mock_client)
            submission = await adapter.submit(sample_proposal)

            assert submission.expected_request_count == 5

        run_async(async_test())

    def test_submit_preserves_correlation_id(self, mock_client, sample_proposal):
        """submit() preserves correlation_id in submission."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            submission = await adapter.submit(sample_proposal)

            assert submission.correlation_id == sample_proposal.correlation_id

        run_async(async_test())

    def test_submit_objective_creation_failure_raises(self, mock_client, sample_proposal):
        """submit() raises on objective creation failure."""
        async def async_test():
            mock_client.objectives.create.side_effect = Exception("API error")

            adapter = AegisGovernanceAdapter(mock_client)

            with pytest.raises(AegisGovernanceAdapterException):
                await adapter.submit(sample_proposal)

        run_async(async_test())

    def test_submit_trigger_failure_raises(self, mock_client, sample_proposal):
        """submit() raises on trigger_execution failure."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.side_effect = Exception("Trigger failed")

            adapter = AegisGovernanceAdapter(mock_client)

            with pytest.raises(AegisGovernanceAdapterException):
                await adapter.submit(sample_proposal)

        run_async(async_test())

    def test_submit_passes_metadata_to_objectives(self, mock_client, sample_proposal):
        """submit() passes metadata parameter to objectives.create()."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            assert "metadata" in call_args[1], "metadata parameter must be passed to objectives.create()"
            metadata = call_args[1]["metadata"]
            assert isinstance(metadata, dict)

        run_async(async_test())

    def test_submit_metadata_contains_exactly_5_keys(self, mock_client, sample_proposal):
        """Metadata contains exactly 5 approved keys."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]
            approved_keys = {
                "invoice_id",
                "correlation_id",
                "proposed_target_system",
                "risk_flags",
                "governance_contract_version",
            }
            assert set(metadata.keys()) == approved_keys, f"Expected exactly {approved_keys}, got {set(metadata.keys())}"

        run_async(async_test())

    def test_submit_target_system_serialized_as_enum_value(self, mock_client, sample_proposal):
        """Target system is serialized as controlled enum value string."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]
            assert metadata["proposed_target_system"] == "ORACLE_FUSION"
            assert isinstance(metadata["proposed_target_system"], str)

        run_async(async_test())

    def test_submit_risk_flags_sorted_and_unique(self, mock_client):
        """Risk flags are sorted and deduplicated in metadata."""
        async def async_test():
            proposal = GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                risk_flags=[
                    GovernanceRiskFlag.AMOUNT_THRESHOLD,
                    GovernanceRiskFlag.DUPLICATE_INVOICE,
                    GovernanceRiskFlag.AMOUNT_THRESHOLD,  # Duplicate
                    GovernanceRiskFlag.LOW_CONFIDENCE,
                ],
            )

            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]
            flags = metadata["risk_flags"]
            # Should be sorted and unique
            assert flags == sorted(set(flags))
            assert len(flags) == 3  # Three unique flags
            assert all(isinstance(f, str) for f in flags)

        run_async(async_test())

    def test_submit_empty_risk_flags_becomes_empty_list(self, mock_client):
        """Empty risk flags list remains empty in metadata."""
        async def async_test():
            proposal = GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.UNDETERMINED,
                extraction_confidence=0.8,
                risk_flags=[],
            )

            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]
            assert metadata["risk_flags"] == []
            assert isinstance(metadata["risk_flags"], list)

        run_async(async_test())

    def test_submit_contract_version_fixed_and_stable(self, mock_client, sample_proposal):
        """Governance contract version is fixed and stable."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]
            assert metadata["governance_contract_version"] == "ldc-finance-governance-v1"

        run_async(async_test())

    def test_submit_invoice_correlation_ids_preserved(self, mock_client, sample_proposal):
        """Invoice and correlation IDs are preserved in metadata."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]
            assert metadata["invoice_id"] == sample_proposal.invoice_id
            assert metadata["correlation_id"] == sample_proposal.correlation_id

        run_async(async_test())

    def test_submit_no_prohibited_fields_in_metadata(self, mock_client, sample_proposal):
        """No prohibited fields appear in metadata."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            metadata = call_args[1]["metadata"]

            prohibited = {
                "supplier_name",
                "customer_name",
                "invoice_number",
                "line_items",
                "bank_details",
                "gross_amount",
                "currency",
                "extraction_confidence",
                "proposed_accounting_code",
                "title",
                "description",
            }
            assert not (set(metadata.keys()) & prohibited), \
                f"Prohibited fields found in metadata: {set(metadata.keys()) & prohibited}"

        run_async(async_test())

    def test_submit_deterministic_metadata_output(self, mock_client):
        """Same proposal produces same metadata on repeated submission."""
        async def async_test():
            proposal = GovernanceProposal(
                invoice_id="inv_det",
                correlation_id="corr_det",
                agent_id="agent_det",
                proposed_target_system=ProposedTargetSystem.SMARTPAL,
                extraction_confidence=0.85,
                risk_flags=[
                    GovernanceRiskFlag.MISSING_REFERENCE,
                    GovernanceRiskFlag.LOW_CONFIDENCE,
                ],
            )

            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)

            # First submission
            await adapter.submit(proposal)
            call_args_1 = mock_client.objectives.create.call_args[1]["metadata"]

            # Reset mock
            mock_client.reset_mock()
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            # Second submission with same proposal
            await adapter.submit(proposal)
            call_args_2 = mock_client.objectives.create.call_args[1]["metadata"]

            # Metadata should be identical
            assert call_args_1 == call_args_2
            # Risk flags should be in deterministic order
            assert call_args_1["risk_flags"] == call_args_2["risk_flags"]
            assert call_args_1["risk_flags"] == sorted(call_args_1["risk_flags"])

        run_async(async_test())


class TestCheckOnceMethod:
    """Test adapter.check_once() method."""

    def _make_mock_request(self, request_id):
        """Create a mock Request object."""
        mock_req = MagicMock()
        mock_req.id = request_id
        mock_req.objective_id = "obj_001"
        return mock_req

    def _make_mock_review_decision(self, decision_type, decision_id="dec_001"):
        """Create a mock ReviewDecision object."""
        mock_decision = MagicMock()
        mock_decision.id = decision_id
        mock_decision.request_id = "req_001"
        mock_decision.decision_type = decision_type
        mock_decision.conditions_json = ""
        return mock_decision

    def test_check_once_all_approved_yields_allow(self, mock_client):
        """All approved requests → ALLOW."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("approved")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.ALLOW
            assert verdict.reason_code == GovernanceReason.ALL_REQUESTS_APPROVED

        run_async(async_test())

    def test_check_once_one_rejected_yields_deny(self, mock_client):
        """One rejected request → DENY."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("rejected")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.DENY
            assert verdict.reason_code == GovernanceReason.EXPLICIT_REJECTION

        run_async(async_test())

    def test_check_once_conditional_yields_hold(self, mock_client):
        """Conditional approval → HOLD."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("conditional_approval")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.CONDITIONAL_APPROVAL

        run_async(async_test())

    def test_check_once_revision_requested_yields_hold(self, mock_client):
        """Revision requested → HOLD."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("revision_requested")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.REVISION_REQUESTED

        run_async(async_test())

    def test_check_once_missing_decision_yields_hold(self, mock_client):
        """Request with no decision yet → HOLD / DECISION_PENDING."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = None

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.DECISION_PENDING

        run_async(async_test())

    def test_check_once_unknown_decision_yields_hold(self, mock_client):
        """Unknown decision type → HOLD."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("some_new_unknown_type")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.UNKNOWN_DECISION_TYPE

        run_async(async_test())

    def test_check_once_no_requests_yields_hold(self, mock_client):
        """No requests → HOLD / NO_REQUESTS."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = []
            mock_client.requests.list.return_value = mock_response

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=0,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.NO_REQUESTS

        run_async(async_test())

    def test_check_once_duplicate_request_ids_yields_hold(self, mock_client):
        """Duplicate request IDs → HOLD."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [
                self._make_mock_request("req_1"),
                self._make_mock_request("req_1"),
            ]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("approved")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=2,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.DECISION_PENDING

        run_async(async_test())

    def test_check_once_fewer_requests_than_expected_yields_hold(self, mock_client):
        """Expected 2, observed 1 → HOLD / REQUEST_PENDING."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("approved")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=2,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.REQUEST_PENDING

        run_async(async_test())

    def test_check_once_more_requests_than_expected_yields_hold(self, mock_client):
        """More observed than expected → HOLD."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [
                self._make_mock_request("req_1"),
                self._make_mock_request("req_2"),
            ]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("approved")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD

        run_async(async_test())

    def test_check_once_all_requests_inspected(self, mock_client):
        """check_once() inspects all returned requests, not just the first."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [
                self._make_mock_request("req_1"),
                self._make_mock_request("req_2"),
                self._make_mock_request("req_3"),
            ]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("approved")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=3,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert mock_client.review_decisions.get_latest.call_count == 3

        run_async(async_test())

    def test_check_once_request_list_failure_yields_hold(self, mock_client):
        """Request list failure → HOLD / AEGIS_UNAVAILABLE."""
        async def async_test():
            mock_client.requests.list.side_effect = Exception("API error")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD
            assert verdict.reason_code == GovernanceReason.AEGIS_UNAVAILABLE

        run_async(async_test())

    def test_check_once_review_decision_failure_yields_hold(self, mock_client):
        """Review decision fetch failure → HOLD / DECISION_PENDING."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.side_effect = Exception("Decision fetch failed")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.action == GovernanceAction.HOLD

        run_async(async_test())

    def test_check_once_deterministic_repeated(self, mock_client):
        """Repeated check_once() with same state yields same verdict."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [self._make_mock_request("req_1")]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = self._make_mock_review_decision("approved")

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict1 = await adapter.check_once(submission)
            verdict2 = await adapter.check_once(submission)

            assert verdict1.action == verdict2.action
            assert verdict1.reason_code == verdict2.reason_code

        run_async(async_test())


class TestMetadataConstruction:
    """Verify metadata is built explicitly, not via model_dump."""

    def test_no_model_dump_in_submit(self):
        """Adapter does not use model_dump() for Aegis submission."""
        import inspect
        source = inspect.getsource(AegisGovernanceAdapter.submit)
        assert "model_dump" not in source

    def test_no_vars_in_submit(self):
        """Adapter does not use vars() for Aegis submission."""
        import inspect
        source = inspect.getsource(AegisGovernanceAdapter.submit)
        assert "vars(" not in source

    def test_no_dict_access_in_submit(self):
        """Adapter does not use __dict__ for Aegis submission."""
        import inspect
        source = inspect.getsource(AegisGovernanceAdapter.submit)
        assert "__dict__" not in source

    def test_submit_does_not_forward_unvalidated_metadata(self, mock_client):
        """Adapter only forwards explicitly validated fields."""
        async def async_test():
            proposal = GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
            )

            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(proposal)

            # Verify only agent_id, title, description, and metadata passed (no extra fields)
            call_kwargs = mock_client.objectives.create.call_args[1]
            assert set(call_kwargs.keys()) == {"title", "description", "agent_id", "metadata"}

        run_async(async_test())


class TestNoExternalIntegration:
    """Verify no unintended external integrations."""

    def test_no_from_env_call(self):
        """Adapter does not call AgenticOSClient.from_env()."""
        import inspect
        source = inspect.getsource(AegisGovernanceAdapter)
        assert "from_env" not in source

    def test_adapter_takes_client_by_injection(self):
        """Adapter requires client injection in constructor."""
        from inspect import signature
        sig = signature(AegisGovernanceAdapter.__init__)
        assert "client" in sig.parameters

    def test_no_dataflow_import(self):
        """Adapter does not import DataFlow or database."""
        import inspect
        source = inspect.getsource(AegisGovernanceAdapter)
        assert "DataFlow" not in source
        assert "database" not in source


class TestPrivacyRegression:
    """Regression tests for privacy controls — verify no sensitive data leaks to Aegis."""

    def test_proposal_rejects_supplier_name_field(self):
        """Proposal does not accept supplier_name field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                supplier_name="Acme Corp",
            )

    def test_proposal_rejects_customer_name_field(self):
        """Proposal does not accept customer_name field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                customer_name="Ship To Corp",
            )

    def test_proposal_rejects_raw_invoice_text_field(self):
        """Proposal does not accept raw_invoice_text field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                raw_invoice_text="Invoice text here",
            )

    def test_proposal_rejects_bank_details_field(self):
        """Proposal does not accept bank_details field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                bank_details="DE89370400440532013000",
            )

    def test_proposal_rejects_account_number_field(self):
        """Proposal does not accept account_number field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                account_number="123456789",
            )

    def test_proposal_rejects_iban_field(self):
        """Proposal does not accept iban field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                iban="DE89370400440532013000",
            )

    def test_proposal_rejects_line_item_description(self):
        """Proposal does not accept line_item_description field."""
        with pytest.raises(ValueError):
            GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                line_item_description="Widget shipment",
            )

    def test_submit_does_not_leak_proposal_fields(self, mock_client, sample_proposal):
        """submit() does not pass unvalidated proposal fields to Aegis client."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            # Capture all arguments passed to objectives.create
            call_args = mock_client.objectives.create.call_args
            call_kwargs = call_args[1] if call_args[1] else {}

            # Verify only safe fields were passed (including approved metadata)
            passed_keys = set(call_kwargs.keys())
            expected_keys = {"title", "description", "agent_id", "metadata"}
            assert passed_keys == expected_keys, f"Unexpected fields passed: {passed_keys - expected_keys}"

        run_async(async_test())

    def test_submit_title_does_not_contain_amount(self, mock_client, sample_proposal):
        """Generated title does not expose financial amounts."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            title = call_args[1]["title"]

            # Amount should not be in title
            assert "5000" not in title
            assert str(sample_proposal.gross_amount) not in title

        run_async(async_test())

    def test_submit_description_does_not_contain_amount(self, mock_client, sample_proposal):
        """Generated description does not expose financial amounts."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            description = call_args[1]["description"]

            # Amount should not be in description
            assert "5000" not in description
            assert str(sample_proposal.gross_amount) not in description

        run_async(async_test())

    def test_submit_description_does_not_contain_currency(self, mock_client, sample_proposal):
        """Generated description does not expose currency codes."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            description = call_args[1]["description"]

            # Currency should not be in description
            assert "USD" not in description
            assert sample_proposal.currency not in description

        run_async(async_test())

    def test_submit_description_does_not_contain_accounting_code(self, mock_client, sample_proposal):
        """Generated description does not expose accounting codes."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            description = call_args[1]["description"]

            # Accounting code should not be in description
            assert "5100-10" not in description
            assert sample_proposal.proposed_accounting_code not in description

        run_async(async_test())

    def test_submit_description_does_not_contain_confidence(self, mock_client, sample_proposal):
        """Generated description does not expose extraction confidence."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(sample_proposal)

            call_args = mock_client.objectives.create.call_args
            description = call_args[1]["description"]

            # Confidence should not be in description
            assert "0.95" not in description
            assert str(sample_proposal.extraction_confidence) not in description

        run_async(async_test())

    def test_submit_description_does_not_contain_risk_flags(self, mock_client):
        """Generated description does not expose risk flag values."""
        async def async_test():
            proposal = GovernanceProposal(
                invoice_id="inv_001",
                correlation_id="corr_001",
                agent_id="agent_001",
                proposed_target_system=ProposedTargetSystem.VESON_IMOS,
                extraction_confidence=0.95,
                risk_flags=[
                    GovernanceRiskFlag.DUPLICATE_INVOICE,
                    GovernanceRiskFlag.BANK_DETAILS_CHANGED,
                ],
            )

            mock_objective = MagicMock()
            mock_objective.id = "obj_001"
            mock_objective.status = "pending"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            await adapter.submit(proposal)

            call_args = mock_client.objectives.create.call_args
            title = call_args[1]["title"]
            description = call_args[1]["description"]

            # Risk flag enum values should not appear in title or description
            assert "DUPLICATE_INVOICE" not in title
            assert "DUPLICATE_INVOICE" not in description
            assert "BANK_DETAILS_CHANGED" not in title
            assert "BANK_DETAILS_CHANGED" not in description
            # Also check lowercase variants that might leak
            assert "duplicate_invoice" not in description.lower()
            assert "bank_details_changed" not in description.lower()

        run_async(async_test())

    def test_proposal_allows_only_validated_fields(self, sample_proposal):
        """Proposal accepts only the validated fields in the schema."""
        # Verify all expected fields are present and valid
        assert sample_proposal.invoice_id is not None
        assert sample_proposal.correlation_id is not None
        assert sample_proposal.agent_id is not None
        assert sample_proposal.proposed_target_system is not None
        assert sample_proposal.extraction_confidence is not None
        # Optional fields
        assert hasattr(sample_proposal, "proposed_accounting_code")
        assert hasattr(sample_proposal, "gross_amount")
        assert hasattr(sample_proposal, "currency")
        assert hasattr(sample_proposal, "risk_flags")

    def test_proposal_enum_values_strict(self):
        """Proposal enforces strict enum values for system and risk flags."""
        # Valid target system
        p1 = GovernanceProposal(
            invoice_id="inv_001",
            correlation_id="corr_001",
            agent_id="agent_001",
            proposed_target_system=ProposedTargetSystem.ORACLE_FUSION,
            extraction_confidence=0.95,
        )
        assert p1.proposed_target_system == ProposedTargetSystem.ORACLE_FUSION

        # Valid risk flags
        p2 = GovernanceProposal(
            invoice_id="inv_001",
            correlation_id="corr_001",
            agent_id="agent_001",
            proposed_target_system=ProposedTargetSystem.VESON_IMOS,
            extraction_confidence=0.95,
            risk_flags=[GovernanceRiskFlag.DUPLICATE_INVOICE],
        )
        assert p2.risk_flags == [GovernanceRiskFlag.DUPLICATE_INVOICE]


class TestAuditTrail:
    """Test that audit-critical IDs are preserved."""

    def test_objective_id_preserved(self, mock_client, sample_proposal):
        """Objective ID preserved through submission."""
        async def async_test():
            mock_objective = MagicMock()
            mock_objective.id = "obj_audit_001"
            mock_client.objectives.create.return_value = mock_objective
            mock_client.objectives.trigger_execution.return_value = {"status": "processing"}

            adapter = AegisGovernanceAdapter(mock_client)
            submission = await adapter.submit(sample_proposal)

            assert submission.objective_id == "obj_audit_001"

        run_async(async_test())

    def test_request_ids_preserved_in_verdict(self, mock_client):
        """Request IDs preserved in verdict results."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [
                MagicMock(id="req_audit_1"),
                MagicMock(id="req_audit_2"),
            ]
            mock_client.requests.list.return_value = mock_response
            mock_client.review_decisions.get_latest.return_value = MagicMock(
                id="dec_001", decision_type="approved"
            )

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=2,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert len(verdict.request_results) == 2
            assert verdict.request_results[0].request_id == "req_audit_1"
            assert verdict.request_results[1].request_id == "req_audit_2"

        run_async(async_test())

    def test_review_decision_ids_preserved(self, mock_client):
        """Review decision IDs preserved in results."""
        async def async_test():
            mock_response = MagicMock()
            mock_response.items = [MagicMock(id="req_001")]
            mock_client.requests.list.return_value = mock_response

            mock_decision = MagicMock()
            mock_decision.id = "review_dec_audit_001"
            mock_decision.decision_type = "approved"
            mock_client.review_decisions.get_latest.return_value = mock_decision

            adapter = AegisGovernanceAdapter(mock_client)
            submission = AegisSubmission(
                objective_id="obj_001",
                expected_request_count=1,
                correlation_id="corr_001",
            )

            verdict = await adapter.check_once(submission)

            assert verdict.request_results[0].review_decision_id == "review_dec_audit_001"

        run_async(async_test())
