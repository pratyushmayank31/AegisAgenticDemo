"""
Kill Switch and Processing Suspension Service for LdcDemo.

Provides system-level control to suspend invoice intake processing.
Maintains full audit trail of all state changes using BusinessAuditEvent with
cryptographic hash-linking via AuditChainService (Step 7A integration).

Implements:
1. Engage kill switch (stop new invoice intake) — GOVERNANCE_ADMIN only
2. Disengage kill switch (resume processing) — GOVERNANCE_ADMIN only
3. Query current processing status
4. Audit trail recording for compliance with tamper-evident hashing

Authorization Model:
- GOVERNANCE_ADMIN: Can engage/disengage kill switch
- OPERATOR: Cannot engage/disengage (fail-closed)
- VIEWER: Cannot engage/disengage (fail-closed)
- Unknown roles: Rejected (fail-closed)

Note: This is demo-scope. Live identity-provider verification of actor_role
claims is outside this scope; actor_role is supplied by the application layer.
"""

import json
import uuid
from enum import Enum
from datetime import datetime, timezone
from typing import Optional

from src.database import db
from src.audit_chain_service import AuditChainService


class OperationalRole(str, Enum):
    """Roles that control kill switch authorization."""
    GOVERNANCE_ADMIN = "GOVERNANCE_ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


