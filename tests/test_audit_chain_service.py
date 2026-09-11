"""
Tests for Step 7A: Hash-Linked Audit Chain Service.

Verifies:
1. Canonical hash computation using full 64-char SHA256
2. Each event links to previous event via previous_hash
3. Audit chain can be reconstructed and verified
4. Chain integrity detection works (no broken links)
5. Tampering detection via hash mismatches
6. Sensitive data validation
7. Canonical JSON ordering
"""

import pytest
import json
from datetime import datetime, timezone
from pathlib import Path

from src.database import DataFlow
from src.audit_chain_service import AuditChainService


@pytest.fixture
def test_db(tmp_path):
    """Create temporary test database."""
    test_db_path = tmp_path / "test_audit_chain.db"
    test_db_url = f"sqlite:///{test_db_path}"

    db = DataFlow(test_db_url)

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
    yield db


@pytest.fixture
def audit_chain_service(test_db, monkeypatch):
    """Create AuditChainService with test database."""
    service = AuditChainService()
    monkeypatch.setattr("src.audit_chain_service.db", test_db)
    service.db = test_db
    return service


# ============================================================================
# CONTROL 1: DETERMINISTIC HASH COMPUTATION
# ============================================================================

def test_hash_computation_deterministic(audit_chain_service):
    """Test that hash computation is deterministic."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    action_type = "APPROVAL_DECISION"
    event_payload = '{"decision":"APPROVED"}'
    timestamp = "2024-01-15T10:00:00+00:00"
    previous_hash = "a" * 64

    hash1 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, event_payload, timestamp, previous_hash
    )
    hash2 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, event_payload, timestamp, previous_hash
    )

    assert hash1 == hash2, "Same inputs should produce same hash"
    assert len(hash1) == 64, "Hash must be full 64-character SHA256 hex"


def test_hash_full_length(audit_chain_service):
    """Test that hash is exactly 64 hexadecimal characters."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    action_type = "APPROVAL_DECISION"
    event_payload = '{"decision":"APPROVED"}'
    timestamp = "2024-01-15T10:00:00+00:00"

    full_hash = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, event_payload, timestamp, ""
    )

    assert len(full_hash) == 64, f"Hash must be 64 chars, got {len(full_hash)}"
    assert all(c in "0123456789abcdef" for c in full_hash), "Hash must be valid hex"


def test_hash_changes_with_payload(audit_chain_service):
    """Test that hash changes when event_payload changes (tampering detection)."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    action_type = "APPROVAL_DECISION"
    timestamp = "2024-01-15T10:00:00+00:00"
    previous_hash = "a" * 64

    hash_v1 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, '{"decision":"APPROVED"}', timestamp, previous_hash
    )
    hash_v2 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, '{"decision":"REJECTED"}', timestamp, previous_hash
    )

    assert hash_v1 != hash_v2, "Different payload should produce different hash"


def test_hash_changes_with_action_type(audit_chain_service):
    """Test that hash changes when action_type changes (tampering detection)."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    event_payload = '{"decision":"APPROVED"}'
    timestamp = "2024-01-15T10:00:00+00:00"
    previous_hash = "a" * 64

    hash_v1 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, "APPROVAL_DECISION", event_payload, timestamp, previous_hash
    )
    hash_v2 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, "INVOICE_POSTED", event_payload, timestamp, previous_hash
    )

    assert hash_v1 != hash_v2, "Different action_type should produce different hash"


def test_hash_changes_with_correlation_id(audit_chain_service):
    """Test that hash changes when correlation_id changes (tampering detection)."""
    event_id = "aud-001"
    action_type = "APPROVAL_DECISION"
    event_payload = '{"decision":"APPROVED"}'
    timestamp = "2024-01-15T10:00:00+00:00"
    previous_hash = "a" * 64

    hash_v1 = audit_chain_service.compute_event_hash(
        event_id, "corr-001", action_type, event_payload, timestamp, previous_hash
    )
    hash_v2 = audit_chain_service.compute_event_hash(
        event_id, "corr-002", action_type, event_payload, timestamp, previous_hash
    )

    assert hash_v1 != hash_v2, "Different correlation_id should produce different hash"


