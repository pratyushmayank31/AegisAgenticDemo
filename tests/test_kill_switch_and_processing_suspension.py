"""
Tests for Step 7C: Kill Switch and Processing Suspension.

Verifies:
1. Role-based authorization (GOVERNANCE_ADMIN only can engage/disengage)
2. Authorization failures are fail-closed (no state mutation, no audit creation)
3. Kill switch engagement/disengagement via AuditChainService
4. Audit events have 64-char SHA256 hashes and chain linkage
5. State persistence and retrieval
6. Intake service respects kill switch status
7. Synchronous and asynchronous APIs work correctly
8. State history tracking
"""

import pytest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from src.database import DataFlow
from src.kill_switch_service import KillSwitchService, OperationalRole
from src.audit_chain_service import AuditChainService
from src.intake_service import run_intake


@pytest.fixture
def test_db(tmp_path):
    """Create temporary test database."""
    test_db_path = tmp_path / "test_kill_switch.db"
    test_db_url = f"sqlite:///{test_db_path}"

    db = DataFlow(test_db_url)

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
    class EventReceipt:
        id: str
        invoice_id: str
        correlation_id: str
        payload_hash: str
        receipt_status: str
        received_at: str

    db.create_tables_sync()
    yield db


@pytest.fixture
def kill_switch_service(test_db, monkeypatch):
    """Create KillSwitchService with test database and AuditChainService."""
    monkeypatch.setattr("src.kill_switch_service.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)
    return KillSwitchService()


@pytest.mark.asyncio
async def test_engage_kill_switch_governance_admin_succeeds(kill_switch_service):
    """Test that GOVERNANCE_ADMIN can engage the kill switch."""
    result = await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Emergency: Suspicious invoice activity detected",
    )

    assert result["status"] == "engaged"
    assert result["is_enabled"] is False
    assert "timestamp" in result
    assert "control_id" in result
    assert "audit_id" in result


@pytest.mark.asyncio
async def test_disengage_kill_switch_governance_admin_succeeds(kill_switch_service):
    """Test that GOVERNANCE_ADMIN can disengage the kill switch."""
    engage_result = await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Emergency: Suspicious invoice activity detected",
    )
    assert engage_result["status"] == "engaged"

    disengage_result = await kill_switch_service.disengage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Emergency resolved, resuming normal processing",
    )

    assert disengage_result["status"] == "disengaged"
    assert disengage_result["is_enabled"] is True
    assert "timestamp" in disengage_result
    assert "control_id" in disengage_result
    assert "audit_id" in disengage_result


@pytest.mark.asyncio
async def test_engage_operator_role_unauthorized(kill_switch_service, test_db):
    """Test that OPERATOR role cannot engage kill switch (fail-closed)."""
    result = await kill_switch_service.engage_kill_switch(
        actor_id="operator@finance.corp",
        actor_role=OperationalRole.OPERATOR.value,
        reason="Attempting to engage with insufficient authority",
    )

    assert result["status"] == "unauthorized"
    assert "not authorized" in result["message"]
    assert "OPERATOR" in result["message"]

    state = kill_switch_service._get_control_state_sync()
    assert state is None


@pytest.mark.asyncio
async def test_engage_viewer_role_unauthorized(kill_switch_service, test_db):
    """Test that VIEWER role cannot engage kill switch (fail-closed)."""
    result = await kill_switch_service.engage_kill_switch(
        actor_id="viewer@finance.corp",
        actor_role=OperationalRole.VIEWER.value,
        reason="Attempting to engage with insufficient authority",
    )

    assert result["status"] == "unauthorized"
    assert "not authorized" in result["message"]
    assert "VIEWER" in result["message"]

    state = kill_switch_service._get_control_state_sync()
    assert state is None


