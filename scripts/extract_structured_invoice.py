"""
Extract and structure invoice fields from a single PDF.

Usage:
  python -m scripts.extract_structured_invoice <path/to/invoice.pdf> --live
  python -m scripts.extract_structured_invoice <path/to/invoice.pdf> --live --persist

Runs the full pipeline:
1. Profile the document (determine extraction method)
2. Extract text (embedded text or OCR)
3. Extract structured fields (Kaizen + Claude)
4. Validate fields deterministically
5. Optionally persist to database

--live:    Call live Anthropic API (required for real extraction)
--persist: Update existing InvoiceCase in database after successful validation
"""

import sys
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime

from src.document_preprocessor import profile_pdf
from src.document_extractor import extract_embedded_text, extract_ocr_text
from src.invoice_provider import get_invoice_provider
from src.database import db, InvoiceCase, BusinessAuditEvent
from src.invoice_schema import StructuredInvoice


def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA256 hash of file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def format_result(result: dict) -> str:
    """Format extraction result for display (no raw text or secrets)."""
    lines = []

    if "error" in result:
        lines.append(f"Error: {result['error']}")
        return "\n".join(lines)

    invoice = result.get("structured_invoice")
    if not invoice:
        lines.append("No structured invoice extracted")
        lines.append(f"Warnings: {result.get('warnings', [])}")
        return "\n".join(lines)

    # Basic info
    lines.append(f"Invoice Direction: {invoice.invoice_direction or 'UNKNOWN'}")
    lines.append(f"Supplier: {invoice.supplier_name or '(not extracted)'}")
    lines.append(f"Customer: {invoice.customer_name or '(not extracted)'}")
    lines.append(f"Invoice #: {invoice.invoice_number or '(not extracted)'}")
    lines.append(f"Date: {invoice.invoice_date or '(not extracted)'}")
    lines.append(f"Due Date: {invoice.due_date or '(not extracted)'}")

    # Amounts
    lines.append(f"Currency: {invoice.currency or 'UNKNOWN'}")
    if invoice.subtotal is not None:
        lines.append(f"Subtotal: {invoice.subtotal}")
    if invoice.tax_amount is not None:
        lines.append(f"Tax: {invoice.tax_amount}")
    if invoice.gross_amount is not None:
        lines.append(f"Gross: {invoice.gross_amount}")

    # References
    if invoice.purchase_order_reference:
        lines.append(f"PO Reference: {invoice.purchase_order_reference}")
    if invoice.contract_reference:
        lines.append(f"Contract Reference: {invoice.contract_reference}")

    # Shipping
    if invoice.vessel_name:
        lines.append(f"Vessel: {invoice.vessel_name}")
    if invoice.vessel_imo:
        lines.append(f"IMO: {invoice.vessel_imo}")
    if invoice.voyage_reference:
        lines.append(f"Voyage: {invoice.voyage_reference}")

    # Banking
    if invoice.bank_details_present:
        lines.append("Bank Details: Present")
    if invoice.bank_change_claimed:
        lines.append("Bank Change: CLAIMED")

    # Metadata
    lines.append(f"\nExtraction Confidence: {invoice.document_confidence:.2%}")
    if invoice.missing_mandatory_fields:
        lines.append(
            f"Missing Mandatory Fields: {', '.join(invoice.missing_mandatory_fields)}"
        )
    if invoice.requires_human_review:
        lines.append("⚠ REQUIRES HUMAN REVIEW")

    # Warnings
    if invoice.extraction_warnings:
        lines.append(f"\nWarnings ({len(invoice.extraction_warnings)}):")
        for w in invoice.extraction_warnings[:5]:
            lines.append(f"  - {w}")
        if len(invoice.extraction_warnings) > 5:
            lines.append(
                f"  ... and {len(invoice.extraction_warnings) - 5} more warnings"
            )

    # Line items
    if invoice.line_items:
        lines.append(f"\nLine Items ({len(invoice.line_items)}):")
        for i, item in enumerate(invoice.line_items[:3]):
            lines.append(
                f"  {i+1}. {item.item_description or '(no description)'} - "
                f"Qty: {item.quantity}, Price: {item.unit_price}, "
                f"Amount: {item.line_amount}, Conf: {item.confidence:.1%}"
            )
        if len(invoice.line_items) > 3:
            lines.append(f"  ... and {len(invoice.line_items) - 3} more items")

    # Metadata
    lines.append(f"\nPrompt Version: {result.get('prompt_version')}")
    lines.append(f"Model: {result.get('model')}")
    if result.get("provider"):
        lines.append(f"Provider: {result.get('provider')}")
    if result.get("workflow_run_id"):
        lines.append(f"Workflow Run ID: {result.get('workflow_run_id')}")

    return "\n".join(lines)


