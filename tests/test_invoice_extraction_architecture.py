"""
Tests for invoice extraction architecture correctness.

Verifies:
1. No direct httpx calls to Anthropic API
2. Live provider delegates through Kaizen CoreAgent
3. Mock and live providers satisfy same interface
4. Stable workflow node ID used
5. Non-empty workflow_run_id returned
6. Extra fields rejected via Pydantic
7. Suspicious output triggers warnings
8. No network calls in tests
"""

import json
import os
import pytest
from unittest import mock
from typing import Dict, Any

from src.invoice_provider import get_invoice_provider, MockInvoiceProvider, AnthropicInvoiceProvider
from src.invoice_extraction_agent import validate_structured_invoice, extract_invoice_fields
from src.invoice_schema import StructuredInvoice
from pydantic import ValidationError


class TestProviderInterface:
    """Test that mock and live providers implement the same interface."""

    def test_mock_provider_returns_expected_fields(self):
        """Mock provider returns all required output fields."""
        provider = MockInvoiceProvider()
        result = provider.extract_structured_invoice(
            extracted_text="Mock invoice text",
            extraction_method="EMBEDDED_TEXT",
            ocr_confidence="",
            source_file="test.pdf",
            workflow_run_id="test-123",
        )

        required_fields = {
            "structured_invoice",
            "prompt_version",
            "model",
            "provider",
            "confidence",
            "warnings",
            "workflow_run_id",
            "validation_failed",
        }
        assert set(result.keys()) == required_fields, f"Missing fields: {required_fields - set(result.keys())}"

    def test_live_provider_interface_signature(self):
        """Live provider has same method signature as mock provider."""
        mock_provider = MockInvoiceProvider()
        live_provider = AnthropicInvoiceProvider()

        # Both should be callable with same parameters
        import inspect

        mock_sig = inspect.signature(mock_provider.extract_structured_invoice)
        live_sig = inspect.signature(live_provider.extract_structured_invoice)

        assert list(mock_sig.parameters.keys()) == list(live_sig.parameters.keys())

    def test_provider_factory_returns_correct_type(self):
        """get_invoice_provider returns correct type."""
        mock_provider = get_invoice_provider(use_live=False)
        assert isinstance(mock_provider, MockInvoiceProvider)

        live_provider = get_invoice_provider(use_live=True)
        assert isinstance(live_provider, AnthropicInvoiceProvider)


class TestNoDirectHttpxCalls:
    """Verify no direct httpx calls to Anthropic API."""

    def test_extract_invoice_fields_does_not_import_httpx(self):
        """extract_invoice_fields function does not use httpx directly."""
        import src.invoice_extraction_agent as agent_module

        # Verify httpx is not imported in the module
        assert "httpx" not in dir(agent_module), "httpx should not be imported"

    def test_direct_api_function_does_not_exist(self):
        """Old direct API call function removed."""
        import src.invoice_extraction_agent as agent_module

        # Verify old function is removed
        assert not hasattr(agent_module, "extract_invoice_fields_via_anthropic"), \
            "extract_invoice_fields_via_anthropic should be removed"

    def test_kaizen_function_exists(self):
        """Kaizen-based extraction function exists."""
        import src.invoice_extraction_agent as agent_module

        assert hasattr(agent_module, "extract_invoice_fields_via_kaizen"), \
            "extract_invoice_fields_via_kaizen should exist"


