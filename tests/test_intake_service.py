"""
Tests for invoice intake service.

Uses isolated temporary SQLite database for test isolation.
"""

import json
from pathlib import Path

import pytest


@pytest.fixture
def sample_pdf_path():
    """Provide path to sample PDF."""
    project_root = Path(__file__).resolve().parents[1]
    return str(project_root / "sample_invoices" / "01_Veson_Bunker_Clean_STP.pdf")


@pytest.fixture
def empty_pdf_path(tmp_path):
    """Create an empty PDF file for testing."""
    empty_pdf = tmp_path / "empty.pdf"
    empty_pdf.write_bytes(b"")
    return str(empty_pdf)


@pytest.fixture
def non_pdf_path(tmp_path):
    """Create a non-PDF file for testing."""
    non_pdf = tmp_path / "document.txt"
    non_pdf.write_text("This is not a PDF")
    return str(non_pdf)


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    """Set up isolated test database."""
    from dataflow import DataFlow
    import src.database

    # Create new DataFlow instance pointing to test database
    db_path = tmp_path / "test_finance.db"
    test_db = DataFlow(f"sqlite:///{db_path}")

    # Register all models from the database module
    # Import model classes to trigger registration
    from src.database import (
        InvoiceCase,
        FinanceDecision,
        ApprovalRequest,
        ExceptionCase,
        PostingRecord,
        EventReceipt,
        BusinessAuditEvent,
    )

    # Re-register models with test database
    @test_db.model
    class InvoiceCase:
        id: str
        correlation_id: str
        event_id: str
        document_path: str
        document_hash: str
        supplier_name: str = ""
        invoice_number: str = ""
        legal_entity: str = ""
        invoice_date: str = ""
        currency: str = ""
        gross_amount: float = 0.0
        extraction_confidence: float = 0.0
        target_system: str = ""
        accounting_code: str = ""
        case_status: str = "RECEIVED"
        duplicate_of: str = ""
        current_owner: str = "finance-orchestrator"

    @test_db.model
    class FinanceDecision:
        id: str
        invoice_id: str
        correlation_id: str
        decision_type: str
        recommended_value: str
        confidence: float = 0.0
        rationale: str = ""
        evidence: str = ""
        agent_id: str = ""
        policy_outcome: str = "PENDING"
        decision_timestamp: str = ""

    @test_db.model
    class ApprovalRequest:
        id: str
        invoice_id: str
        correlation_id: str
        requested_by: str
        requested_from: str
        approval_reason: str
        approval_status: str = "PENDING"
        approver_id: str = ""
        approver_comment: str = ""
        requested_at: str = ""
        decided_at: str = ""

    @test_db.model
    class ExceptionCase:
        id: str
        invoice_id: str
        correlation_id: str
        reason_code: str
        reason_detail: str
        recommended_action: str
        owner_id: str
        exception_status: str = "OPEN"
        opened_at: str = ""
        resolved_at: str = ""

    @test_db.model
    class PostingRecord:
        id: str
        invoice_id: str
        correlation_id: str
        target_system: str
        accounting_code: str
        posting_status: str
        posting_reference: str = ""
        approved_by: str = ""
        posted_by_agent: str = ""
        posted_at: str = ""

    @test_db.model
    class EventReceipt:
        id: str
        invoice_id: str
        correlation_id: str
        payload_hash: str
        receipt_status: str
        received_at: str

    @test_db.model
    class BusinessAuditEvent:
        id: str
        invoice_id: str
        correlation_id: str
        actor_id: str
        action_type: str
        action_outcome: str
        event_payload: str = "{}"
        previous_hash: str = ""
        event_hash: str = ""
        event_timestamp: str = ""

    @test_db.model
    class SystemControlState:
        id: str
        control_name: str
        is_enabled: bool
        enabled_by: str
        enabled_at: str
        disabled_by: str = ""
        disabled_at: str = ""
        reason: str = ""
        last_modified_at: str = ""

    test_db.create_tables_sync()

    # Initialize kill switch to be enabled
    test_db.express_sync.create(
        "SystemControlState",
        {
            "id": "KILL_SWITCH_001",
            "control_name": "processing_enabled",
            "is_enabled": True,
            "enabled_by": "test-setup",
            "enabled_at": "2026-09-11T00:00:00Z",
            "last_modified_at": "2026-09-11T00:00:00Z",
        },
    )

    # Replace the db in the intake_service module
    import src.intake_service
    import src.kill_switch_service
    monkeypatch.setattr(src.intake_service, "db", test_db)
    monkeypatch.setattr(src.kill_switch_service, "db", test_db)

    yield test_db


