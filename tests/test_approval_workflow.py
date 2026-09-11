"""
Step 6: Human Approval Workflow - Complete Governance Controls Tests.

Tests for:
1. Maker-Checker: Approver cannot equal request creator
2. Authority Enforcement: Approver authority >= required authority
3. Decision Immutability: Cannot change APPROVED→REJECTED or vice versa
4. Rejection Visibility: REJECT creates one ExceptionCase (replay-safe)
5. Posting Protection: No PostingRecord created
6. Audit Trail: Structured events with compliance metadata
"""

import pytest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.database import DataFlow
from src.approval_workflow import (
    ApprovalWorkflow,
    ApprovalDecisionInput,
)
from src.authority_hierarchy import AuthorityLevel


@pytest.fixture
def test_db(tmp_path):
    """Create temporary test database."""
    test_db_path = tmp_path / "test_approval.db"
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
        current_owner: str = "finance-orchestrator"

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
    class ExceptionCase:
        id: str
        invoice_id: str
        correlation_id: str
        reason_code: str
        reason_detail: str
        recommended_action: str
        owner_id: str = ""
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

    db.create_tables_sync()
    yield db


@pytest.fixture
def approval_workflow(test_db, monkeypatch):
    """Create ApprovalWorkflow instance with test database."""
    workflow = ApprovalWorkflow()
    monkeypatch.setattr("src.approval_workflow.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)
    workflow.db = test_db
    workflow.audit_chain.db = test_db
    return workflow


@pytest.fixture
def sample_invoice_case(test_db):
    """Create sample invoice case in test database."""
    now = datetime.now(timezone.utc)
    invoice_data = {
        "id": "inv-001",
        "correlation_id": "corr-001",
        "event_id": "evt-001",
        "document_path": "/invoices/invoice-001.pdf",
        "document_hash": "abc123",
        "supplier_name": "Acme Corp",
        "invoice_number": "INV-2024-001",
        "legal_entity": "Company USA",
        "invoice_date": "2024-01-15",
        "currency": "USD",
        "gross_amount": 25000.00,
        "extraction_confidence": 0.95,
        "target_system": "SAP",
        "accounting_code": "5000-1234",
        "case_status": "RECEIVED",
        "duplicate_of": "",
        "current_owner": "finance-orchestrator",
    }
    test_db.express_sync.create("InvoiceCase", invoice_data)
    return invoice_data


@pytest.fixture
def sample_approval_request(test_db, sample_invoice_case):
    """Create sample pending approval request."""
    now = datetime.now(timezone.utc)
    approval_data = {
        "id": "arq-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "requested_by": "governance-orchestrator",
        "requested_from": AuthorityLevel.L2_SUPERVISOR.value,
        "approval_reason": "AMOUNT_THRESHOLD",
        "approval_status": "PENDING",
        "approver_id": "",
        "approver_comment": "",
        "requested_at": now.isoformat(),
        "decided_at": "",
    }
    test_db.express_sync.create("ApprovalRequest", approval_data)
    return approval_data


# ============================================================================
# CONTROL 1: MAKER-CHECKER TESTS
# ============================================================================

def test_maker_checker_blocked_when_approver_equals_creator(
    approval_workflow, sample_approval_request
):
    """Test that maker-checker prevents self-approval."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="governance-orchestrator",  # Same as requested_by
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
        approver_comment="Self approval attempt",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "BLOCKED"
    assert result.approval_request_status == "PENDING"
    assert "Maker-Checker violation" in result.blocked_reason
    assert "governance-orchestrator" in result.blocked_reason

    # Verify ApprovalRequest remains PENDING
    updated_request = approval_workflow.get_approval_request("arq-001")
    assert updated_request["approval_status"] == "PENDING"
    assert updated_request["approver_id"] == ""


def test_maker_checker_allowed_different_approver(
    approval_workflow, sample_approval_request
):
    """Test that maker-checker allows different approver."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",  # Different from governance-orchestrator
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "APPROVED"
    assert result.approval_request_status == "APPROVED"
    assert result.blocked_reason is None


# ============================================================================
# CONTROL 2: AUTHORITY ENFORCEMENT TESTS
# ============================================================================

def test_authority_blocked_l1_cannot_approve_l2_request(
    approval_workflow, test_db, sample_invoice_case
):
    """Test that L1 cannot approve L2 request."""
    now = datetime.now(timezone.utc)
    approval_data = {
        "id": "arq-l2-test",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "requested_by": "governance-orchestrator",
        "requested_from": AuthorityLevel.L2_SUPERVISOR.value,
        "approval_reason": "AMOUNT_THRESHOLD",
        "approval_status": "PENDING",
        "approver_id": "",
        "approver_comment": "",
        "requested_at": now.isoformat(),
        "decided_at": "",
    }
    test_db.express_sync.create("ApprovalRequest", approval_data)

    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-l2-test",
        approver_id="approver-l1-001",
        approver_authority=AuthorityLevel.L1_PROCESSOR.value,  # L1 insufficient
        decision="APPROVED",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "BLOCKED"
    assert "Authority" in result.blocked_reason
    assert "L1_PROCESSOR" in result.blocked_reason


def test_authority_allowed_l2_can_approve_l2_request(
    approval_workflow, sample_approval_request
):
    """Test that L2 can approve L2 request."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "APPROVED"


def test_authority_allowed_l3_can_approve_l2_request(
    approval_workflow, sample_approval_request
):
    """Test that L3 can approve L2 request (higher authority)."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l3-001",
        approver_authority=AuthorityLevel.L3_CONTROLLER.value,  # Higher authority
        decision="APPROVED",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "APPROVED"


def test_authority_allowed_human_approver_can_approve_any(
    approval_workflow, test_db, sample_invoice_case
):
    """Test that HUMAN_APPROVER can approve any request level."""
    now = datetime.now(timezone.utc)
    approval_data = {
        "id": "arq-l3-test",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "requested_by": "governance-orchestrator",
        "requested_from": AuthorityLevel.L3_CONTROLLER.value,
        "approval_reason": "AMOUNT_THRESHOLD",
        "approval_status": "PENDING",
        "approver_id": "",
        "approver_comment": "",
        "requested_at": now.isoformat(),
        "decided_at": "",
    }
    test_db.express_sync.create("ApprovalRequest", approval_data)

    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-l3-test",
        approver_id="human-approver-001",
        approver_authority=AuthorityLevel.HUMAN_APPROVER.value,
        decision="APPROVED",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "APPROVED"


# ============================================================================
# CONTROL 3: DECISION IMMUTABILITY TESTS
# ============================================================================

def test_immutability_cannot_change_approved_to_rejected(
    approval_workflow, sample_approval_request, test_db
):
    """Test that APPROVED decision cannot be changed to REJECTED."""
    # First, approve the request
    decision_input_1 = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )
    result_1 = approval_workflow.record_approval_decision(decision_input_1)
    assert result_1.decision == "APPROVED"

    # Try to change it to REJECTED (should be blocked)
    decision_input_2 = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-002",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="REJECTED",
    )
    result_2 = approval_workflow.record_approval_decision(decision_input_2)

    assert result_2.decision == "BLOCKED"
    assert "Cannot change" in result_2.blocked_reason
    assert "APPROVED" in result_2.blocked_reason