def test_hash_changes_with_previous_hash(audit_chain_service):
    """Test that hash changes when previous_hash changes (chain linking)."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    action_type = "APPROVAL_DECISION"
    event_payload = '{"decision":"APPROVED"}'
    timestamp = "2024-01-15T10:00:00+00:00"

    hash_with_link = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, event_payload, timestamp, "a" * 64
    )
    hash_without_link = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, event_payload, timestamp, ""
    )

    assert hash_with_link != hash_without_link, "Different previous_hash should produce different hash"


def test_sensitive_data_validation(audit_chain_service):
    """Test that sensitive fields in payload are rejected."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    action_type = "APPROVAL_DECISION"
    timestamp = "2024-01-15T10:00:00+00:00"

    # Try with api_key in payload
    sensitive_payload = '{"decision":"APPROVED","api_key":"secret123"}'
    with pytest.raises(ValueError, match="Prohibited sensitive field"):
        audit_chain_service.compute_event_hash(
            event_id, correlation_id, action_type, sensitive_payload, timestamp, ""
        )


def test_canonical_json_ordering(audit_chain_service):
    """Test that different JSON key order produces same hash (canonical)."""
    event_id = "aud-001"
    correlation_id = "corr-001"
    action_type = "APPROVAL_DECISION"
    timestamp = "2024-01-15T10:00:00+00:00"
    previous_hash = "a" * 64

    # Same payload with different key order
    payload1 = '{"decision":"APPROVED","approver":"user1"}'
    payload2 = '{"approver":"user1","decision":"APPROVED"}'

    hash1 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, payload1, timestamp, previous_hash
    )
    hash2 = audit_chain_service.compute_event_hash(
        event_id, correlation_id, action_type, payload2, timestamp, previous_hash
    )

    assert hash1 == hash2, "Canonical JSON should produce same hash regardless of key order"


# ============================================================================
# CONTROL 2: CHAIN LINKING
# ============================================================================

def test_get_last_audit_event_none(audit_chain_service):
    """Test that get_last_audit_event returns None for empty chain."""
    last = audit_chain_service.get_last_audit_event("corr-001")
    assert last is None


def test_get_last_audit_event_single(audit_chain_service, test_db):
    """Test fetching last event from single-event chain."""
    now = datetime.now(timezone.utc)
    event = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": "{}",
        "previous_hash": "",
        "event_hash": "hash001",
        "event_timestamp": now.isoformat(),
    }
    test_db.express_sync.create("BusinessAuditEvent", event)

    last = audit_chain_service.get_last_audit_event("corr-001")
    assert last is not None
    assert last["id"] == "aud-001"


def test_get_last_audit_event_multiple(audit_chain_service, test_db):
    """Test that get_last_audit_event returns most recent event."""
    now = datetime.now(timezone.utc)

    # Create first event
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": "{}",
        "previous_hash": "",
        "event_hash": "hash001",
        "event_timestamp": "2024-01-15T10:00:00+00:00",
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    # Create second event (later timestamp)
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": "{}",
        "previous_hash": "hash001",
        "event_hash": "hash002",
        "event_timestamp": "2024-01-15T10:01:00+00:00",
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)

    last = audit_chain_service.get_last_audit_event("corr-001")
    assert last is not None
    assert last["id"] == "aud-002", "Should return most recent event"


def test_link_audit_event_no_previous(audit_chain_service, test_db):
    """Test linking when no previous event exists."""
    event_data = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": "",
        "event_timestamp": "2024-01-15T10:00:00+00:00",
    }

    linked = audit_chain_service.link_audit_event(event_data, "corr-001")

    assert linked["previous_hash"] == "", "First event should have empty previous_hash"
    assert linked["event_hash"] != "", "Event hash should be computed"
    assert len(linked["event_hash"]) == 64, "Event hash must be full 64-char SHA256"


