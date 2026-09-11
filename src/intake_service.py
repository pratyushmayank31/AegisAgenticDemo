"""
Controlled invoice intake service with event deduplication.

Registers PDFs as finance cases before AI processing:
PDF → validate → hash → detect replay → workflow → case creation
"""

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from kailash import WorkflowBuilder, LocalRuntime
from kailash.nodes.code import PythonCodeNode

from src.database import db
from src.kill_switch_service import KillSwitchService


def generate_case_id() -> str:
    """Generate a readable case ID beginning with INV-."""
    return f"INV-{uuid.uuid4().hex[:12].upper()}"


def generate_correlation_id() -> str:
    """Generate a readable correlation ID beginning with CORR-."""
    return f"CORR-{uuid.uuid4().hex[:12].upper()}"


def generate_event_id() -> str:
    """Generate a readable event ID beginning with EVT-."""
    return f"EVT-{uuid.uuid4().hex[:12].upper()}"


def generate_audit_id() -> str:
    """Generate a readable audit ID beginning with AUD-."""
    return f"AUD-{uuid.uuid4().hex[:12].upper()}"


def calculate_document_hash(file_path: str) -> str:
    """
    Calculate SHA-256 hash of a document using chunked reading.

    Args:
        file_path: Path to the file

    Returns:
        SHA-256 hex digest

    Raises:
        FileNotFoundError: If file does not exist
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def derive_event_id_from_hash(document_hash: str) -> str:
    """
    Derive a stable event ID from document hash.

    This ensures replaying the same invoice produces the same event ID,
    enabling deduplication.

    Args:
        document_hash: SHA-256 hash of the document

    Returns:
        Event ID beginning with EVT-
    """
    # Use first 12 chars of hash to create deterministic ID
    return f"EVT-{document_hash[:12].upper()}"




def check_event_replay(event_id: str) -> Optional[Dict[str, Any]]:
    """
    Check if an event has been processed before.

    Args:
        event_id: The event ID to check

    Returns:
        Existing EventReceipt record if found, None otherwise
    """
    receipt = db.express_sync.find_one(
        "EventReceipt",
        {"id": event_id}
    )
    return receipt


def get_existing_invoice(event_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the original invoice case for a replayed event.

    Args:
        event_id: The event ID

    Returns:
        Existing InvoiceCase record if found, None otherwise
    """
    case = db.express_sync.find_one(
        "InvoiceCase",
        {"event_id": event_id}
    )
    return case


def create_event_receipt(
    event_id: str,
    invoice_id: str,
    correlation_id: str,
    document_hash: str,
) -> Dict[str, Any]:
    """
    Create an EventReceipt to mark event as processed.

    Args:
        event_id: Event ID
        invoice_id: Invoice case ID
        correlation_id: Correlation ID
        document_hash: SHA-256 hash of the document

    Returns:
        Created EventReceipt record
    """
    receipt_data = {
        "id": event_id,
        "invoice_id": invoice_id,
        "correlation_id": correlation_id,
        "payload_hash": document_hash,
        "receipt_status": "RECEIVED",
        "received_at": datetime.now().isoformat(),
    }

    receipt = db.express_sync.create("EventReceipt", receipt_data)
    return receipt


def create_invoice_case(
    case_id: str,
    correlation_id: str,
    event_id: str,
    file_path: str,
    document_hash: str,
) -> Dict[str, Any]:
    """
    Create an InvoiceCase record.

    Args:
        case_id: Invoice case ID
        correlation_id: Correlation ID
        event_id: Event ID
        file_path: Path to the PDF
        document_hash: SHA-256 hash

    Returns:
        Created InvoiceCase record
    """
    case_data = {
        "id": case_id,
        "correlation_id": correlation_id,
        "event_id": event_id,
        "document_path": file_path,
        "document_hash": document_hash,
        "case_status": "RECEIVED",
        "current_owner": "finance-orchestrator",
    }

    case = db.express_sync.create("InvoiceCase", case_data)
    return case