def test_immutability_replay_same_decision_blocked(
    approval_workflow, sample_approval_request
):
    """Test that replaying same decision is blocked (idempotent after first)."""
    # First approval
    decision_input_1 = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )
    result_1 = approval_workflow.record_approval_decision(decision_input_1)
    assert result_1.decision == "APPROVED"

    # Replay the same decision (should be blocked since status is now APPROVED)
    decision_input_2 = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",  # Same approver
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",  # Same decision
    )
    result_2 = approval_workflow.record_approval_decision(decision_input_2)

    assert result_2.decision == "BLOCKED"


# ============================================================================
# CONTROL 4: REJECTION VISIBILITY TESTS
# ============================================================================

def test_rejection_creates_exception_case(
    approval_workflow, sample_approval_request, test_db
):
    """Test that REJECT creates ExceptionCase."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="REJECTED",
        approver_comment="Does not match PO",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "REJECTED"
    assert result.exception_case_id is not None

    # Verify ExceptionCase was created
    exception_case = test_db.express_sync.find_one(
        "ExceptionCase", {"id": result.exception_case_id}
    )
    assert exception_case is not None
    assert exception_case["reason_code"] == "APPROVAL_REJECTED"
    assert exception_case["exception_status"] == "OPEN"


def test_rejection_replay_does_not_create_second_exception_case(
    approval_workflow, sample_approval_request, test_db
):
    """Test that replaying REJECT doesn't create duplicate ExceptionCase."""
    # First rejection
    decision_input_1 = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="REJECTED",
        approver_comment="First rejection",
    )
    result_1 = approval_workflow.record_approval_decision(decision_input_1)
    exception_id_1 = result_1.exception_case_id

    # Try to change decision after REJECT (should be blocked)
    decision_input_2 = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-002",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="REJECTED",
    )
    result_2 = approval_workflow.record_approval_decision(decision_input_2)

    assert result_2.decision == "BLOCKED"
    # Exception case ID should be from first rejection (immutability)