def test_link_audit_event_with_previous(audit_chain_service, test_db):
    """Test linking when previous event exists."""
    # Create first event with proper hash
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    # Link second event
    event2_data = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": "",
        "event_hash": "",
        "event_timestamp": "2024-01-15T10:01:00+00:00",
    }

    linked = audit_chain_service.link_audit_event(event2_data, "corr-001")

    assert linked["previous_hash"] == hash1, "Should link to previous event's hash"
    assert linked["event_hash"] != "", "Event hash should be computed with link"
    assert len(linked["event_hash"]) == 64, "Event hash must be full 64-char SHA256"


# ============================================================================
# CONTROL 3: CHAIN INTEGRITY VERIFICATION
# ============================================================================

def test_verify_audit_chain_empty(audit_chain_service):
    """Test verification of empty chain."""
    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is True, "Empty chain is valid"
    assert len(issues) == 0


def test_verify_audit_chain_single_event(audit_chain_service, test_db):
    """Test verification of single-event chain."""
    ts = "2024-01-15T10:00:00+00:00"
    event_hash = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts, ""
    )
    event = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": event_hash,
        "event_timestamp": ts,
    }
    test_db.express_sync.create("BusinessAuditEvent", event)

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is True, f"Chain should be valid: {issues}"
    assert len(issues) == 0


def test_verify_audit_chain_linked(audit_chain_service, test_db):
    """Test verification of properly linked chain."""
    # Create first event
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    # Create second event linked to first
    ts2 = "2024-01-15T10:01:00+00:00"
    hash2 = audit_chain_service.compute_event_hash(
        "aud-002", "corr-001", "APPROVAL_DECISION", '{"decision":"REJECTED"}', ts2, hash1
    )
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": hash1,
        "event_hash": hash2,
        "event_timestamp": ts2,
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is True, f"Valid chain should verify: {issues}"
    assert len(issues) == 0


def test_verify_audit_chain_broken_link(audit_chain_service, test_db):
    """Test detection of broken link in chain."""
    # Create first event
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    # Create second event with WRONG previous_hash (broken link)
    ts2 = "2024-01-15T10:01:00+00:00"
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": "wrong" + "a" * 59,  # Wrong previous hash
        "event_hash": "b" * 64,  # Will be wrong anyway
        "event_timestamp": ts2,
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is False, "Broken link should be detected"
    assert len(issues) > 0


# ============================================================================
# CONTROL 4A: TAMPERING DETECTION (NEGATIVE TESTS)
# ============================================================================

def test_tampering_detection_payload_modified(audit_chain_service, test_db):
    """Test detection when event_payload is modified (BROKEN)."""
    # Create valid event
    ts = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts, ""
    )
    event = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts,
    }
    test_db.express_sync.create("BusinessAuditEvent", event)

    # Simulate tampering: modify stored payload
    test_db.express_sync.upsert(
        "BusinessAuditEvent",
        {
            "id": "aud-001",
            "invoice_id": "inv-001",
            "correlation_id": "corr-001",
            "actor_id": "actor-001",
            "action_type": "APPROVAL_DECISION",
            "action_outcome": "APPROVED",
            "event_payload": '{"decision":"REJECTED"}',  # TAMPERING
            "previous_hash": "",
            "event_hash": hash1,  # Hash no longer matches
            "event_timestamp": ts,
        }
    )

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is False, "Tampering should be detected"
    assert len(issues) > 0
    assert any("hash mismatch" in issue.lower() for issue in issues)


def test_tampering_detection_action_type_modified(audit_chain_service, test_db):
    """Test detection when action_type is modified (BROKEN)."""
    # Create valid event
    ts = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts, ""
    )
    event = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts,
    }
    test_db.express_sync.create("BusinessAuditEvent", event)

    # Simulate tampering: modify action_type
    test_db.express_sync.upsert(
        "BusinessAuditEvent",
        {
            "id": "aud-001",
            "invoice_id": "inv-001",
            "correlation_id": "corr-001",
            "actor_id": "actor-001",
            "action_type": "INVOICE_POSTED",  # TAMPERING
            "action_outcome": "APPROVED",
            "event_payload": '{"decision":"APPROVED"}',
            "previous_hash": "",
            "event_hash": hash1,  # Hash no longer matches
            "event_timestamp": ts,
        }
    )

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is False, "Tampering should be detected"
    assert len(issues) > 0
    assert any("hash mismatch" in issue.lower() for issue in issues)