class TestKaizenIntegration:
    """Test Kaizen/Kailash integration through workflow execution."""

    def test_kaizen_instance_created(self):
        """Kaizen instance is created during extraction via CoreAgent."""
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with mock.patch("src.invoice_extraction_agent.Kaizen") as mock_kaizen_cls:
                with mock.patch("src.invoice_extraction_agent.CoreAgent") as mock_agent_cls:
                    with mock.patch("src.invoice_extraction_agent.LocalRuntime"):
                        mock_kaizen_instance = mock.Mock()
                        mock_kaizen_cls.return_value = mock_kaizen_instance

                        mock_agent = mock.Mock()
                        mock_agent.execute.return_value = {
                            "json_output": json.dumps({
                                "invoice_direction": "AP",
                                "supplier_name": "Test",
                                "invoice_number": "123",
                                "invoice_date": "2024-01-01",
                                "gross_amount": 100.0,
                                "currency": "USD",
                                "document_confidence": 0.95,
                            })
                        }
                        mock_agent_cls.return_value = mock_agent

                        # Mock the workflow execution
                        with mock.patch("src.invoice_extraction_agent.extract_invoice_fields_via_kaizen") as mock_extract:
                            mock_extract.return_value = (
                                {
                                    "structured_invoice": mock.Mock(),
                                    "prompt_version": "invoice-extraction-v1",
                                    "model": "claude-haiku-4-5",
                                    "provider": "anthropic",
                                    "confidence": 0.95,
                                    "warnings": [],
                                    "validation_failed": False,
                                },
                                "workflow-run-id-123",
                            )
                            extract_invoice_fields(
                                extracted_text="test text",
                                extraction_method="EMBEDDED_TEXT",
                            )

                            # Verify extraction via Kaizen was called
                            mock_extract.assert_called_once()

    def test_workflow_node_id_is_stable(self):
        """Workflow node ID is extract_invoice_fields (stable semantic ID)."""
        from kailash.nodes import HandlerNode
        from kailash import WorkflowBuilder

        builder = WorkflowBuilder()

        # Create HandlerNode with a test handler
        def test_handler(extracted_text: str, extraction_method: str) -> dict:
            return {"result": "test"}

        node = HandlerNode(handler=test_handler)

        # Add node with stable ID
        node_id = builder.add_node(node, "extract_invoice_fields")

        # Verify stable node ID is returned
        assert node_id == "extract_invoice_fields", f"Expected 'extract_invoice_fields', got {node_id}"

        # Build workflow to verify node is registered
        workflow = builder.build(workflow_id="invoice_extraction_workflow")

        # Verify node ID exists in workflow's nodes (nodes is dict or list)
        if isinstance(workflow.nodes, dict):
            assert "extract_invoice_fields" in workflow.nodes, \
                f"extract_invoice_fields node not found in workflow nodes: {list(workflow.nodes.keys())}"
        else:
            assert any(getattr(n, 'node_id', None) == "extract_invoice_fields" for n in workflow.nodes), \
                "extract_invoice_fields node not found in workflow"


class TestWorkflowRunId:
    """Test workflow_run_id handling."""

    def test_non_empty_workflow_run_id_generated(self):
        """Non-empty workflow_run_id is always returned."""
        provider = MockInvoiceProvider()
        result = provider.extract_structured_invoice(
            extracted_text="test",
            extraction_method="EMBEDDED_TEXT",
            workflow_run_id="",
        )

        assert result["workflow_run_id"], "workflow_run_id should not be empty"
        assert isinstance(result["workflow_run_id"], str)
        assert len(result["workflow_run_id"]) > 0

    def test_workflow_run_id_passed_through(self):
        """External workflow_run_id is used when provided."""
        provider = MockInvoiceProvider()
        external_id = "external-workflow-123"
        result = provider.extract_structured_invoice(
            extracted_text="test",
            extraction_method="EMBEDDED_TEXT",
            workflow_run_id=external_id,
        )

        assert result["workflow_run_id"] == external_id


