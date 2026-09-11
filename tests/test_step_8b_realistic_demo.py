"""
Step 8B Realistic Demonstration: Complete Invoice Processing with Corrected Posting.

Demonstrates proper authorization model:
- Direct ALLOW: FinanceDecision.policy_outcome == "ALLOW" → SIMULATED_POSTED
- Approved HOLD: FinanceDecision.policy_outcome == "HOLD" + ApprovalRequest.APPROVED → SIMULATED_POSTED
- Pending HOLD: FinanceDecision.policy_outcome == "HOLD" + ApprovalRequest.PENDING → BLOCKED
- DENY: FinanceDecision.policy_outcome == "DENY" → BLOCKED

Target systems: VESON_IMOS, SMARTPAL, ORACLE_FUSION (not SAP)
Posting references: SIM-{system}-{id} (clearly simulated, not real GL numbers)
"""

import pytest
from datetime import datetime, timezone

from src.database import DataFlow
from src.posting_service import PostingService, PostingInput
from src.intake_service import generate_case_id, generate_correlation_id


@pytest.fixture
def demo_db(tmp_path):
    """Create demo database for realistic scenarios."""
    test_db_path = tmp_path / "step_8b_demo.db"
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
        current_owner: str = ""

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


def test_demo_veson_imos_direct_allow(demo_db, monkeypatch):
    """
    Realistic Demo 1: VESON_IMOS Direct ALLOW.

    Simulates: Low-risk invoice with direct governance ALLOW → immediate posting
    """
    monkeypatch.setattr("src.posting_service.db", demo_db)
    monkeypatch.setattr("src.audit_chain_service.db", demo_db)
    monkeypatch.setattr("src.kill_switch_service.db", demo_db)

    case_id = generate_case_id()
    corr_id = generate_correlation_id()

    # 1. Create InvoiceCase (extraction complete)
    demo_db.express_sync.create(
        "InvoiceCase",
        {
            "id": case_id,
            "correlation_id": corr_id,
            "event_id": generate_case_id(),
            "document_path": "/samples/veson_invoice_001.pdf",
            "document_hash": "abc123def456veson",
            "supplier_name": "Veson Shipping Ltd",
            "invoice_number": "VES-2026-15847",
            "legal_entity": "Shipping Services AG",
            "invoice_date": "2026-09-10",
            "currency": "USD",
            "gross_amount": 45000.00,
            "extraction_confidence": 0.96,
            "target_system": "VESON_IMOS",
            "accounting_code": "4100-FREIGHT",
            "case_status": "EXTRACTED",
            "current_owner": "processor@corp",
        },
    )

    # 2. Create FinanceDecision with ALLOW (low-risk, known vendor)
    demo_db.express_sync.create(
        "FinanceDecision",
        {
            "id": generate_case_id(),
            "invoice_id": case_id,
            "correlation_id": corr_id,
            "decision_type": "ROUTING",
            "recommended_value": "VESON_IMOS",
            "confidence": 0.96,
            "rationale": "Known vendor, standard freight invoice",
            "evidence": '["KNOWN_VENDOR", "AMOUNT_IN_RANGE", "HIGH_CONFIDENCE"]',
            "agent_id": "finance_router_v1",
            "policy_outcome": "ALLOW",
            "decision_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 3. Post invoice
    service = PostingService()
    result = service.post_approved_invoice(
        PostingInput(
            invoice_id=case_id,
            correlation_id=corr_id,
            approver_id="approver@corp",
        )
    )

    # 4. Verify posting success
    assert result.posting_status == "SIMULATED_POSTED"
    assert result.posting_reference.startswith("SIM-VESON_IMOS-")
    assert result.workflow_run_id != ""
    assert "VESON_IMOS" in result.explanation

    # 5. Verify PostingRecord created
    postings = demo_db.express_sync.list("PostingRecord")
    assert len(postings) == 1
    posting = postings[0]
    assert posting["posting_status"] == "SIMULATED_POSTED"
    assert posting["approved_by"] == "approver@corp"
    assert posting["target_system"] == "VESON_IMOS"
    assert posting["accounting_code"] == "4100-FREIGHT"

    # 6. Verify InvoiceCase status updated to POSTED
    updated_case = demo_db.express_sync.find_one("InvoiceCase", {"id": case_id})
    assert updated_case["case_status"] == "POSTED"
    assert updated_case["current_owner"] == "approver@corp"

    # 7. Verify audit events
    audits = demo_db.express_sync.list("BusinessAuditEvent", {"invoice_id": case_id})
    audit_types = [a["action_type"] for a in audits]
    assert "POSTING_AUTHORIZED" in audit_types
    assert "POSTING_COMPLETED" in audit_types

    print(
        f"✓ Demo 1 Complete: VESON_IMOS {result.posting_reference} → POSTED "
        f"(Amount: ${posting['accounting_code']})"
    )


def test_demo_smartpal_approved_hold(demo_db, monkeypatch):
    """
    Realistic Demo 2: SMARTPAL with Human Approval (HOLD → APPROVED).

    Simulates: Medium-risk invoice requiring human approval before posting
    """
    monkeypatch.setattr("src.posting_service.db", demo_db)
    monkeypatch.setattr("src.audit_chain_service.db", demo_db)
    monkeypatch.setattr("src.kill_switch_service.db", demo_db)

    case_id = generate_case_id()
    corr_id = generate_correlation_id()

    # 1. Create InvoiceCase
    demo_db.express_sync.create(
        "InvoiceCase",
        {
            "id": case_id,
            "correlation_id": corr_id,
            "event_id": generate_case_id(),
            "document_path": "/samples/smartpal_invoice_042.pdf",
            "document_hash": "xyz789smartpal42",
            "supplier_name": "SmartPAL Logistics Inc",
            "invoice_number": "SPL-2026-98765",
            "legal_entity": "Logistics Global",
            "invoice_date": "2026-09-09",
            "currency": "EUR",
            "gross_amount": 125000.00,
            "extraction_confidence": 0.88,
            "target_system": "SMARTPAL",
            "accounting_code": "5200-LOGISTICS",
            "case_status": "EXTRACTED",
            "current_owner": "processor@corp",
        },
    )

    # 2. Create FinanceDecision with HOLD (needs review due to amount)
    demo_db.express_sync.create(
        "FinanceDecision",
        {
            "id": generate_case_id(),
            "invoice_id": case_id,
            "correlation_id": corr_id,
            "decision_type": "ROUTING",
            "recommended_value": "SMARTPAL",
            "confidence": 0.88,
            "rationale": "High amount requires management review",
            "evidence": '["HIGH_AMOUNT", "MEDIUM_CONFIDENCE", "KNOWN_VENDOR"]',
            "agent_id": "finance_router_v1",
            "policy_outcome": "HOLD",
            "decision_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 3. Create ApprovalRequest (initially PENDING)
    demo_db.express_sync.create(
        "ApprovalRequest",
        {
            "id": generate_case_id(),
            "invoice_id": case_id,
            "correlation_id": corr_id,
            "requested_by": "processor@corp",
            "requested_from": "L3_CONTROLLER",
            "approval_reason": "Invoice amount exceeds L2 threshold, requires controller approval",
            "approval_status": "APPROVED",  # Approved after human review
            "approver_id": "controller@corp",
            "approver_comment": "Reviewed and approved. Vendor is reputable. Amount justified.",
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "decided_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 4. Post invoice (now approved)
    service = PostingService()
    result = service.post_approved_invoice(
        PostingInput(
            invoice_id=case_id,
            correlation_id=corr_id,
            approver_id="controller@corp",
        )
    )

    # 5. Verify posting success
    assert result.posting_status == "SIMULATED_POSTED"
    assert result.posting_reference.startswith("SIM-SMARTPAL-")

    postings = demo_db.express_sync.list("PostingRecord")
    assert len(postings) == 1
    assert postings[0]["posting_status"] == "SIMULATED_POSTED"

    updated_case = demo_db.express_sync.find_one("InvoiceCase", {"id": case_id})
    assert updated_case["case_status"] == "POSTED"

    print(
        f"✓ Demo 2 Complete: SMARTPAL {result.posting_reference} → POSTED "
        f"(Approved by: controller@corp, Amount: €{125000:.2f})"
    )


def test_demo_oracle_fusion_pending_blocked(demo_db, monkeypatch):
    """
    Realistic Demo 3: ORACLE_FUSION with Pending Approval (BLOCKED).

    Simulates: Invoice requiring approval that is still PENDING → cannot post yet
    """
    monkeypatch.setattr("src.posting_service.db", demo_db)
    monkeypatch.setattr("src.kill_switch_service.db", demo_db)

    case_id = generate_case_id()
    corr_id = generate_correlation_id()

    # 1. Create InvoiceCase
    demo_db.express_sync.create(
        "InvoiceCase",
        {
            "id": case_id,
            "correlation_id": corr_id,
            "event_id": generate_case_id(),
            "document_path": "/samples/oracle_invoice_156.pdf",
            "document_hash": "ora888fusion156",
            "supplier_name": "Oracle Financial Services",
            "invoice_number": "ORA-FS-2026-555",
            "legal_entity": "Enterprise Finance",
            "invoice_date": "2026-09-08",
            "currency": "GBP",
            "gross_amount": 250000.00,
            "extraction_confidence": 0.92,
            "target_system": "ORACLE_FUSION",
            "accounting_code": "6300-CONSULTANCY",
            "case_status": "EXTRACTED",
            "current_owner": "processor@corp",
        },
    )

    # 2. Create FinanceDecision with HOLD
    demo_db.express_sync.create(
        "FinanceDecision",
        {
            "id": generate_case_id(),
            "invoice_id": case_id,
            "correlation_id": corr_id,
            "decision_type": "ROUTING",
            "recommended_value": "ORACLE_FUSION",
            "policy_outcome": "HOLD",
            "decision_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 3. Create ApprovalRequest (PENDING - not yet approved)
    demo_db.express_sync.create(
        "ApprovalRequest",
        {
            "id": generate_case_id(),
            "invoice_id": case_id,
            "correlation_id": corr_id,
            "requested_by": "processor@corp",
            "requested_from": "L4_EXECUTIVE",
            "approval_reason": "Very high amount and consultancy services require executive sign-off",
            "approval_status": "PENDING",  # Still awaiting approval
            "requested_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 4. Attempt to post (should be blocked)
    service = PostingService()
    result = service.post_approved_invoice(
        PostingInput(
            invoice_id=case_id,
            correlation_id=corr_id,
            approver_id="processor@corp",
        )
    )

    # 5. Verify posting is blocked
    assert result.posting_status == "BLOCKED"
    assert "Approval pending" in result.explanation

    postings = demo_db.express_sync.list("PostingRecord")
    assert len(postings) == 0  # No posting created

    updated_case = demo_db.express_sync.find_one("InvoiceCase", {"id": case_id})
    assert updated_case["case_status"] == "EXTRACTED"  # Status unchanged

    print(
        f"✓ Demo 3 Complete: ORACLE_FUSION BLOCKED (pending approval) "
        f"- Amount £{250000:.2f} awaiting executive approval"
    )


def test_demo_replay_idempotency(demo_db, monkeypatch):
    """
    Realistic Demo 4: Replay Detection (Idempotency).

    Simulates: Posting same invoice twice returns same PostingRecord ID (no duplicate)
    """
    monkeypatch.setattr("src.posting_service.db", demo_db)
    monkeypatch.setattr("src.audit_chain_service.db", demo_db)
    monkeypatch.setattr("src.kill_switch_service.db", demo_db)

    case_id = generate_case_id()
    corr_id = generate_correlation_id()

    # Setup invoice
    demo_db.express_sync.create(
        "InvoiceCase",
        {
            "id": case_id,
            "correlation_id": corr_id,
            "event_id": generate_case_id(),
            "document_path": "/samples/replay_test.pdf",
            "document_hash": "replay_hash_123",
            "supplier_name": "Test Vendor",
            "invoice_number": "RPL-2026-001",
            "gross_amount": 15000.00,
            "target_system": "VESON_IMOS",
            "accounting_code": "4100-FREIGHT",
            "case_status": "EXTRACTED",
        },
    )

    demo_db.express_sync.create(
        "FinanceDecision",
        {
            "id": generate_case_id(),
            "invoice_id": case_id,
            "correlation_id": corr_id,
            "decision_type": "ROUTING",
            "recommended_value": "VESON_IMOS",
            "policy_outcome": "ALLOW",
            "decision_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    service = PostingService()

    # First posting
    result1 = service.post_approved_invoice(
        PostingInput(
            invoice_id=case_id,
            correlation_id=corr_id,
            approver_id="approver@corp",
        )
    )
    assert result1.posting_status == "SIMULATED_POSTED"
    posting_id_1 = result1.posting_record_id
    ref_1 = result1.posting_reference

    # Second posting (replay)
    result2 = service.post_approved_invoice(
        PostingInput(
            invoice_id=case_id,
            correlation_id=corr_id,
            approver_id="approver@corp",
        )
    )

    # Verify replay ignored
    assert result2.posting_status == "REPLAY_IGNORED"
    assert result2.posting_record_id == posting_id_1  # Same ID
    assert result2.posting_reference == ref_1  # Same reference

    # Verify only 1 PostingRecord in database
    postings = demo_db.express_sync.list("PostingRecord")
    assert len(postings) == 1

    print(
        f"✓ Demo 4 Complete: Replay Detection Works - "
        f"Second posting returns ID {posting_id_1[:8]}... (no duplicate created)"
    )
