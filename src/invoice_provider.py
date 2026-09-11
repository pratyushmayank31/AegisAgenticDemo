"""
Provider abstraction for invoice extraction.

Supports:
- Live Anthropic provider for production and manual testing
- Deterministic mock provider for automated tests (no API calls)
"""

import json
import uuid
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod

from src.invoice_schema import StructuredInvoice


class InvoiceExtractionProvider(ABC):
    """Abstract base for invoice extraction providers."""

    @abstractmethod
    def extract_structured_invoice(
        self,
        extracted_text: str,
        extraction_method: str,
        ocr_confidence: Optional[str] = None,
        source_file: str = "unknown",
        workflow_run_id: str = "",
    ) -> Dict[str, Any]:
        """Extract structured invoice fields."""
        pass


class MockInvoiceProvider(InvoiceExtractionProvider):
    """Deterministic mock provider for tests (no API calls)."""

    def __init__(self, mock_response: Optional[Dict[str, Any]] = None):
        """
        Initialize mock provider.

        Args:
            mock_response: Optional predefined response. If None, generates valid default.
        """
        self.mock_response = mock_response

    def extract_structured_invoice(
        self,
        extracted_text: str,
        extraction_method: str,
        ocr_confidence: Optional[str] = None,
        source_file: str = "unknown",
        workflow_run_id: str = "",
    ) -> Dict[str, Any]:
        """Return deterministic mock response."""
        # Generate workflow_run_id if not provided
        generated_run_id = workflow_run_id or str(uuid.uuid4())

        if self.mock_response:
            result = self.mock_response.copy()
            result["workflow_run_id"] = generated_run_id
            result["provider"] = result.get("provider", "mock")
            return result

        # Generate valid default response
        invoice = StructuredInvoice(
            invoice_direction="AP",
            supplier_name="Mock Supplier Inc.",
            customer_name="Our Company Ltd.",
            invoice_number="INV-2024-001",
            invoice_date="2024-09-01",
            due_date="2024-10-01",
            currency="USD",
            subtotal=1000.00,
            tax_amount=100.00,
            gross_amount=1100.00,
            payment_terms="Net 30",
            purchase_order_reference="PO-2024-001",
            document_confidence=0.95,
        )

        return {
            "structured_invoice": invoice,
            "prompt_version": "invoice-extraction-v1",
            "model": "mock",
            "provider": "mock",
            "confidence": 0.95,
            "warnings": [],
            "workflow_run_id": generated_run_id,
            "validation_failed": False,
        }


class AnthropicInvoiceProvider(InvoiceExtractionProvider):
    """Live Anthropic provider for production invoice extraction."""

    def extract_structured_invoice(
        self,
        extracted_text: str,
        extraction_method: str,
        ocr_confidence: Optional[str] = None,
        source_file: str = "unknown",
        workflow_run_id: str = "",
    ) -> Dict[str, Any]:
        """Extract using live Anthropic API."""
        from src.invoice_extraction_agent import extract_invoice_fields

        return extract_invoice_fields(
            extracted_text=extracted_text,
            extraction_method=extraction_method,
            ocr_confidence=ocr_confidence,
            source_file=source_file,
            workflow_run_id=workflow_run_id,
        )


def get_invoice_provider(use_live: bool = False) -> InvoiceExtractionProvider:
    """
    Get invoice extraction provider.

    Args:
        use_live: If True, use live Anthropic provider. Otherwise, use mock.

    Returns:
        InvoiceExtractionProvider instance
    """
    if use_live:
        return AnthropicInvoiceProvider()
    else:
        return MockInvoiceProvider()