def test_tampering_detection_correlation_id_modified(audit_chain_service, test_db):
    """Test detection when correlation_id is modified (BROKEN)."""
    # Create valid event chain
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    # Create second event with corr-001
    ts2 = "2024-01-15T10:01:00+00:00"
    hash2 = audit_chain_service.compute_event_hash(
        "aud-002", "corr-001", "APPROVAL_DECISION", '{"decision":"REJECTED"}', ts2, hash1
    )
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": hash1,
        "event_hash": hash2,
        "event_timestamp": ts2,
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)

    # Simulate tampering: modify stored correlation_id of event2
    test_db.express_sync.upsert(
        "BusinessAuditEvent",
        {
            "id": "aud-002",
            "invoice_id": "inv-001",
            "correlation_id": "corr-tampering",  # TAMPERING: changed from corr-001
            "actor_id": "actor-002",
            "action_type": "APPROVAL_DECISION",
            "action_outcome": "REJECTED",
            "event_payload": '{"decision":"REJECTED"}',
            "previous_hash": hash1,
            "event_hash": hash2,  # Hash no longer matches with modified correlation_id
            "event_timestamp": ts2,
        }
    )

    # When verification uses stored correlation_id, it detects the tampering
    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")

    # Query with corr-001 won't find aud-002 (it's now in corr-tampering)
    # So the chain appears broken (missing event or short chain)
    # Actually, we need to query with the modified correlation_id to test this properly
    # Let's instead query with corr-tampering to test that verification detects the issue

    # Query the tampered event with its modified correlation_id
    is_valid_tampering, issues_tampering = audit_chain_service.verify_audit_chain("corr-tampering")

    # The tampered event will be found, but hash mismatch will be detected
    # because it was computed with corr-001 but stored with corr-tampering
    assert is_valid_tampering is False, "Tampering should be detected"
    assert len(issues_tampering) > 0, "Issues should be found"
    assert any("hash mismatch" in issue.lower() for issue in issues_tampering)
    # First affected event should be identified
    assert "aud-002" in str(issues_tampering)


def test_tampering_detection_previous_hash_modified(audit_chain_service, test_db):
    """Test detection when previous_hash is modified (BROKEN)."""
    # Create first event
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    # Create second event
    ts2 = "2024-01-15T10:01:00+00:00"
    hash2 = audit_chain_service.compute_event_hash(
        "aud-002", "corr-001", "APPROVAL_DECISION", '{"decision":"REJECTED"}', ts2, hash1
    )
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": hash1,
        "event_hash": hash2,
        "event_timestamp": ts2,
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)

    # Simulate tampering: modify previous_hash
    test_db.express_sync.upsert(
        "BusinessAuditEvent",
        {
            "id": "aud-002",
            "invoice_id": "inv-001",
            "correlation_id": "corr-001",
            "actor_id": "actor-002",
            "action_type": "APPROVAL_DECISION",
            "action_outcome": "REJECTED",
            "event_payload": '{"decision":"REJECTED"}',
            "previous_hash": "wrong" + "a" * 59,  # TAMPERING
            "event_hash": hash2,  # Now inconsistent
            "event_timestamp": ts2,
        }
    )

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is False, "Tampering should be detected"
    assert len(issues) > 0