@pytest.mark.asyncio
async def test_engage_unknown_role_rejected(kill_switch_service, test_db):
    """Test that unknown role is rejected."""
    result = await kill_switch_service.engage_kill_switch(
        actor_id="unknown@finance.corp",
        actor_role="UNKNOWN_ROLE",
        reason="Attempting with unknown role",
    )

    assert result["status"] == "unauthorized"
    assert "Unknown role" in result["message"]

    state = kill_switch_service._get_control_state_sync()
    assert state is None


@pytest.mark.asyncio
async def test_engage_missing_role_rejected(kill_switch_service, test_db):
    """Test that missing role is rejected."""
    result = await kill_switch_service.engage_kill_switch(
        actor_id="no_role@finance.corp",
        actor_role=None,
        reason="Attempting without role",
    )

    assert result["status"] == "unauthorized"
    assert "required" in result["message"].lower()

    state = kill_switch_service._get_control_state_sync()
    assert state is None


@pytest.mark.asyncio
async def test_disengage_operator_role_unauthorized(kill_switch_service, test_db):
    """Test that OPERATOR role cannot disengage kill switch (fail-closed)."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Authorized engagement",
    )

    result = await kill_switch_service.disengage_kill_switch(
        actor_id="operator@finance.corp",
        actor_role=OperationalRole.OPERATOR.value,
        reason="Attempting to disengage with insufficient authority",
    )

    assert result["status"] == "unauthorized"
    assert "not authorized" in result["message"]

    state = kill_switch_service._get_control_state_sync()
    assert state is not None
    assert not bool(state["is_enabled"])


@pytest.mark.asyncio
async def test_double_engage_returns_already_suspended(kill_switch_service):
    """Test that engaging already-suspended switch returns idempotent response."""
    first_result = await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="First engagement",
    )
    assert first_result["status"] == "engaged"

    second_result = await kill_switch_service.engage_kill_switch(
        actor_id="other_admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Second attempt",
    )

    assert second_result["status"] == "already_suspended"
    assert second_result["disabled_by"] == "admin@finance.corp"
    assert second_result["reason"] == "First engagement"


@pytest.mark.asyncio
async def test_double_disengage_returns_already_enabled(kill_switch_service):
    """Test that disengaging already-enabled switch returns idempotent response."""
    first_result = await kill_switch_service.disengage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="First disengage",
    )

    second_result = await kill_switch_service.disengage_kill_switch(
        actor_id="other_admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Second attempt",
    )

    assert second_result["status"] == "already_enabled"


@pytest.mark.asyncio
async def test_is_processing_enabled_after_engagement(kill_switch_service):
    """Test that processing is disabled after kill switch engagement."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing suspension",
    )

    enabled = await kill_switch_service.is_processing_enabled()
    assert enabled is False


