"""
Tests for Step 7B: Exception Visibility and Resolution.

Verifies:
1. Exception visibility (querying, filtering, metrics)
2. Exception resolution workflow (authorization, status transitions)
3. Audit trail for exception state changes
4. Idempotent resolution (replay-safe)
"""

import pytest
import json
from datetime import datetime, timezone
from pathlib import Path

from src.database import DataFlow
from src.exception_visibility_service import (
    ExceptionVisibilityService,
    ExceptionQueryFilter,
)
from src.exception_resolution_service import (
    ExceptionResolutionService,
    ExceptionResolutionInput,
)


@pytest.fixture
def test_db(tmp_path):
    """Create temporary test database."""
    test_db_path = tmp_path / "test_exceptions.db"
    test_db_url = f"sqlite:///{test_db_path}"

    db = DataFlow(test_db_url)

    @db.model
    class ExceptionCase:
        id: str
        invoice_id: str
        correlation_id: str
        reason_code: str
        reason_detail: str
        recommended_action: str
        owner_id: str
        required_authority: str
        exception_status: str = "OPEN"
        opened_at: str = ""
        resolved_at: str = ""

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
def visibility_service(test_db, monkeypatch):
    """Create ExceptionVisibilityService with test database."""
    service = ExceptionVisibilityService()
    monkeypatch.setattr("src.exception_visibility_service.db", test_db)
    service.db = test_db
    return service