def test_valid_pdf_accepted(sample_pdf_path, test_database):
    """A valid PDF is accepted and executed through workflow."""
    from src.intake_service import run_intake

    result = run_intake(sample_pdf_path)

    assert result["intake_status"] == "ACCEPTED"
    assert result["case_id"].startswith("INV-")
    assert result["event_id"].startswith("EVT-")
    assert result["correlation_id"].startswith("CORR-")
    assert result["invoice_id"] == result["case_id"]
    assert result["document_hash"]
    assert result["errors"] == []
    # Verify real workflow execution
    assert result["workflow_run_id"], "workflow_run_id must be non-empty for ACCEPTED intake"
    assert len(result["workflow_run_id"]) > 0


def test_missing_file_rejected(test_database):
    """A missing file is rejected."""
    from src.intake_service import run_intake

    result = run_intake("/nonexistent/path/invoice.pdf")

    assert result["intake_status"] == "REJECTED"
    assert "File not found" in result["errors"][0]


def test_non_pdf_rejected(non_pdf_path, test_database):
    """A non-PDF file is rejected through the executed workflow."""
    from src.intake_service import run_intake

    result = run_intake(non_pdf_path)

    assert result["intake_status"] == "REJECTED"
    assert "Only PDF files accepted" in result["errors"][0]
    # Non-PDF rejection should come from workflow execution
    assert result["workflow_run_id"], "workflow_run_id must be set for validation errors from workflow"


def test_empty_pdf_rejected(empty_pdf_path, test_database):
    """An empty PDF is rejected through the executed workflow."""
    from src.intake_service import run_intake

    result = run_intake(empty_pdf_path)

    assert result["intake_status"] == "REJECTED"
    assert "Empty files not accepted" in result["errors"][0]
    # Empty PDF rejection should come from workflow execution
    assert result["workflow_run_id"], "workflow_run_id must be set for validation errors from workflow"


def test_replay_ignored(sample_pdf_path, test_database):
    """Replaying the same event does not create a second case or run workflow."""
    from src.intake_service import run_intake

    # First intake
    result1 = run_intake(sample_pdf_path)
    assert result1["intake_status"] == "ACCEPTED"
    original_workflow_run_id = result1["workflow_run_id"]

    # Replay with same file
    result2 = run_intake(sample_pdf_path)
    assert result2["intake_status"] == "REPLAY_IGNORED"
    assert result2["case_id"] == result1["case_id"]
    assert result2["event_id"] == result1["event_id"]
    assert result2["correlation_id"] == result1["correlation_id"]
    assert "Event already processed" in result2["errors"]
    # Replay should not execute workflow again
    assert result2["workflow_run_id"] is None, "REPLAY_IGNORED should not have workflow_run_id"

    # Verify only one case exists
    cases = test_database.express_sync.list("InvoiceCase")
    assert len(cases) == 1


def test_id_prefixes():
    """Generated IDs have required prefixes."""
    from src.intake_service import (
        generate_audit_id,
        generate_case_id,
        generate_correlation_id,
        generate_event_id,
    )

    assert generate_case_id().startswith("INV-")
    assert generate_correlation_id().startswith("CORR-")
    assert generate_event_id().startswith("EVT-")
    assert generate_audit_id().startswith("AUD-")


def test_audit_event_created(sample_pdf_path, test_database):
    """Audit event is created for accepted invoice."""
    from src.intake_service import run_intake

    result = run_intake(sample_pdf_path)

    assert result["intake_status"] == "ACCEPTED"

    # Verify audit event exists
    audits = test_database.express_sync.list("BusinessAuditEvent")
    assert len(audits) == 1

    audit = audits[0]
    assert audit["action_type"] == "INVOICE_RECEIVED"
    assert audit["action_outcome"] == "SUCCESS"
    assert audit["actor_id"] == "finance-orchestrator"
    assert audit["invoice_id"] == result["case_id"]
    assert audit["correlation_id"] == result["correlation_id"]

    # Verify payload is valid JSON
    payload = json.loads(audit["event_payload"])
    assert payload["invoice_id"] == result["case_id"]
    assert payload["document_hash"] == result["document_hash"]


def test_document_hash_calculation(sample_pdf_path):
    """Document hash is calculated correctly."""
    from src.intake_service import calculate_document_hash

    hash1 = calculate_document_hash(sample_pdf_path)
    hash2 = calculate_document_hash(sample_pdf_path)

    # Same file should produce same hash
    assert hash1 == hash2
    # Hash should be valid hex
    assert len(hash1) == 64
    assert all(c in "0123456789abcdef" for c in hash1)