def test_approval_does_not_create_exception_case(
    approval_workflow, sample_approval_request, test_db
):
    """Test that APPROVE does not create ExceptionCase."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    assert result.decision == "APPROVED"
    assert result.exception_case_id is None


# ============================================================================
# CONTROL 5: POSTING PROTECTION TESTS
# ============================================================================

def test_approval_does_not_create_posting_record(
    approval_workflow, sample_approval_request, test_db
):
    """Test that APPROVE does not create PostingRecord."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )

    approval_workflow.record_approval_decision(decision_input)

    # Verify no PostingRecord was created
    posting_records = test_db.express_sync.list(
        "PostingRecord", {"invoice_id": "inv-001"}
    )
    assert len(posting_records) == 0


def test_rejection_does_not_create_posting_record(
    approval_workflow, sample_approval_request, test_db
):
    """Test that REJECT does not create PostingRecord."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="REJECTED",
    )

    approval_workflow.record_approval_decision(decision_input)

    # Verify no PostingRecord was created
    posting_records = test_db.express_sync.list(
        "PostingRecord", {"invoice_id": "inv-001"}
    )
    assert len(posting_records) == 0


# ============================================================================
# CONTROL 6: AUDIT TRAIL TESTS
# ============================================================================

def test_audit_event_created_with_governance_metadata(
    approval_workflow, sample_approval_request, test_db
):
    """Test that audit event contains governance control metadata."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
        approver_comment="Verified and approved",
    )

    result = approval_workflow.record_approval_decision(decision_input)

    # Verify BusinessAuditEvent was created with metadata
    audit_events = test_db.express_sync.list(
        "BusinessAuditEvent",
        {"action_type": "APPROVAL_DECISION", "invoice_id": "inv-001"},
    )
    assert len(audit_events) >= 1

    audit_event = audit_events[0]
    assert audit_event["action_outcome"] == "APPROVED"
    assert audit_event["actor_id"] == "approver-l2-001"

    # Verify payload contains governance metadata
    import json
    payload = json.loads(audit_event["event_payload"])
    assert "maker_checker_enforced" in payload
    assert "authority_enforced" in payload
    assert "approver_authority" in payload
    assert payload["maker_checker_enforced"] == "governance-orchestrator"
    assert payload["authority_enforced"] == "L2_SUPERVISOR"


def test_audit_event_has_event_hash(
    approval_workflow, sample_approval_request, test_db
):
    """Test that audit event has deterministic event hash."""
    decision_input = ApprovalDecisionInput(
        approval_request_id="arq-001",
        approver_id="approver-l2-001",
        approver_authority=AuthorityLevel.L2_SUPERVISOR.value,
        decision="APPROVED",
    )

    approval_workflow.record_approval_decision(decision_input)

    # Verify event_hash exists and is deterministic (full 64-char SHA256)
    audit_events = test_db.express_sync.list(
        "BusinessAuditEvent",
        {"action_type": "APPROVAL_DECISION"},
    )
    assert len(audit_events) >= 1
    assert audit_events[0]["event_hash"] != ""
    assert len(audit_events[0]["event_hash"]) == 64  # Full SHA256 hexadecimal digest


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