def create_business_audit_event(
    audit_id: str,
    invoice_id: str,
    correlation_id: str,
    document_hash: str,
    filename: str,
    file_size: int,
) -> Dict[str, Any]:
    """
    Create a BusinessAuditEvent for immutable audit trail.

    Args:
        audit_id: Audit event ID
        invoice_id: Invoice case ID
        correlation_id: Correlation ID
        document_hash: SHA-256 hash
        filename: Original filename
        file_size: File size in bytes

    Returns:
        Created BusinessAuditEvent record
    """
    now = datetime.now().isoformat()

    # Create sanitized event payload
    event_payload = {
        "invoice_id": invoice_id,
        "correlation_id": correlation_id,
        "filename": filename,
        "file_size": file_size,
        "document_hash": document_hash,
        "received_at": now,
    }

    payload_json = json.dumps(event_payload, sort_keys=True)

    # Calculate event hash - provides individual event integrity.
    # previous_hash will be empty at Chapter 3; linked audit chain belongs to Chapter 7.
    event_hash = hashlib.sha256(payload_json.encode()).hexdigest()

    audit_data = {
        "id": audit_id,
        "invoice_id": invoice_id,
        "correlation_id": correlation_id,
        "actor_id": "finance-orchestrator",
        "action_type": "INVOICE_RECEIVED",
        "action_outcome": "SUCCESS",
        "event_payload": payload_json,
        "event_timestamp": now,
        "event_hash": event_hash,
        "previous_hash": "",  # Linked chain belongs to Chapter 7
    }

    audit_event = db.express_sync.create("BusinessAuditEvent", audit_data)
    return audit_event


def build_intake_workflow() -> tuple[Any, str]:
    """
    Build the intake validation workflow using Kailash WorkflowBuilder.

    Creates a deterministic workflow with a single PythonCodeNode that validates
    intake metadata (file type, size, hash).

    Returns:
        Tuple of (Kailash Workflow object, stable node_id="validate_intake")
    """
    builder = WorkflowBuilder()

    # Add validation node with stable, deterministic ID for governance traceability
    node_id = builder.add_node(
        "PythonCodeNode",
        "validate_intake",
        {
            "function": validate_intake,
            "input_types": {
                "case_id": str,
                "correlation_id": str,
                "event_id": str,
                "extension": str,
                "file_size": int,
                "document_hash": str,
            },
            "output_type": dict,
            "sandbox_mode": "restricted",
            "description": "Validate invoice intake metadata",
        },
    )

    # Build and return the workflow with the stable node ID
    workflow = builder.build(workflow_id="intake_workflow")
    return workflow, node_id