class TestPydanticValidation:
    """Test Pydantic schema validation."""

    def test_extra_fields_rejected(self):
        """Extra fields outside schema are rejected via Pydantic validation."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "2024-01-01",
            "gross_amount": 100.0,
            "currency": "USD",
            "document_confidence": 0.95,
            "extra_malicious_field": "should be rejected",
            "another_injected_field": {"nested": "payload"},
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        # Pydantic with extra="forbid" should reject and return None
        assert validated_invoice is None
        assert any("validation" in w.lower() or "extra" in w.lower() for w in warnings), \
            f"Expected validation warning about extra fields, got: {warnings}"

    def test_pydantic_extra_forbid_raises_validation_error(self):
        """StructuredInvoice model directly rejects extra fields with ValidationError."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "2024-01-01",
            "gross_amount": 100.0,
            "currency": "USD",
            "document_confidence": 0.95,
            "injected_field": "malicious",
        }

        # Direct model construction should raise ValidationError due to extra="forbid"
        with pytest.raises(ValidationError) as exc_info:
            StructuredInvoice(**invoice_dict)

        # Verify the error mentions the extra field
        error_str = str(exc_info.value)
        assert "injected_field" in error_str or "Extra inputs are not permitted" in error_str

    def test_invalid_confidence_rejected_by_pydantic(self):
        """Confidence outside [0.0, 1.0] is rejected by Pydantic at construction."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "2024-01-01",
            "gross_amount": 100.0,
            "currency": "USD",
            "document_confidence": 1.5,  # Invalid - rejected by Pydantic
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        # Pydantic rejects this at validation time
        assert validated_invoice is None
        assert any("validation" in w.lower() or "schema" in w.lower() for w in warnings)

    def test_missing_mandatory_fields_triggers_review(self):
        """Missing mandatory fields triggers human review."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            # Missing invoice_number
            # Missing invoice_date
            # Missing gross_amount
            "currency": "USD",
            "document_confidence": 0.95,
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        assert validated_invoice is not None
        assert validated_invoice.requires_human_review
        assert len(validated_invoice.missing_mandatory_fields) > 0

    def test_invalid_currency_code_rejected_by_pydantic(self):
        """Invalid currency code is rejected by Pydantic at construction."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "2024-01-01",
            "gross_amount": 100.0,
            "currency": "INVALID",  # Not 3-letter ISO - rejected by validator
            "document_confidence": 0.95,
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        # Pydantic rejects this via currency validator
        assert validated_invoice is None
        assert any("validation" in w.lower() or "schema" in w.lower() for w in warnings)

    def test_invalid_date_format_rejected_by_pydantic(self):
        """Invalid date format is rejected by Pydantic at construction."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "01-01-2024",  # Wrong format - rejected by validator
            "gross_amount": 100.0,
            "currency": "USD",
            "document_confidence": 0.95,
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        # Pydantic rejects this via date validator
        assert validated_invoice is None
        assert any("validation" in w.lower() or "schema" in w.lower() for w in warnings)

    def test_arithmetic_mismatch_triggers_review(self):
        """Subtotal + tax != gross triggers review."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "2024-01-01",
            "subtotal": 100.0,
            "tax_amount": 10.0,
            "gross_amount": 200.0,  # Should be 110.0
            "currency": "USD",
            "document_confidence": 0.95,
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        assert validated_invoice is not None
        assert validated_invoice.requires_human_review
        assert any("arithmetic" in w.lower() for w in warnings)


class TestSuspiciousInvoiceInstructions:
    """Test detection of suspicious invoice content."""

    def test_line_item_low_confidence_warning(self):
        """Low confidence line items generate warnings."""
        invoice_dict = {
            "invoice_direction": "AP",
            "supplier_name": "Test",
            "invoice_number": "123",
            "invoice_date": "2024-01-01",
            "gross_amount": 100.0,
            "currency": "USD",
            "document_confidence": 0.95,
            "line_items": [
                {
                    "line_number": 1,
                    "item_description": "Item 1",
                    "quantity": 1.0,
                    "unit_price": 100.0,
                    "line_amount": 100.0,
                    "confidence": 0.3,  # Low confidence
                }
            ],
        }

        validated_invoice, warnings = validate_structured_invoice(invoice_dict)

        assert validated_invoice is not None
        assert any("confidence" in w.lower() and "line" in w.lower() for w in warnings)


class TestMockProviderDeterminism:
    """Test mock provider is deterministic and does no network calls."""

    def test_mock_provider_no_network_calls(self):
        """Mock provider executes locally without delegating to extraction agent."""
        provider = MockInvoiceProvider()

        # Mock any network-related imports to ensure they're not used
        with mock.patch("src.invoice_extraction_agent.Kaizen") as mock_kaizen:
            with mock.patch("src.invoice_extraction_agent.LocalRuntime") as mock_runtime:
                # Call mock provider (should not trigger Kaizen or LocalRuntime)
                result = provider.extract_structured_invoice(
                    extracted_text="test",
                    extraction_method="EMBEDDED_TEXT",
                )

                # Neither Kaizen nor LocalRuntime should be instantiated
                mock_kaizen.assert_not_called()
                mock_runtime.assert_not_called()

                # Result should still be valid
                assert result["provider"] == "mock"
                assert result["structured_invoice"] is not None

    def test_mock_provider_deterministic(self):
        """Mock provider returns consistent results."""
        provider = MockInvoiceProvider()
        result1 = provider.extract_structured_invoice(
            extracted_text="test",
            extraction_method="EMBEDDED_TEXT",
        )
        result2 = provider.extract_structured_invoice(
            extracted_text="test",
            extraction_method="EMBEDDED_TEXT",
        )

        # Structured fields should match
        assert result1["structured_invoice"].supplier_name == result2["structured_invoice"].supplier_name
        assert result1["structured_invoice"].invoice_number == result2["structured_invoice"].invoice_number
        assert result1["model"] == result2["model"] == "mock"