def test_tampering_detection_event_hash_modified(audit_chain_service, test_db):
    """Test detection when stored event_hash is modified (BROKEN)."""
    # Create valid event
    ts = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts, ""
    )
    event = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts,
    }
    test_db.express_sync.create("BusinessAuditEvent", event)

    # Simulate tampering: modify stored hash
    test_db.express_sync.upsert(
        "BusinessAuditEvent",
        {
            "id": "aud-001",
            "invoice_id": "inv-001",
            "correlation_id": "corr-001",
            "actor_id": "actor-001",
            "action_type": "APPROVAL_DECISION",
            "action_outcome": "APPROVED",
            "event_payload": '{"decision":"APPROVED"}',
            "previous_hash": "",
            "event_hash": "wrong" + "a" * 59,  # TAMPERING
            "event_timestamp": ts,
        }
    )

    is_valid, issues = audit_chain_service.verify_audit_chain("corr-001")
    assert is_valid is False, "Tampering should be detected"
    assert len(issues) > 0
    assert any("hash mismatch" in issue.lower() for issue in issues)


# ============================================================================
# CONTROL 4: GET AUDIT CHAIN (FULL CHAIN RETRIEVAL)
# ============================================================================

def test_get_audit_chain_empty(audit_chain_service):
    """Test retrieving empty chain."""
    chain = audit_chain_service.get_audit_chain("corr-001")
    assert len(chain) == 0


def test_get_audit_chain_ordered(audit_chain_service, test_db):
    """Test that get_audit_chain returns events in chronological order."""
    # Create events in reverse order
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    # Insert second event first (reverse order)
    ts2 = "2024-01-15T10:01:00+00:00"
    hash2 = audit_chain_service.compute_event_hash(
        "aud-002", "corr-001", "APPROVAL_DECISION", '{"decision":"REJECTED"}', ts2, hash1
    )
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": hash1,
        "event_hash": hash2,
        "event_timestamp": ts2,
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)
    test_db.express_sync.create("BusinessAuditEvent", event1)

    chain = audit_chain_service.get_audit_chain("corr-001")

    assert len(chain) == 2
    assert chain[0]["id"] == "aud-001", "First in chain should be first event"
    assert chain[1]["id"] == "aud-002", "Second in chain should be second event"


# ============================================================================
# CONTROL 5: CHAIN SUMMARY
# ============================================================================

def test_get_chain_summary_empty(audit_chain_service):
    """Test chain summary for empty chain."""
    summary = audit_chain_service.get_chain_summary("corr-001")

    assert summary["correlation_id"] == "corr-001"
    assert summary["event_count"] == 0
    assert summary["is_valid"] is True
    assert summary["first_event_id"] is None
    assert summary["last_event_id"] is None


def test_get_chain_summary_with_events(audit_chain_service, test_db):
    """Test chain summary with events."""
    # Create two linked events
    ts1 = "2024-01-15T10:00:00+00:00"
    hash1 = audit_chain_service.compute_event_hash(
        "aud-001", "corr-001", "APPROVAL_DECISION", '{"decision":"APPROVED"}', ts1, ""
    )
    event1 = {
        "id": "aud-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-001",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "APPROVED",
        "event_payload": '{"decision":"APPROVED"}',
        "previous_hash": "",
        "event_hash": hash1,
        "event_timestamp": ts1,
    }
    test_db.express_sync.create("BusinessAuditEvent", event1)

    ts2 = "2024-01-15T10:01:00+00:00"
    hash2 = audit_chain_service.compute_event_hash(
        "aud-002", "corr-001", "APPROVAL_DECISION", '{"decision":"REJECTED"}', ts2, hash1
    )
    event2 = {
        "id": "aud-002",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "actor_id": "actor-002",
        "action_type": "APPROVAL_DECISION",
        "action_outcome": "REJECTED",
        "event_payload": '{"decision":"REJECTED"}',
        "previous_hash": hash1,
        "event_hash": hash2,
        "event_timestamp": ts2,
    }
    test_db.express_sync.create("BusinessAuditEvent", event2)

    summary = audit_chain_service.get_chain_summary("corr-001")

    assert summary["event_count"] == 2
    assert summary["is_valid"] is True
    assert summary["first_event_id"] == "aud-001"
    assert summary["last_event_id"] == "aud-002"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