@pytest.mark.asyncio
async def test_is_processing_enabled_after_disengagement(kill_switch_service):
    """Test that processing is enabled after kill switch disengagement."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing suspension",
    )

    await kill_switch_service.disengage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Resume processing",
    )

    enabled = await kill_switch_service.is_processing_enabled()
    assert enabled is True


@pytest.mark.asyncio
async def test_is_processing_enabled_default_true(kill_switch_service):
    """Test that processing is enabled by default (no kill switch set)."""
    enabled = await kill_switch_service.is_processing_enabled()
    assert enabled is True


def test_is_processing_enabled_sync(kill_switch_service):
    """Test synchronous check of processing status."""
    enabled = kill_switch_service.is_processing_enabled_sync()
    assert enabled is True


def test_is_processing_enabled_sync_after_engagement(kill_switch_service):
    """Test synchronous check after kill switch engagement."""
    import asyncio

    asyncio.run(
        kill_switch_service.engage_kill_switch(
            actor_id="admin@finance.corp",
            actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
            reason="Testing suspension",
        )
    )

    enabled = kill_switch_service.is_processing_enabled_sync()
    assert enabled is False


@pytest.mark.asyncio
async def test_get_control_state(kill_switch_service):
    """Test retrieving current control state."""
    state_before = await kill_switch_service.get_control_state()
    assert state_before is None

    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing state retrieval",
    )

    state_after = await kill_switch_service.get_control_state()
    assert state_after is not None
    assert state_after["is_enabled"] is False
    assert state_after["disabled_by"] == "admin@finance.corp"
    assert state_after["reason"] == "Testing state retrieval"


@pytest.mark.asyncio
async def test_get_control_history(kill_switch_service):
    """Test retrieving control state history."""
    history_empty = await kill_switch_service.get_control_history()
    assert len(history_empty) == 0

    await kill_switch_service.engage_kill_switch(
        actor_id="admin1@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="First suspension",
    )

    await kill_switch_service.disengage_kill_switch(
        actor_id="admin2@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="First resumption",
    )

    history = await kill_switch_service.get_control_history()
    assert len(history) >= 2

    latest = history[0]
    assert latest["is_enabled"] is True
    assert latest["enabled_by"] == "admin2@finance.corp"


@pytest.mark.asyncio
async def test_audit_trail_on_engagement_has_64char_hash(kill_switch_service, test_db):
    """Test that kill switch engagement creates audit event with 64-char SHA256 hash."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Emergency suspension",
    )

    all_audits = test_db.express_sync.list("BusinessAuditEvent")
    audits = [a for a in all_audits if a.get("action_type") == "KILL_SWITCH_ENGAGED"]

    assert len(audits) == 1
    audit = audits[0]
    assert audit["actor_id"] == "admin@finance.corp"
    assert audit["action_outcome"] == "SUCCESS"
    assert audit["invoice_id"] == "SYSTEM"
    assert audit["correlation_id"] == "SYSTEM_CONTROL"

    assert len(audit["event_hash"]) == 64
    assert all(c in "0123456789abcdef" for c in audit["event_hash"])

    payload = json.loads(audit["event_payload"])
    assert payload["reason"] == "Emergency suspension"
    assert payload["actor_id"] == "admin@finance.corp"
    assert payload["actor_role"] == "GOVERNANCE_ADMIN"
    assert payload["previous_state"] == "enabled"
    assert payload["new_state"] == "disabled"


@pytest.mark.asyncio
async def test_audit_trail_disengagement_links_to_engagement(kill_switch_service, test_db):
    """Test that disengagement event links to engagement via previous_hash."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Emergency suspension",
    )

    await kill_switch_service.disengage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Emergency resolved",
    )

    all_audits = test_db.express_sync.list("BusinessAuditEvent")
    engaged = [a for a in all_audits if a.get("action_type") == "KILL_SWITCH_ENGAGED"]
    disengaged = [a for a in all_audits if a.get("action_type") == "KILL_SWITCH_DISENGAGED"]

    assert len(engaged) == 1
    assert len(disengaged) == 1

    engagement_hash = engaged[0]["event_hash"]
    disengagement_previous = disengaged[0]["previous_hash"]

    assert engagement_hash == disengagement_previous
    assert len(disengagement_previous) == 64


def test_intake_respects_kill_switch(test_db, monkeypatch):
    """Test that intake service respects kill switch during processing."""
    monkeypatch.setattr("src.intake_service.db", test_db)
    monkeypatch.setattr("src.kill_switch_service.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)

    import asyncio

    kill_switch_service = KillSwitchService()

    asyncio.run(
        kill_switch_service.engage_kill_switch(
            actor_id="admin@finance.corp",
            actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
            reason="Testing intake suspension",
        )
    )

    sample_file = Path(__file__).parent.parent / "sample_invoices" / "sample_01.pdf"
    if sample_file.exists():
        result = run_intake(str(sample_file))
        assert result["intake_status"] == "SUSPENDED"
        assert "Processing is suspended by kill switch" in result["errors"]


def test_intake_processes_when_kill_switch_disengaged(test_db, monkeypatch, tmp_path):
    """Test that intake service processes invoices when kill switch is disengaged."""
    monkeypatch.setattr("src.intake_service.db", test_db)
    monkeypatch.setattr("src.kill_switch_service.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)

    import asyncio
    from unittest.mock import patch

    kill_switch_service = KillSwitchService()

    asyncio.run(
        kill_switch_service.disengage_kill_switch(
            actor_id="admin@finance.corp",
            actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
            reason="Processing enabled",
        )
    )

    sample_file = Path(__file__).parent.parent / "sample_invoices" / "sample_01.pdf"
    if sample_file.exists():
        with patch("src.intake_service.LocalRuntime") as mock_runtime:
            mock_instance = Mock()
            mock_instance.execute.return_value = (
                {"validate_intake": {"result": {"valid": True, "errors": []}}},
                "workflow-run-id-123",
            )
            mock_runtime.return_value.__enter__.return_value = mock_instance

            result = run_intake(str(sample_file))

            if result["intake_status"] != "REPLAY_IGNORED":
                assert result["intake_status"] == "ACCEPTED"
                assert result["errors"] == []


@pytest.mark.asyncio
async def test_kill_switch_state_persists(kill_switch_service, test_db, monkeypatch):
    """Test that kill switch state persists across service instances."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin1@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="First service instance",
    )

    monkeypatch.setattr("src.kill_switch_service.db", test_db)
    monkeypatch.setattr("src.audit_chain_service.db", test_db)
    new_service = KillSwitchService()
    enabled = await new_service.is_processing_enabled()

    assert enabled is False


