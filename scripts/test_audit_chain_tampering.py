"""
Realistic Temporary Database Demonstration: Hash-Linked Audit Chain Tamper Detection.

Step 7A Contract: Creates at least 3 linked events, verifies VALID, modifies one,
verifies BROKEN, then restores/resets to demonstrate tamper-evident behavior without
mutating production database.

Uses temporary SQLite database that is cleaned up after demonstration.
"""

import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from dataflow import DataFlow
from src.audit_chain_service import AuditChainService


def main():
    """Run tamper detection demonstration."""
    # Create temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db_path = Path(tmpdir) / "demo_audit.db"
        db_url = f"sqlite:///{tmp_db_path}"
        db = DataFlow(db_url)

        # Define BusinessAuditEvent model
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

        # Initialize audit chain service
        audit_service = AuditChainService()
        audit_service.db = db

        correlation_id = "corr-demo-001"
        invoice_id = "inv-demo-001"

        print("=" * 80)
        print("STEP 1: Create at least 3 linked events")
        print("=" * 80)

        # Event 1: Invoice Received
        ts1 = "2024-01-15T10:00:00+00:00"
        payload1 = json.dumps({"event": "INVOICE_RECEIVED", "supplier": "Vendor-A"})
        hash1 = audit_service.compute_event_hash(
            "aud-001", correlation_id, "INVOICE_RECEIVED", payload1, ts1, ""
        )
        event1 = {
            "id": "aud-001",
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "actor_id": "intake-service",
            "action_type": "INVOICE_RECEIVED",
            "action_outcome": "SUCCESS",
            "event_payload": payload1,
            "previous_hash": "",
            "event_hash": hash1,
            "event_timestamp": ts1,
        }
        db.express_sync.create("BusinessAuditEvent", event1)
        print(f"✓ Event 1 (INVOICE_RECEIVED): {event1['id']}")
        print(f"  Hash: {hash1[:16]}...{hash1[-16:]}")

        # Event 2: Approval Requested
        ts2 = "2024-01-15T10:15:00+00:00"
        payload2 = json.dumps(
            {
                "event": "APPROVAL_REQUESTED",
                "requested_by": "processor-1",
                "requested_from": "L2_SUPERVISOR",
            }
        )
        hash2 = audit_service.compute_event_hash(
            "aud-002", correlation_id, "APPROVAL_REQUESTED", payload2, ts2, hash1
        )
        event2 = {
            "id": "aud-002",
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "actor_id": "processor-1",
            "action_type": "APPROVAL_REQUESTED",
            "action_outcome": "PENDING",
            "event_payload": payload2,
            "previous_hash": hash1,
            "event_hash": hash2,
            "event_timestamp": ts2,
        }
        db.express_sync.create("BusinessAuditEvent", event2)
        print(f"✓ Event 2 (APPROVAL_REQUESTED): {event2['id']}")
        print(f"  Hash: {hash2[:16]}...{hash2[-16:]}")
        print(f"  Links to: {hash1[:16]}...{hash1[-16:]}")

        # Event 3: Approval Decision
        ts3 = "2024-01-15T10:30:00+00:00"
        payload3 = json.dumps(
            {
                "event": "APPROVAL_DECISION",
                "decision": "APPROVED",
                "approver": "supervisor-1",
            }
        )
        hash3 = audit_service.compute_event_hash(
            "aud-003", correlation_id, "APPROVAL_DECISION", payload3, ts3, hash2
        )
        event3 = {
            "id": "aud-003",
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "actor_id": "supervisor-1",
            "action_type": "APPROVAL_DECISION",
            "action_outcome": "APPROVED",
            "event_payload": payload3,
            "previous_hash": hash2,
            "event_hash": hash3,
            "event_timestamp": ts3,
        }
        db.express_sync.create("BusinessAuditEvent", event3)
        print(f"✓ Event 3 (APPROVAL_DECISION): {event3['id']}")
        print(f"  Hash: {hash3[:16]}...{hash3[-16:]}")
        print(f"  Links to: {hash2[:16]}...{hash2[-16:]}")

        print("\n" + "=" * 80)
        print("STEP 2: Verify chain is VALID")
        print("=" * 80)

        is_valid, issues = audit_service.verify_audit_chain(correlation_id)
        print(f"Chain status: {'✓ VALID' if is_valid else '✗ BROKEN'}")
        print(f"Event count: 3")
        print(f"Issues: {len(issues)}")
        if issues:
            for issue in issues:
                print(f"  - {issue}")

        assert is_valid is True, "Chain should be valid initially"

        print("\n" + "=" * 80)
        print("STEP 3: Modify one stored business payload (simulate tampering)")
        print("=" * 80)

        # Tamper with Event 2 payload
        tampered_payload2 = json.dumps(
            {
                "event": "APPROVAL_REQUESTED",
                "requested_by": "processor-1",
                "requested_from": "L3_CONTROLLER",  # CHANGED
            }
        )
        db.express_sync.upsert(
            "BusinessAuditEvent",
            {
                "id": "aud-002",
                "invoice_id": invoice_id,
                "correlation_id": correlation_id,
                "actor_id": "processor-1",
                "action_type": "APPROVAL_REQUESTED",
                "action_outcome": "PENDING",
                "event_payload": tampered_payload2,  # MODIFIED
                "previous_hash": hash1,
                "event_hash": hash2,  # Hash now invalid
                "event_timestamp": ts2,
            }
        )
        print("✗ Event 2 payload modified: requested_from changed to L3_CONTROLLER")
        print(f"  Stored hash still: {hash2[:16]}...{hash2[-16:]}")
        print("  (Hash now inconsistent with payload)")

        print("\n" + "=" * 80)
        print("STEP 4: Verify chain is now BROKEN (tampering detected)")
        print("=" * 80)

        is_valid, issues = audit_service.verify_audit_chain(correlation_id)
        print(f"Chain status: {'✓ VALID' if is_valid else '✗ BROKEN'}")
        print(f"Issues found: {len(issues)}")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")

        assert is_valid is False, "Chain should be broken after tampering"
        assert len(issues) > 0, "Issues should be detected"
        assert any(
            "hash mismatch" in issue.lower() for issue in issues
        ), "Hash mismatch should be detected"

        print("\n" + "=" * 80)
        print("STEP 5: Restore/reset by re-storing original payload")
        print("=" * 80)

        # Restore original payload
        db.express_sync.upsert(
            "BusinessAuditEvent",
            {
                "id": "aud-002",
                "invoice_id": invoice_id,
                "correlation_id": correlation_id,
                "actor_id": "processor-1",
                "action_type": "APPROVAL_REQUESTED",
                "action_outcome": "PENDING",
                "event_payload": payload2,  # RESTORED
                "previous_hash": hash1,
                "event_hash": hash2,
                "event_timestamp": ts2,
            }
        )
        print("✓ Event 2 payload restored to original")

        print("\n" + "=" * 80)
        print("STEP 6: Verify chain is VALID again (restoration successful)")
        print("=" * 80)

        is_valid, issues = audit_service.verify_audit_chain(correlation_id)
        print(f"Chain status: {'✓ VALID' if is_valid else '✗ BROKEN'}")
        print(f"Issues: {len(issues)}")
        if issues:
            for issue in issues:
                print(f"  - {issue}")

        assert is_valid is True, "Chain should be valid after restoration"

        print("\n" + "=" * 80)
        print("STEP 7: Demonstrate no production database mutation")
        print("=" * 80)
        print(f"✓ Using temporary SQLite database: {tmp_db_path}")
        print("✓ All events created and modified in isolated temp database")
        print("✓ No impact on production database")
        print(f"✓ Temporary database will be deleted on cleanup")

        print("\n" + "=" * 80)
        print("SUMMARY: Application-Level Tamper-Evident Chain")
        print("=" * 80)
        print("✓ Created 3 linked events in chain")
        print("✓ Verified VALID state with correct canonical hashes")
        print("✓ Detected BROKEN state when payload was modified")
        print("✓ Restored to VALID state after payload correction")
        print("✓ Demonstrated tamper detection capability")
        print("\nStatus: APPLICATION-LEVEL TAMPER-EVIDENT (not tamper-proof)")
        print("- Modified payload changes the computed hash")
        print("- Chain verification detects hash mismatches")
        print("- Requires additional measures for tamper-proof (e.g., signing, HSM)")


if __name__ == "__main__":
    main()
