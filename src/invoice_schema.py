"""
Typed structured invoice schema with evidence backing and confidence scoring.

Uses Pydantic for type safety and validation. Fields can be None when
information is absent or unreadable — no invented default values.
"""

from typing import Optional, List
from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator, ConfigDict


class EvidenceField(BaseModel):
    """Field value with confidence, short evidence, and readability marker."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "A single extracted field with confidence score and evidence."
        }
    )

    value: Optional[str | float | int] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = ""
    readable: bool = True


class InvoiceLineItem(BaseModel):
    """A line item from an invoice."""

    model_config = ConfigDict(
        json_schema_extra={
            "description": "Invoice line item with optional fields for sparse invoices."
        }
    )

    line_number: int
    item_description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class StructuredInvoice(BaseModel):
    """Complete structured invoice extraction result."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "description": "Complete structured invoice with all extracted fields, confidence scores, and audit markers."
        }
    )

    # Direction and parties
    invoice_direction: Optional[str] = None  # AP, AR, UNKNOWN
    supplier_name: Optional[str] = None
    customer_name: Optional[str] = None

    # Document identifiers
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None  # ISO YYYY-MM-DD only
    due_date: Optional[str] = None

    # Amounts (no currency symbols, decimal notation)
    currency: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    gross_amount: Optional[float] = None

    # Payment and references
    payment_terms: Optional[str] = None
    purchase_order_reference: Optional[str] = None
    contract_reference: Optional[str] = None

    # Shipping/logistics
    vessel_name: Optional[str] = None
    vessel_imo: Optional[str] = None
    voyage_reference: Optional[str] = None

    # Banking
    bank_details_present: bool = False
    bank_change_claimed: bool = False

    # Line items
    line_items: List[InvoiceLineItem] = Field(default_factory=list)

    # Extraction metadata
    document_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_warnings: List[str] = Field(default_factory=list)
    missing_mandatory_fields: List[str] = Field(default_factory=list)
    requires_human_review: bool = False

    @field_validator("invoice_date", "due_date")
    @classmethod
    def validate_iso_dates(cls, v):
        """Validate ISO YYYY-MM-DD format."""
        if v is None:
            return v
        if not isinstance(v, str):
            return v
        import re
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError(f"Date must be ISO YYYY-MM-DD format, got {v}")
        return v

    @field_validator("invoice_direction")
    @classmethod
    def validate_direction(cls, v):
        """Validate invoice direction."""
        if v is None:
            return v
        if v not in ("AP", "AR", "UNKNOWN"):
            raise ValueError(f"invoice_direction must be AP, AR, or UNKNOWN, got {v}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v):
        """Currency should be ISO 4217 code."""
        if v is None:
            return v
        if not isinstance(v, str) or len(v) != 3 or not v.isupper():
            raise ValueError(f"Currency must be ISO 4217 (3-letter uppercase), got {v}")
        return v