def run_intake(
    file_path: str,
    event_id: Optional[str] = None,
    case_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute controlled invoice intake workflow through Kailash.

    Registers a PDF invoice as a finance case with event deduplication.

    Args:
        file_path: Path to the PDF invoice
        event_id: Optional event ID (derived from hash if not provided)
        case_id: Optional case ID (generated if not provided)
        correlation_id: Optional correlation ID (generated if not provided)

    Returns:
        Dictionary with intake result:
        {
            "intake_status": "ACCEPTED" | "REPLAY_IGNORED" | "REJECTED" | "SUSPENDED",
            "case_id": str,
            "event_id": str,
            "correlation_id": str,
            "invoice_id": str,  # Same as case_id
            "document_hash": str,
            "workflow_run_id": str,  # Real Kailash workflow execution ID
            "errors": list[str],
        }
    """
    path = Path(file_path)

    # Generate IDs if not provided
    if not case_id:
        case_id = generate_case_id()
    if not correlation_id:
        correlation_id = generate_correlation_id()

    # Check kill switch before processing
    kill_switch_service = KillSwitchService()
    if not kill_switch_service.is_processing_enabled_sync():
        return {
            "intake_status": "SUSPENDED",
            "case_id": case_id,
            "event_id": event_id or "",
            "correlation_id": correlation_id,
            "invoice_id": case_id,
            "document_hash": "",
            "workflow_run_id": "",
            "errors": ["Processing is suspended by kill switch"],
        }

    # Calculate document hash (application-controlled operation)
    try:
        document_hash = calculate_document_hash(file_path)
    except FileNotFoundError:
        return {
            "intake_status": "REJECTED",
            "case_id": case_id,
            "event_id": event_id or "",
            "correlation_id": correlation_id,
            "invoice_id": case_id,
            "document_hash": "",
            "workflow_run_id": "",
            "errors": [f"File not found: {file_path}"],
        }

    # Derive event ID from hash if not provided
    if not event_id:
        event_id = derive_event_id_from_hash(document_hash)

    # Check for replay
    existing_receipt = check_event_replay(event_id)
    if existing_receipt:
        existing_invoice = get_existing_invoice(event_id)
        return {
            "intake_status": "REPLAY_IGNORED",
            "case_id": existing_invoice.get("id", case_id) if existing_invoice else case_id,
            "event_id": event_id,
            "correlation_id": existing_invoice.get("correlation_id", correlation_id) if existing_invoice else correlation_id,
            "invoice_id": existing_invoice.get("id", case_id) if existing_invoice else case_id,
            "document_hash": document_hash,
            "workflow_run_id": None,
            "errors": ["Event already processed"],
        }

    # Get file metadata
    filename = path.name
    extension = path.suffix
    file_size = path.stat().st_size

    # Build and execute validation workflow through Kailash
    workflow, node_id = build_intake_workflow()

    try:
        with LocalRuntime() as runtime:
            # Execute workflow with node parameters
            # File path and filename are application-controlled (verified before workflow)
            result, workflow_run_id = runtime.execute(
                workflow,
                parameters={
                    node_id: {
                        "case_id": case_id,
                        "correlation_id": correlation_id,
                        "event_id": event_id,
                        "extension": extension,
                        "file_size": file_size,
                        "document_hash": document_hash,
                    }
                },
            )

            # Extract validation result from workflow node output
            # Result structure: {node_id: {'result': validation_dict}} or {node_id: {'error': ...}}
            node_output = result.get(node_id, {})
            validation_result = node_output.get("result", node_output)

    except Exception as e:
        return {
            "intake_status": "REJECTED",
            "case_id": case_id,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "invoice_id": case_id,
            "document_hash": document_hash,
            "workflow_run_id": "",
            "errors": [f"Workflow execution error: {str(e)}"],
        }

    # Check validation result from executed workflow node
    if not validation_result.get("valid", False):
        return {
            "intake_status": "REJECTED",
            "case_id": case_id,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "invoice_id": case_id,
            "document_hash": document_hash,
            "workflow_run_id": workflow_run_id,
            "errors": validation_result.get("errors", ["Validation failed"]),
        }

    # Create database records for new event only after successful workflow execution
    try:
        # Create invoice case
        create_invoice_case(
            case_id=case_id,
            correlation_id=correlation_id,
            event_id=event_id,
            file_path=file_path,
            document_hash=document_hash,
        )

        # Create event receipt (deduplication marker)
        create_event_receipt(
            event_id=event_id,
            invoice_id=case_id,
            correlation_id=correlation_id,
            document_hash=document_hash,
        )

        # Create business audit event
        audit_id = generate_audit_id()
        create_business_audit_event(
            audit_id=audit_id,
            invoice_id=case_id,
            correlation_id=correlation_id,
            document_hash=document_hash,
            filename=filename,
            file_size=file_size,
        )

        return {
            "intake_status": "ACCEPTED",
            "case_id": case_id,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "invoice_id": case_id,
            "document_hash": document_hash,
            "workflow_run_id": workflow_run_id,
            "errors": [],
        }

    except Exception as e:
        return {
            "intake_status": "REJECTED",
            "case_id": case_id,
            "event_id": event_id,
            "correlation_id": correlation_id,
            "invoice_id": case_id,
            "document_hash": document_hash,
            "workflow_run_id": workflow_run_id,
            "errors": [f"Database error: {str(e)}"],
        }


def validate_intake(
    case_id: str,
    correlation_id: str,
    event_id: str,
    extension: str,
    file_size: int,
    document_hash: str,
) -> Dict[str, Any]:
    """
    Validate intake metadata (deterministic, no LLM).

    Validates file type, size, and document hash. File existence and filename
    are checked by the application before calling the workflow.
    """
    errors = []

    # Reject non-PDF files
    if extension.lower() != ".pdf":
        errors.append(f"Only PDF files accepted, got: {extension}")

    # Reject empty files
    if file_size == 0:
        errors.append("Empty files not accepted")

    # Reject missing document hash
    if not document_hash:
        errors.append("Missing document hash")

    # Reject missing event ID
    if not event_id:
        errors.append("Missing event ID")

    if errors:
        return {
            "valid": False,
            "errors": errors,
            "case_id": case_id,
            "correlation_id": correlation_id,
            "event_id": event_id,
        }

    return {
        "valid": True,
        "errors": [],
        "case_id": case_id,
        "correlation_id": correlation_id,
        "event_id": event_id,
        "extension": extension,
        "file_size": file_size,
        "document_hash": document_hash,
    }