def generate_id(prefix: str) -> str:
    """Generate a readable ID with given prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _authorize_kill_switch_operation(actor_role: Optional[str]) -> tuple[bool, str]:
    """
    Validate that actor has authorization to engage/disengage kill switch.

    Args:
        actor_role: Role claimed by actor (must be a valid OperationalRole)

    Returns:
        (is_authorized, error_message) tuple
        - is_authorized: True only if role is GOVERNANCE_ADMIN
        - error_message: Reason for denial, or "" if authorized
    """
    if not actor_role:
        return False, "actor_role is required"

    try:
        role = OperationalRole(actor_role)
    except ValueError:
        return False, f"Unknown role: {actor_role}"

    if role != OperationalRole.GOVERNANCE_ADMIN:
        return False, f"Authority {role.value} is not authorized to control kill switch. Only GOVERNANCE_ADMIN can engage/disengage."

    return True, ""


class KillSwitchService:
    """Manages system kill switch and processing suspension state with audit chain integration."""

    KILL_SWITCH_ID = "KILL_SWITCH_001"
    CONTROL_NAME = "processing_enabled"
    SYSTEM_CONTROL_CORRELATION_ID = "SYSTEM_CONTROL"

    def __init__(self):
        """Initialize the kill switch service with audit chain support."""
        self.audit_chain = AuditChainService()

    async def engage_kill_switch(
        self,
        actor_id: str,
        actor_role: Optional[str],
        reason: str,
    ) -> dict:
        """
        Engage the kill switch to suspend invoice intake processing.

        Authorization: Only GOVERNANCE_ADMIN can engage.

        Args:
            actor_id: ID of actor engaging the kill switch
            actor_role: Role of actor (must be GOVERNANCE_ADMIN); fail-closed otherwise
            reason: Reason for suspension (stored as safe metadata only)

        Returns:
            dict with status, timestamp, and control state
            Unauthorized attempts return {"status": "unauthorized", ...} without state mutation
        """
        try:
            is_authorized, auth_error = _authorize_kill_switch_operation(actor_role)
            if not is_authorized:
                return {
                    "status": "unauthorized",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": auth_error,
                    "reason_detail": f"actor_id='{actor_id}' with role='{actor_role}' cannot engage kill switch",
                }

            now_iso = datetime.now(timezone.utc).isoformat()

            existing = self._get_control_state_sync()
            if existing and not bool(existing.get("is_enabled", True)):
                return {
                    "status": "already_suspended",
                    "timestamp": now_iso,
                    "message": "Kill switch already engaged",
                    "disabled_by": existing.get("disabled_by", ""),
                    "disabled_at": existing.get("disabled_at", ""),
                    "reason": existing.get("reason", ""),
                }

            record_id = generate_id("CTRL")
            audit_id = generate_id("AUD")

            state_data = {
                "id": record_id,
                "control_name": self.CONTROL_NAME,
                "is_enabled": False,
                "enabled_by": "",
                "enabled_at": "",
                "disabled_by": actor_id,
                "disabled_at": now_iso,
                "reason": reason,
                "last_modified_at": now_iso,
            }

            db.express_sync.create("SystemControlState", state_data)

            audit_payload = {
                "control_id": record_id,
                "control_name": self.CONTROL_NAME,
                "previous_state": "enabled",
                "new_state": "disabled",
                "reason": reason,
                "actor_id": actor_id,
                "actor_role": actor_role,
            }

            audit_data = {
                "id": audit_id,
                "invoice_id": "SYSTEM",
                "correlation_id": self.SYSTEM_CONTROL_CORRELATION_ID,
                "actor_id": actor_id,
                "action_type": "KILL_SWITCH_ENGAGED",
                "action_outcome": "SUCCESS",
                "event_payload": json.dumps(audit_payload),
                "previous_hash": "",
                "event_hash": "",
                "event_timestamp": now_iso,
            }

            audit_data = self.audit_chain.link_audit_event(
                audit_data, self.SYSTEM_CONTROL_CORRELATION_ID
            )

            db.express_sync.create("BusinessAuditEvent", audit_data)

            return {
                "status": "engaged",
                "timestamp": now_iso,
                "message": "Kill switch engaged successfully",
                "control_id": record_id,
                "audit_id": audit_id,
                "is_enabled": False,
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def disengage_kill_switch(
        self,
        actor_id: str,
        actor_role: Optional[str],
        reason: str,
    ) -> dict:
        """
        Disengage the kill switch to resume invoice intake processing.

        Authorization: Only GOVERNANCE_ADMIN can disengage.

        Args:
            actor_id: ID of actor disengaging the kill switch
            actor_role: Role of actor (must be GOVERNANCE_ADMIN); fail-closed otherwise
            reason: Reason for resumption (stored as safe metadata only)

        Returns:
            dict with status, timestamp, and control state
            Unauthorized attempts return {"status": "unauthorized", ...} without state mutation
        """
        try:
            is_authorized, auth_error = _authorize_kill_switch_operation(actor_role)
            if not is_authorized:
                return {
                    "status": "unauthorized",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": auth_error,
                    "reason_detail": f"actor_id='{actor_id}' with role='{actor_role}' cannot disengage kill switch",
                }

            now_iso = datetime.now(timezone.utc).isoformat()

            existing = self._get_control_state_sync()
            if existing and bool(existing.get("is_enabled", True)):
                return {
                    "status": "already_enabled",
                    "timestamp": now_iso,
                    "message": "Kill switch already disengaged",
                    "enabled_by": existing.get("enabled_by", ""),
                    "enabled_at": existing.get("enabled_at", ""),
                }

            record_id = generate_id("CTRL")
            audit_id = generate_id("AUD")

            state_data = {
                "id": record_id,
                "control_name": self.CONTROL_NAME,
                "is_enabled": True,
                "enabled_by": actor_id,
                "enabled_at": now_iso,
                "disabled_by": "",
                "disabled_at": "",
                "reason": reason,
                "last_modified_at": now_iso,
            }

            db.express_sync.create("SystemControlState", state_data)

            audit_payload = {
                "control_id": record_id,
                "control_name": self.CONTROL_NAME,
                "previous_state": "disabled",
                "new_state": "enabled",
                "reason": reason,
                "actor_id": actor_id,
                "actor_role": actor_role,
            }

            audit_data = {
                "id": audit_id,
                "invoice_id": "SYSTEM",
                "correlation_id": self.SYSTEM_CONTROL_CORRELATION_ID,
                "actor_id": actor_id,
                "action_type": "KILL_SWITCH_DISENGAGED",
                "action_outcome": "SUCCESS",
                "event_payload": json.dumps(audit_payload),
                "previous_hash": "",
                "event_hash": "",
                "event_timestamp": now_iso,
            }

            audit_data = self.audit_chain.link_audit_event(
                audit_data, self.SYSTEM_CONTROL_CORRELATION_ID
            )

            db.express_sync.create("BusinessAuditEvent", audit_data)

            return {
                "status": "disengaged",
                "timestamp": now_iso,
                "message": "Kill switch disengaged successfully",
                "control_id": record_id,
                "audit_id": audit_id,
                "is_enabled": True,
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def is_processing_enabled(self) -> bool:
        """
        Check if invoice intake processing is currently enabled.

        Returns:
            True if processing is enabled, False if kill switch is engaged.
        """
        try:
            state = self._get_control_state_sync()
            if state is None:
                return True
            return bool(state.get("is_enabled", True))
        except Exception:
            return True

    async def get_control_state(self) -> Optional[dict]:
        """
        Get current control state.

        Returns:
            dict with current state or None if not set
        """
        try:
            state = self._get_control_state_sync()
            if state is None:
                return None

            return {
                "id": state.get("id", ""),
                "control_name": state.get("control_name", ""),
                "is_enabled": bool(state.get("is_enabled", True)),
                "enabled_by": state.get("enabled_by", ""),
                "enabled_at": state.get("enabled_at", ""),
                "disabled_by": state.get("disabled_by", ""),
                "disabled_at": state.get("disabled_at", ""),
                "reason": state.get("reason", ""),
                "last_modified_at": state.get("last_modified_at", ""),
            }
        except Exception:
            return None

    def _get_control_state_sync(self) -> Optional[dict]:
        """
        Get the most recent control state record (synchronously).

        Returns:
            dict with control state or None if not found
        """
        try:
            all_records = db.express_sync.list("SystemControlState")

            if not all_records:
                return None

            filtered = [r for r in all_records if r.get("control_name") == self.CONTROL_NAME]

            if not filtered:
                return None

            latest = max(filtered, key=lambda x: x.get("last_modified_at") or x.get("id", ""))
            return latest
        except Exception:
            return None

    async def get_control_history(self, limit: int = 10) -> list:
        """
        Get history of control state changes.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of control state changes in reverse chronological order
        """
        try:
            all_records = db.express_sync.list("SystemControlState")

            if not all_records:
                return []

            filtered = [r for r in all_records if r.get("control_name") == self.CONTROL_NAME]

            if not filtered:
                return []

            sorted_results = sorted(
                filtered,
                key=lambda x: x.get("last_modified_at") or x.get("id", ""),
                reverse=True
            )

            return [
                {
                    "id": state.get("id", ""),
                    "is_enabled": bool(state.get("is_enabled", True)),
                    "enabled_by": state.get("enabled_by", ""),
                    "enabled_at": state.get("enabled_at", ""),
                    "disabled_by": state.get("disabled_by", ""),
                    "disabled_at": state.get("disabled_at", ""),
                    "reason": state.get("reason", ""),
                    "last_modified_at": state.get("last_modified_at", ""),
                }
                for state in sorted_results[:limit]
            ]
        except Exception:
            return []

    def is_processing_enabled_sync(self) -> bool:
        """
        Synchronous check if invoice intake processing is currently enabled.

        Returns:
            True if processing is enabled, False if kill switch is engaged.
        """
        try:
            state = self._get_control_state_sync()
            if state is None:
                return True
            return bool(state.get("is_enabled", True))
        except Exception:
            return True
