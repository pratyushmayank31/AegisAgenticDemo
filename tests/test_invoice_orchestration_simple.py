"""
Simple focused tests for Step 8A: Governed End-to-End Invoice Pipeline.

Tests core functionality with governance verdicts:
1. Kill-switch enforcement (fail-closed)
2. Replay protection (ignore duplicate submissions)
3. Governance verdicts (ALLOW/HOLD/DENY)
4. Audit trail with 64-char hashes
5. NO PostingRecords created in Step 8A
"""

import pytest
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from src.database import DataFlow
from src.invoice_orchestration_service import InvoiceOrchestrationService
from src.kill_switch_service import KillSwitchService, OperationalRole


@pytest.fixture
def test_db(tmp_path):
    """Create temporary test database."""
    test_db_path = tmp_path / "test_simple_orchestration.db"
    test_db_url = f"sqlite:///{test_db_path}"
    db = DataFlow(test_db_url)

    @db.model
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
        current_owner: str = "orchestrator"

    @db.model
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

    @db.model
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

    @db.model
    class ExceptionCase:
        id: str
        invoice_id: str
        correlation_id: str
        reason_code: str
        reason_detail: str = ""
        recommended_action: str = ""
        owner_id: str = ""
        required_authority: str = ""
        exception_status: str = "OPEN"
        opened_at: str = ""
        resolved_at: str = ""

    @db.model
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

    @db.model
    class EventReceipt:
        id: str
        invoice_id: str
        correlation_id: str
        payload_hash: str
        receipt_status: str
        received_at: str

    @db.model
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

    @db.model
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

    db.create_tables_sync()
    yield db


@pytest.fixture
def orchestration_service(test_db, monkeypatch):
    """Create InvoiceOrchestrationService with test database (demo mode, no Aegis)."""
    monkeypatch.setattr("src.invoice_orchestration_service.db", test_db)
    monkeypatch.setattr("src.kill_switch_service.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)
    # No AegisGovernanceAdapter - demo mode uses fallback
    return InvoiceOrchestrationService()


@pytest.fixture
def sample_invoice_file(tmp_path):
    """Create a sample invoice PDF file for testing."""
    sample_file = tmp_path / "sample_invoice.pdf"
    sample_file.write_bytes(b"PDF mock content for testing orchestration")
    return str(sample_file)


@pytest.mark.asyncio
async def test_kill_switch_blocks_orchestration(orchestration_service, test_db, sample_invoice_file, monkeypatch):
    """Test that engaged kill-switch blocks orchestration (fail-closed)."""
    monkeypatch.setattr("src.invoice_orchestration_service.db", test_db)

    kill_switch = KillSwitchService()
    await kill_switch.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing orchestration kill-switch block",
    )

    result = await orchestration_service.orchestrate_invoice(
        sample_invoice_file,
        actor_id="processor@finance.corp",
        actor_role="OPERATOR",
    )

    assert result.governance_status == "HOLD"
    assert result.reason_code == "KILL_SWITCH_ENGAGED"
    assert "Processing is suspended by kill switch" in result.error_message
    print(f"✓ Kill-switch blocks orchestration (fail-closed): {result.reason_code}")


@pytest.mark.asyncio
async def test_replay_protection_ignored(orchestration_service, test_db, sample_invoice_file):
    """Test that replayed invoices are ignored without downstream processing."""
    # First submission
    result1 = await orchestration_service.orchestrate_invoice(
        sample_invoice_file,
        actor_id="processor@finance.corp",
        actor_role="OPERATOR",
    )

    assert result1.case_id != ""
    case_id_1 = result1.case_id

    # Replay same file
    result2 = await orchestration_service.orchestrate_invoice(
        sample_invoice_file,
        actor_id="processor@finance.corp",
        actor_role="OPERATOR",
    )

    assert result2.governance_status == "HOLD"
    assert result2.reason_code == "REPLAY_DETECTED"
    assert result2.case_id == case_id_1  # Same case
    assert "no downstream processing" in result2.explanation.lower()
    print(f"✓ Replay protection active: duplicate ignored")


@pytest.mark.asyncio
async def test_orchestration_demo_mode_returns_allow(orchestration_service, test_db, sample_invoice_file):
    """Test that orchestration in demo mode returns ALLOW verdict."""
    result = await orchestration_service.orchestrate_invoice(
        sample_invoice_file,
        actor_id="processor@finance.corp",
        actor_role="OPERATOR",
    )

    assert result.case_id != ""
    assert result.correlation_id != ""
    assert result.governance_status == "ALLOW"  # Demo mode fallback
    assert result.reason_code == "DEMO_MODE_NO_GOVERNANCE"
    assert result.workflow_run_id != ""
    print(f"✓ Demo mode orchestration: governance_status={result.governance_status}")


@pytest.mark.asyncio
async def test_no_posting_records_created_in_step_8a(orchestration_service, test_db, sample_invoice_file):
    """Test that Step 8A does NOT create PostingRecords (critical requirement)."""
    result = await orchestration_service.orchestrate_invoice(
        sample_invoice_file,
        actor_id="processor@finance.corp",
        actor_role="OPERATOR",
    )

    # Verify result is successful
    assert result.case_id != ""

    # Verify NO PostingRecords were created
    postings = test_db.express_sync.list("PostingRecord")
    assert len(postings) == 0, f"Step 8A should NOT create PostingRecords. Found: {len(postings)}"
    print(f"✓ Step 8A produces ZERO PostingRecords (correct)")


@pytest.mark.asyncio
async def test_orchestration_creates_audit_events(orchestration_service, test_db, sample_invoice_file):
    """Test that orchestration creates audit events with 64-char hashes."""
    result = await orchestration_service.orchestrate_invoice(
        sample_invoice_file,
        actor_id="processor@finance.corp",
        actor_role="OPERATOR",
    )

    audits = test_db.express_sync.list("BusinessAuditEvent")
    assert len(audits) > 0
    print(f"✓ Created {len(audits)} audit events")

    for audit in audits:
        assert len(audit["event_hash"]) == 64
        assert all(c in "0123456789abcdef" for c in audit["event_hash"])
        print(f"  ✓ Audit event {audit['action_type']}: {audit['event_hash'][:16]}...")
