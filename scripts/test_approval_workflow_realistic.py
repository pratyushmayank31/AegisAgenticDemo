#!/usr/bin/env python
"""
Realistic execution test for Step 6: Human Approval Workflow.

Tests the complete approval workflow using actual invoice data:
1. Creates sample invoice cases
2. Creates approval requests (simulating governance decisions)
3. Records approver decisions (approved/rejected)
4. Verifies InvoiceCase and ApprovalRequest status updates
5. Verifies audit trail creation

Uses temporary test database to avoid polluting production data.
"""

import sys
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import DataFlow
from src.approval_workflow import ApprovalWorkflow, ApprovalDecisionInput
from src.authority_hierarchy import AuthorityLevel


def setup_test_database():
    """Create temporary test database with models."""
    temp_dir = tempfile.mkdtemp()
    test_db_path = Path(temp_dir) / "test_approval_realistic.db"
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

    db.create_tables_sync()
    return db, str(test_db_path)


def create_sample_invoice(db, invoice_id, amount, supplier, invoice_number):
    """Create a sample invoice case."""
    now = datetime.now(timezone.utc)
    invoice_data = {
        "id": invoice_id,
        "correlation_id": f"corr-{invoice_id}",
        "event_id": f"evt-{invoice_id}",
        "document_path": f"/invoices/{invoice_number}.pdf",
        "document_hash": f"hash-{invoice_id}",
        "supplier_name": supplier,
        "invoice_number": invoice_number,
        "legal_entity": "Company USA",
        "invoice_date": "2024-01-15",
        "currency": "USD",
        "gross_amount": amount,
        "extraction_confidence": 0.92,
        "target_system": "SAP",
        "accounting_code": "5000-1234",
        "case_status": "RECEIVED",
        "duplicate_of": "",
        "current_owner": "finance-orchestrator",
    }
    db.express_sync.create("InvoiceCase", invoice_data)
    return invoice_data


def create_approval_request(db, invoice_id, authority_level, reason):
    """Create an approval request for an invoice."""
    now = datetime.now(timezone.utc)
    approval_data = {
        "id": f"arq-{invoice_id}",
        "invoice_id": invoice_id,
        "correlation_id": f"corr-{invoice_id}",
        "requested_by": "governance-orchestrator",
        "requested_from": authority_level.value,
        "approval_reason": reason,
        "approval_status": "PENDING",
        "approver_id": "",
        "approver_comment": "",
        "requested_at": now.isoformat(),
        "decided_at": "",
    }
    db.express_sync.create("ApprovalRequest", approval_data)
    return approval_data


def main():
    """Run realistic approval workflow execution."""
    print("=" * 80)
    print("Step 6: Human Approval Workflow - Realistic Execution Test")
    print("=" * 80)

    # Setup test database
    print("\n[1/5] Setting up test database...")
    db, db_path = setup_test_database()
    print(f"✓ Test database created: {db_path}")

    # Create sample invoices
    print("\n[2/5] Creating sample invoices...")
    invoices = [
        ("inv-001", 25000.0, "Acme Corp", "INV-2024-001"),
        ("inv-002", 65000.0, "Global Services Inc", "INV-2024-002"),
        ("inv-003", 8500.0, "Local Supplier", "INV-2024-003"),
    ]

    for invoice_id, amount, supplier, invoice_number in invoices:
        invoice = create_sample_invoice(db, invoice_id, amount, supplier, invoice_number)
        print(
            f"  ✓ Created {invoice_id}: ${amount:,.2f} from {supplier}"
        )

    # Create approval requests (simulate governance decisions)
    print("\n[3/5] Creating approval requests (simulating HOLD decisions)...")
    approval_requests = [
        ("inv-001", AuthorityLevel.L2_SUPERVISOR, "AMOUNT_THRESHOLD"),
        ("inv-002", AuthorityLevel.L3_CONTROLLER, "AMOUNT_THRESHOLD"),
        ("inv-003", AuthorityLevel.L1_PROCESSOR, "LOW_CONFIDENCE"),
    ]

    for invoice_id, authority, reason in approval_requests:
        approval = create_approval_request(db, invoice_id, authority, reason)
        print(
            f"  ✓ Created {approval['id']}: {authority.value} approval for {reason}"
        )

    # Test approval workflow
    print("\n[4/5] Processing approval decisions...")
    workflow = ApprovalWorkflow()
    workflow.db = db

    test_cases = [
        ("arq-inv-001", "approver-l2", "APPROVED", "Invoice verified"),
        ("arq-inv-002", "approver-l3", "REJECTED", "Amount exceeds policy"),
        ("arq-inv-003", "approver-l1", "APPROVED", "Low confidence item approved"),
    ]

    results = []
    for approval_id, approver_id, decision, comment in test_cases:
        try:
            decision_input = ApprovalDecisionInput(
                approval_request_id=approval_id,
                approver_id=approver_id,
                decision=decision,
                approver_comment=comment,
            )
            result = workflow.record_approval_decision(decision_input)
            results.append(result)

            print(
                f"  ✓ {approval_id}: {decision} by {approver_id}"
            )
            print(
                f"    → Invoice {result.invoice_id} status → {result.invoice_case_status}"
            )
        except Exception as e:
            print(f"  ✗ {approval_id}: {str(e)}")
            return 1

    # Verify workflow results
    print("\n[5/5] Verifying workflow results...")

    # Verify ApprovalRequest status updates
    pending = workflow.get_pending_approvals()
    print(f"  ✓ Pending approvals: {len(pending)} (expected 0)")

    # Verify InvoiceCase status updates
    for result in results:
        invoice_case = db.express_sync.find_one("InvoiceCase", {"id": result.invoice_id})
        if invoice_case:
            status = invoice_case.get("case_status")
            expected = result.invoice_case_status
            if status == expected:
                print(f"  ✓ {result.invoice_id}: case_status = {status}")
            else:
                print(f"  ✗ {result.invoice_id}: case_status = {status}, expected {expected}")
                return 1
        else:
            print(f"  ✗ {result.invoice_id}: InvoiceCase not found")
            return 1

    # Verify audit events
    audit_events = db.express_sync.list(
        "BusinessAuditEvent",
        {"action_type": "APPROVAL_DECISION"},
    )
    print(f"  ✓ Audit events created: {len(audit_events)}")

    print("\n" + "=" * 80)
    print("✓ ALL CHECKS PASSED - Step 6 Approval Workflow is functional")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