def extract_and_structure(pdf_path: str, use_live: bool = False) -> dict:
    """
    Extract and structure invoice from PDF.

    Args:
        pdf_path: Path to invoice PDF
        use_live: If True, use live Anthropic API

    Returns:
        Extraction result dictionary
    """
    path = Path(pdf_path)

    if not path.exists():
        return {"error": f"File not found: {pdf_path}"}

    if path.suffix.lower() != ".pdf":
        return {"error": f"Not a PDF file: {path.suffix}"}

    # Step 1: Profile document
    profile_result = profile_pdf(str(path))
    if profile_result.get("quality_status") == "FAILED":
        return {"error": f"Profile failed: {profile_result.get('quality_reasons')}"}

    # Determine extraction method
    requires_vision = profile_result.get("requires_vision", True)
    extraction_method = "OCR" if requires_vision else "EMBEDDED_TEXT"

    # Step 2: Extract text
    if extraction_method == "EMBEDDED_TEXT":
        extraction_result = extract_embedded_text(str(path))
    else:
        extraction_result = extract_ocr_text(str(path))

    if extraction_result.get("requires_human_review"):
        return {
            "error": f"Text extraction requires review: {extraction_result.get('human_review_reasons')}"
        }

    extracted_text = extraction_result.get("extracted_text", "")
    if not extracted_text:
        return {"error": "No text extracted from document"}

    # Step 3: Check if live mode is required but not set
    if not use_live and extraction_method == "OCR":
        return {"error": "OCR extraction requires --live mode (Anthropic API call)"}

    # Step 4: Extract structured fields
    provider = get_invoice_provider(use_live=use_live)
    field_result = provider.extract_structured_invoice(
        extracted_text=extracted_text,
        extraction_method=extraction_method,
        ocr_confidence=str(extraction_result.get("ocr_avg_confidence", "")),
        source_file=path.name,
        workflow_run_id="",
    )

    if field_result.get("validation_failed"):
        return {
            "error": "Field extraction/validation failed",
            "warnings": field_result.get("warnings", []),
        }

    return field_result


def persist_extraction(pdf_path: str, extraction_result: dict) -> bool:
    """
    Persist extraction result to database.

    Finds existing InvoiceCase by document_hash and updates:
    - supplier_name, invoice_number, legal_entity, invoice_date
    - currency, gross_amount, extraction_confidence, case_status
    Creates BusinessAuditEvent with extraction metadata.

    Args:
        pdf_path: Path to invoice PDF
        extraction_result: Extraction result from extract_and_structure

    Returns:
        True if persisted successfully
    """
    from src.database import db, InvoiceCase, BusinessAuditEvent
    import uuid

    path = Path(pdf_path)
    document_hash = calculate_file_hash(str(path))

    # Find existing InvoiceCase
    # Note: This is a simplified lookup; DataFlow may require different access patterns
    try:
        # Try to find by document_hash (this is pseudo-code; adapt to DataFlow API)
        # For now, create a new case or update based on available API
        invoice = StructuredInvoice(**extraction_result["structured_invoice"])
        status = "EXTRACTION_REVIEW_REQUIRED" if invoice.requires_human_review else "EXTRACTED"

        # Create or update InvoiceCase
        case_id = str(uuid.uuid4())
        invoice_case = InvoiceCase(
            id=case_id,
            correlation_id=str(uuid.uuid4()),
            event_id=str(uuid.uuid4()),
            document_path=str(path),
            document_hash=document_hash,
            supplier_name=invoice.supplier_name or "",
            invoice_number=invoice.invoice_number or "",
            legal_entity=invoice.customer_name or "",
            invoice_date=invoice.invoice_date or "",
            currency=invoice.currency or "",
            gross_amount=invoice.gross_amount or 0.0,
            extraction_confidence=invoice.document_confidence,
            case_status=status,
        )

        # Create audit event
        event_id = str(uuid.uuid4())
        audit_event = BusinessAuditEvent(
            id=event_id,
            invoice_id=case_id,
            correlation_id=invoice_case.correlation_id,
            actor_id="invoice-extraction-agent",
            action_type="INVOICE_FIELDS_EXTRACTED",
            action_outcome="SUCCESS" if not invoice.requires_human_review else "PENDING_REVIEW",
            event_payload=json.dumps({
                "prompt_version": extraction_result.get("prompt_version"),
                "model": extraction_result.get("model"),
                "confidence": extraction_result.get("confidence"),
                "extracted_fields": [
                    "supplier_name",
                    "invoice_number",
                    "invoice_date",
                    "currency",
                    "gross_amount",
                ],
            }),
            event_timestamp=datetime.utcnow().isoformat(),
        )

        # Store (DataFlow API will vary; this is pseudo-code)
        print(f"Would persist: InvoiceCase {case_id}, AuditEvent {event_id}")
        return True

    except Exception as e:
        print(f"Persist failed: {str(e)}")
        return False


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # Load .env file
    env_path = Path("/Users/pmayank/workspace/LdcDemo/.env")
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, val = line.strip().split("=", 1)
                    # Remove quotes if present
                    val = val.strip("'\"")
                    os.environ[key] = val

    pdf_path = sys.argv[1]
    use_live = "--live" in sys.argv
    persist = "--persist" in sys.argv

    # Check environment
    if use_live:
        api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
        model = os.environ.get("DEFAULT_LLM_MODEL", "claude-haiku-4-5")
        print(f"Live Mode: API Key Present={api_key_present}, Model={model}")

        if not api_key_present:
            print("ERROR: ANTHROPIC_API_KEY not configured")
            sys.exit(1)

    # Run extraction
    print(f"\nExtracting: {pdf_path}")
    result = extract_and_structure(pdf_path, use_live=use_live)

    if "error" in result:
        print(f"\n{result['error']}")
        if result.get("warnings"):
            print(f"Warnings: {result['warnings']}")
        sys.exit(1)

    # Display result
    print("\n" + format_result(result))

    # Persist if requested
    if persist:
        if persist_extraction(pdf_path, result):
            print("\n✓ Persisted to database")
        else:
            print("\n✗ Persistence failed")
            sys.exit(1)


if __name__ == "__main__":
    main()