def test_event_id_from_hash():
    """Event ID is derived deterministically from hash."""
    from src.intake_service import derive_event_id_from_hash

    hash1 = "b970e86bb68e614168596a5e966c82298d707421bd40bb3e01b43a87fc99273f"
    event1 = derive_event_id_from_hash(hash1)

    # Same hash should produce same event ID
    event2 = derive_event_id_from_hash(hash1)
    assert event1 == event2

    # Event ID should have correct prefix
    assert event1.startswith("EVT-")
    # Event ID should use first 12 chars of hash
    assert event1 == "EVT-B970E86BB68E"


def test_database_records_created(sample_pdf_path, test_database):
    """All required database records are created."""
    from src.intake_service import run_intake

    result = run_intake(sample_pdf_path)
    assert result["intake_status"] == "ACCEPTED"

    # Check InvoiceCase
    cases = test_database.express_sync.list("InvoiceCase")
    assert len(cases) == 1
    case = cases[0]
    assert case["id"] == result["case_id"]
    assert case["event_id"] == result["event_id"]
    assert case["correlation_id"] == result["correlation_id"]
    assert case["case_status"] == "RECEIVED"
    assert case["document_hash"] == result["document_hash"]

    # Check EventReceipt
    receipts = test_database.express_sync.list("EventReceipt")
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["id"] == result["event_id"]
    assert receipt["invoice_id"] == result["case_id"]
    assert receipt["receipt_status"] == "RECEIVED"

    # Check BusinessAuditEvent
    audits = test_database.express_sync.list("BusinessAuditEvent")
    assert len(audits) == 1
    audit = audits[0]
    assert audit["invoice_id"] == result["case_id"]
    assert audit["action_type"] == "INVOICE_RECEIVED"


def test_deterministic_event_id(sample_pdf_path, test_database):
    """Same file produces same event ID (deterministic)."""
    from src.intake_service import run_intake

    result1 = run_intake(sample_pdf_path)
    event_id_1 = result1["event_id"]

    # Simulate replay
    result2 = run_intake(sample_pdf_path)
    event_id_2 = result2["event_id"]

    assert event_id_1 == event_id_2


def test_custom_ids(sample_pdf_path, test_database):
    """Custom IDs are respected."""
    from src.intake_service import run_intake

    custom_case_id = "INV-CUSTOM01"
    custom_correlation_id = "CORR-CUSTOM01"
    custom_event_id = "EVT-CUSTOM01"

    result = run_intake(
        sample_pdf_path,
        event_id=custom_event_id,
        case_id=custom_case_id,
        correlation_id=custom_correlation_id,
    )

    assert result["case_id"] == custom_case_id
    assert result["event_id"] == custom_event_id
    assert result["correlation_id"] == custom_correlation_id


def test_event_receipt_prevents_duplicate(sample_pdf_path, test_database):
    """EventReceipt correctly prevents duplicate processing."""
    from src.intake_service import run_intake

    result1 = run_intake(sample_pdf_path)
    assert result1["intake_status"] == "ACCEPTED"

    # Verify EventReceipt exists
    receipts = test_database.express_sync.list("EventReceipt")
    assert len(receipts) == 1

    # Replay should not create new receipt
    result2 = run_intake(sample_pdf_path)
    assert result2["intake_status"] == "REPLAY_IGNORED"

    receipts = test_database.express_sync.list("EventReceipt")
    assert len(receipts) == 1  # Still only 1


def test_stable_workflow_node_id(sample_pdf_path, test_database):
    """Workflow uses stable 'validate_intake' node ID for governance traceability."""
    from src.intake_service import run_intake, build_intake_workflow
    from kailash import LocalRuntime

    # Verify stable node ID
    workflow, node_id = build_intake_workflow()
    assert node_id == "validate_intake", "Node ID must be exactly 'validate_intake'"

    # Verify node ID is used in workflow execution
    result = run_intake(sample_pdf_path)
    assert result["intake_status"] == "ACCEPTED"
    assert result["workflow_run_id"], "workflow_run_id must be present"

    # Verify the node executes with stable ID in Kailash
    with LocalRuntime() as runtime:
        exec_result, exec_run_id = runtime.execute(
            workflow,
            parameters={
                "validate_intake": {
                    "case_id": "INV-TEST",
                    "correlation_id": "CORR-TEST",
                    "event_id": "EVT-TEST2",
                    "extension": ".pdf",
                    "file_size": 1000,
                    "document_hash": "abc123",
                }
            },
        )
        # Verify result uses stable node ID
        assert "validate_intake" in exec_result, "Result must contain 'validate_intake' key"
        assert exec_run_id, "workflow_run_id must be non-empty"