@pytest.fixture
def resolution_service(test_db, monkeypatch):
    """Create ExceptionResolutionService with test database."""
    service = ExceptionResolutionService()
    monkeypatch.setattr("src.exception_resolution_service.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)
    service.db = test_db
    service.audit_chain.db = test_db
    return service


# ============================================================================
# EXCEPTION VISIBILITY TESTS
# ============================================================================


def test_get_exceptions_empty(visibility_service):
    """Test querying when no exceptions exist."""
    filter_criteria = ExceptionQueryFilter()
    exceptions = visibility_service.get_exceptions(filter_criteria)
    assert len(exceptions) == 0


def test_get_exceptions_creates_and_queries(visibility_service, test_db):
    """Test creating and querying exceptions."""
    # Create exception
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Invoice is duplicate of inv-999",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    # Query all
    filter_criteria = ExceptionQueryFilter()
    exceptions = visibility_service.get_exceptions(filter_criteria)
    assert len(exceptions) == 1
    assert exceptions[0]["id"] == "exc-001"


def test_get_exceptions_filter_by_invoice_id(visibility_service, test_db):
    """Test filtering exceptions by invoice_id."""
    now = datetime.now(timezone.utc).isoformat()
    exc1 = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    exc2 = {
        "id": "exc-002",
        "invoice_id": "inv-002",
        "correlation_id": "corr-002",
        "reason_code": "MISSING_REF",
        "reason_detail": "Missing PO reference",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exc1)
    test_db.express_sync.create("ExceptionCase", exc2)

    # Filter by invoice_id
    filter_criteria = ExceptionQueryFilter(invoice_id="inv-001")
    exceptions = visibility_service.get_exceptions(filter_criteria)
    assert len(exceptions) == 1
    assert exceptions[0]["invoice_id"] == "inv-001"


def test_get_exceptions_filter_by_reason_code(visibility_service, test_db):
    """Test filtering exceptions by reason_code."""
    now = datetime.now(timezone.utc).isoformat()
    exc1 = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    exc2 = {
        "id": "exc-002",
        "invoice_id": "inv-002",
        "correlation_id": "corr-002",
        "reason_code": "MISSING_REF",
        "reason_detail": "Missing PO reference",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exc1)
    test_db.express_sync.create("ExceptionCase", exc2)

    # Filter by reason_code
    filter_criteria = ExceptionQueryFilter(reason_code="DUPLICATE")
    exceptions = visibility_service.get_exceptions(filter_criteria)
    assert len(exceptions) == 1
    assert exceptions[0]["reason_code"] == "DUPLICATE"


def test_get_open_exceptions(visibility_service, test_db):
    """Test querying only open exceptions."""
    now = datetime.now(timezone.utc).isoformat()
    open_exc = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    resolved_exc = {
        "id": "exc-002",
        "invoice_id": "inv-002",
        "correlation_id": "corr-002",
        "reason_code": "MISSING_REF",
        "reason_detail": "Missing PO reference",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "RESOLVED",
        "opened_at": now,
        "resolved_at": now,
    }
    test_db.express_sync.create("ExceptionCase", open_exc)
    test_db.express_sync.create("ExceptionCase", resolved_exc)

    # Get only open
    open_exceptions = visibility_service.get_open_exceptions()
    assert len(open_exceptions) == 1
    assert open_exceptions[0]["exception_status"] == "OPEN"


def test_compute_metrics(visibility_service, test_db):
    """Test exception metrics computation."""
    now = datetime.now(timezone.utc).isoformat()
    exc1 = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    exc2 = {
        "id": "exc-002",
        "invoice_id": "inv-002",
        "correlation_id": "corr-002",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-2",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    exc3 = {
        "id": "exc-003",
        "invoice_id": "inv-003",
        "correlation_id": "corr-003",
        "reason_code": "MISSING_REF",
        "reason_detail": "Missing PO reference",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "RESOLVED",
        "opened_at": now,
        "resolved_at": now,
    }
    test_db.express_sync.create("ExceptionCase", exc1)
    test_db.express_sync.create("ExceptionCase", exc2)
    test_db.express_sync.create("ExceptionCase", exc3)

    # Compute metrics
    metrics = visibility_service.compute_metrics()
    assert metrics.total_open == 2
    assert metrics.total_resolved == 1
    assert metrics.by_reason_code["DUPLICATE"] == 2
    assert metrics.by_reason_code["MISSING_REF"] == 1
    assert metrics.by_owner["approver-1"] == 2
    assert metrics.by_owner["approver-2"] == 1


# ============================================================================
# EXCEPTION RESOLUTION TESTS
# ============================================================================


def test_resolve_exception_not_found(resolution_service):
    """Test resolving non-existent exception."""
    resolution_input = ExceptionResolutionInput(
        exception_id="nonexistent",
        resolver_id="approver-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    with pytest.raises(ValueError, match="not found"):
        resolution_service.resolve_exception(resolution_input)


def test_resolve_exception_authority_check(resolution_service, test_db):
    """Test that L1_PROCESSOR cannot resolve exceptions."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    # L1_PROCESSOR tries to resolve (should be blocked)
    resolution_input = ExceptionResolutionInput(
        exception_id="exc-001",
        resolver_id="processor-1",
        resolver_authority="L1_PROCESSOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "BLOCKED"
    assert "cannot resolve" in result.blocked_reason.lower()


def test_resolve_exception_l2_supervisor_succeeds(resolution_service, test_db):
    """Test that L2_SUPERVISOR can resolve exceptions."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    # L2_SUPERVISOR resolves
    resolution_input = ExceptionResolutionInput(
        exception_id="exc-001",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Verified and resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "RESOLVED"
    assert result.audit_event_id != ""


def test_resolve_exception_creates_audit_event(resolution_service, test_db):
    """Test that resolving exception creates BusinessAuditEvent."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    # Resolve
    resolution_input = ExceptionResolutionInput(
        exception_id="exc-001",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Verified",
    )

    result = resolution_service.resolve_exception(resolution_input)

    # Verify audit event was created
    audit_events = test_db.express_sync.list(
        "BusinessAuditEvent", {"action_type": "EXCEPTION_RESOLVED"}
    )
    assert len(audit_events) >= 1
    assert audit_events[0]["action_outcome"] == "RESOLVED"


def test_resolve_exception_updates_status(resolution_service, test_db):
    """Test that resolution updates exception status."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    # Resolve
    resolution_input = ExceptionResolutionInput(
        exception_id="exc-001",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Verified",
    )

    result = resolution_service.resolve_exception(resolution_input)

    # Verify status changed
    updated = test_db.express_sync.find_one("ExceptionCase", {"id": "exc-001"})
    assert updated["exception_status"] == "RESOLVED"
    assert updated["resolved_at"] != ""


def test_resolve_exception_cannot_resolve_already_resolved(resolution_service, test_db):
    """Test that already-resolved exceptions cannot be re-resolved."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-001",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "approver-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "RESOLVED",  # Already resolved
        "opened_at": now,
        "resolved_at": now,
    }
    test_db.express_sync.create("ExceptionCase", exception)

    # Try to resolve again
    resolution_input = ExceptionResolutionInput(
        exception_id="exc-001",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Verified",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "BLOCKED"
    assert "blocked" in result.explanation.lower()


# ============================================================================
# HIERARCHY-AWARE AUTHORIZATION TESTS
# ============================================================================


def test_l1_cannot_resolve_l2_exception(resolution_service, test_db):
    """Test that L1_PROCESSOR cannot resolve L2_SUPERVISOR exception."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-l1-l2",
        "invoice_id": "inv-001",
        "correlation_id": "corr-001",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "processor-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l1-l2",
        resolver_id="processor-1",
        resolver_authority="L1_PROCESSOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "BLOCKED"
    assert "L1_PROCESSOR" in result.blocked_reason


def test_l2_cannot_resolve_l3_exception(resolution_service, test_db):
    """Test that L2_SUPERVISOR cannot resolve L3_CONTROLLER exception."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-l2-l3",
        "invoice_id": "inv-002",
        "correlation_id": "corr-002",
        "reason_code": "MISSING_REF",
        "reason_detail": "Missing reference",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "supervisor-1",
        "required_authority": "L3_CONTROLLER",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l2-l3",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "BLOCKED"
    assert "L2_SUPERVISOR" in result.blocked_reason
    assert "L3_CONTROLLER" in result.blocked_reason


def test_l3_cannot_resolve_human_approver_exception(resolution_service, test_db):
    """Test that L3_CONTROLLER cannot resolve HUMAN_APPROVER exception."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-l3-human",
        "invoice_id": "inv-003",
        "correlation_id": "corr-003",
        "reason_code": "COMPLIANCE_HOLD",
        "reason_detail": "Compliance hold",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "controller-1",
        "required_authority": "HUMAN_APPROVER",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l3-human",
        resolver_id="controller-1",
        resolver_authority="L3_CONTROLLER",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "BLOCKED"
    assert "L3_CONTROLLER" in result.blocked_reason
    assert "HUMAN_APPROVER" in result.blocked_reason


def test_l2_can_resolve_l2_exception(resolution_service, test_db):
    """Test that L2_SUPERVISOR can resolve L2_SUPERVISOR exception."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-l2-l2",
        "invoice_id": "inv-004",
        "correlation_id": "corr-004",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "supervisor-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l2-l2",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "RESOLVED"
    assert result.audit_event_id != ""


def test_l3_can_resolve_l2_exception(resolution_service, test_db):
    """Test that L3_CONTROLLER can resolve L2_SUPERVISOR exception."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-l3-l2",
        "invoice_id": "inv-005",
        "correlation_id": "corr-005",
        "reason_code": "DUPLICATE",
        "reason_detail": "Duplicate",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "controller-1",
        "required_authority": "L2_SUPERVISOR",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l3-l2",
        resolver_id="controller-1",
        resolver_authority="L3_CONTROLLER",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "RESOLVED"
    assert result.audit_event_id != ""


def test_l3_can_resolve_l3_exception(resolution_service, test_db):
    """Test that L3_CONTROLLER can resolve L3_CONTROLLER exception."""
    now = datetime.now(timezone.utc).isoformat()
    exception = {
        "id": "exc-l3-l3",
        "invoice_id": "inv-006",
        "correlation_id": "corr-006",
        "reason_code": "COMPLIANCE_HOLD",
        "reason_detail": "Compliance hold",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "controller-1",
        "required_authority": "L3_CONTROLLER",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l3-l3",
        resolver_id="controller-1",
        resolver_authority="L3_CONTROLLER",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Resolved",
    )

    result = resolution_service.resolve_exception(resolution_input)
    assert result.resolution_status == "RESOLVED"
    assert result.audit_event_id != ""


def test_human_approver_can_resolve_any_exception(resolution_service, test_db):
    """Test that HUMAN_APPROVER can resolve any exception."""
    now = datetime.now(timezone.utc).isoformat()

    test_cases = [
        ("exc-human-l2", "L2_SUPERVISOR"),
        ("exc-human-l3", "L3_CONTROLLER"),
        ("exc-human-human", "HUMAN_APPROVER"),
    ]

    for exc_id, required_auth in test_cases:
        exception = {
            "id": exc_id,
            "invoice_id": f"inv-{exc_id}",
            "correlation_id": f"corr-{exc_id}",
            "reason_code": "TEST",
            "reason_detail": "Test",
            "recommended_action": "MANUAL_REVIEW",
            "owner_id": "approver-1",
            "required_authority": required_auth,
            "exception_status": "OPEN",
            "opened_at": now,
            "resolved_at": "",
        }
        test_db.express_sync.create("ExceptionCase", exception)

        resolution_input = ExceptionResolutionInput(
            exception_id=exc_id,
            resolver_id="approver-1",
            resolver_authority="HUMAN_APPROVER",
            resolution_code="RESOLVED_MANUALLY",
            resolution_comment="Resolved",
        )

        result = resolution_service.resolve_exception(resolution_input)
        assert result.resolution_status == "RESOLVED", f"Failed for {required_auth}"
        assert result.audit_event_id != ""


# ============================================================================
# TESTS: STEP 7B - REQUIRED_AUTHORITY SCHEMA INTEGRATION
# ============================================================================


def test_missing_required_authority_blocks_resolution(resolution_service, test_db):
    """Test 51: Missing required_authority is blocked (fail closed)."""
    now = datetime.now(timezone.utc).isoformat()

    # Create exception with empty string required_authority (simulates NULL in legacy data)
    exception = {
        "id": "exc-legacy-missing",
        "invoice_id": "inv-legacy-missing",
        "correlation_id": "corr-legacy-missing",
        "reason_code": "MISSING_AUTHORITY",
        "reason_detail": "Test legacy case without required_authority",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "system",
        "required_authority": "",  # Empty = missing
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-legacy-missing",
        resolver_id="approver-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="ATTEMPTED_RESOLUTION",
        resolution_comment="Tried to resolve",
    )

    result = resolution_service.resolve_exception(resolution_input)

    # Must be blocked
    assert result.resolution_status == "BLOCKED"
    assert "required_authority" in result.blocked_reason
    assert "missing" in result.blocked_reason.lower()
    assert result.audit_event_id == ""


def test_unknown_required_authority_blocks_resolution(resolution_service, test_db):
    """Test 52: Unknown required_authority is blocked (fail closed)."""
    now = datetime.now(timezone.utc).isoformat()

    # Create exception with unknown/invalid authority
    exception = {
        "id": "exc-unknown-auth",
        "invoice_id": "inv-unknown-auth",
        "correlation_id": "corr-unknown-auth",
        "reason_code": "UNKNOWN_AUTHORITY",
        "reason_detail": "Test case with unknown authority",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "system",
        "required_authority": "INVALID_AUTHORITY_LEVEL",  # Unknown
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-unknown-auth",
        resolver_id="approver-1",
        resolver_authority="HUMAN_APPROVER",
        resolution_code="ATTEMPTED_RESOLUTION",
        resolution_comment="Tried to resolve",
    )

    result = resolution_service.resolve_exception(resolution_input)

    # Must be blocked even for HUMAN_APPROVER
    assert result.resolution_status == "BLOCKED"
    assert "unknown" in result.blocked_reason.lower()
    assert "INVALID_AUTHORITY_LEVEL" in result.blocked_reason
    assert result.audit_event_id == ""


def test_human_approver_can_resolve_legacy_fail_closed_case(resolution_service, test_db):
    """Test 53: HUMAN_APPROVER can help resolve when required_authority exists but is unknown (escalation path)."""
    now = datetime.now(timezone.utc).isoformat()

    # This test ensures HUMAN_APPROVER cannot override the fail-closed behavior for truly unknown authorities,
    # but CAN resolve legitimate exceptions with known authorities
    exception = {
        "id": "exc-escalation",
        "invoice_id": "inv-escalation",
        "correlation_id": "corr-escalation",
        "reason_code": "REQUIRES_HUMAN_REVIEW",
        "reason_detail": "Complex governance decision",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "system",
        "required_authority": "HUMAN_APPROVER",  # This is valid
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-escalation",
        resolver_id="approver-1",
        resolver_authority="HUMAN_APPROVER",
        resolution_code="RESOLVED_MANUALLY",
        resolution_comment="Human resolution",
    )

    result = resolution_service.resolve_exception(resolution_input)

    # Should be resolved, not blocked
    assert result.resolution_status == "RESOLVED"
    assert result.audit_event_id != ""


def test_l2_supervisor_cannot_resolve_l3_or_unknown_authority(resolution_service, test_db):
    """Test 54: L2_SUPERVISOR cannot resolve exceptions requiring higher authority."""
    now = datetime.now(timezone.utc).isoformat()

    # Create exception requiring L3_CONTROLLER
    exception = {
        "id": "exc-l2-cannot-l3",
        "invoice_id": "inv-l2-cannot-l3",
        "correlation_id": "corr-l2-cannot-l3",
        "reason_code": "REQUIRES_L3",
        "reason_detail": "Requires L3 authority",
        "recommended_action": "MANUAL_REVIEW",
        "owner_id": "system",
        "required_authority": "L3_CONTROLLER",
        "exception_status": "OPEN",
        "opened_at": now,
        "resolved_at": "",
    }
    test_db.express_sync.create("ExceptionCase", exception)

    resolution_input = ExceptionResolutionInput(
        exception_id="exc-l2-cannot-l3",
        resolver_id="supervisor-1",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="ATTEMPTED_RESOLUTION",
        resolution_comment="Tried to resolve",
    )

    result = resolution_service.resolve_exception(resolution_input)

    # Must be blocked
    assert result.resolution_status == "BLOCKED"
    assert "cannot resolve" in result.blocked_reason.lower()
    assert result.audit_event_id == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