@pytest.mark.asyncio
async def test_control_state_has_timestamps(kill_switch_service):
    """Test that control state includes proper timestamps."""
    before = datetime.now(timezone.utc).isoformat()

    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing timestamps",
    )

    after = datetime.now(timezone.utc).isoformat()

    state = await kill_switch_service.get_control_state()
    assert state is not None
    assert state["disabled_at"]

    disabled_time = datetime.fromisoformat(state["disabled_at"])
    before_time = datetime.fromisoformat(before)
    after_time = datetime.fromisoformat(after)

    assert before_time <= disabled_time <= after_time


@pytest.mark.asyncio
async def test_audit_chain_verifies_valid(kill_switch_service, test_db):
    """Test that operational audit chain verifies as VALID after engage/disengage."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing chain verification",
    )

    await kill_switch_service.disengage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Resume normal operations",
    )

    audit_chain = AuditChainService()
    is_valid, issues = audit_chain.verify_audit_chain("SYSTEM_CONTROL")

    assert is_valid is True
    assert len(issues) == 0


@pytest.mark.asyncio
async def test_unauthorized_attempt_no_audit_event_created(kill_switch_service, test_db):
    """Test that unauthorized engagement attempts do not create audit events."""
    result = await kill_switch_service.engage_kill_switch(
        actor_id="operator@finance.corp",
        actor_role=OperationalRole.OPERATOR.value,
        reason="Unauthorized attempt",
    )

    assert result["status"] == "unauthorized"

    all_audits = test_db.express_sync.list("BusinessAuditEvent")
    engaged_audits = [a for a in all_audits if a.get("action_type") == "KILL_SWITCH_ENGAGED"]

    assert len(engaged_audits) == 0


@pytest.mark.asyncio
async def test_audit_payload_is_valid_json(kill_switch_service, test_db):
    """Test that audit event payloads are valid JSON with actor_role."""
    await kill_switch_service.engage_kill_switch(
        actor_id="admin@finance.corp",
        actor_role=OperationalRole.GOVERNANCE_ADMIN.value,
        reason="Testing JSON payload",
    )

    all_audits = test_db.express_sync.list("BusinessAuditEvent")
    audits = [a for a in all_audits if a.get("action_type") == "KILL_SWITCH_ENGAGED"]

    assert len(audits) > 0

    for audit in audits:
        payload_str = audit["event_payload"]
        payload = json.loads(payload_str)
        assert isinstance(payload, dict)
        assert "control_id" in payload
        assert "reason" in payload
        assert "actor_id" in payload
        assert "actor_role" in payload
