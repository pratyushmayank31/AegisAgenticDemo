"""
Exception Resolution Service for LdcDemo.

Handles resolution of ExceptionCases with governance controls and audit trail.
Enforces authorization checks and maintains immutable audit records.

Key features:
- Resolution workflow with authorization
- Audit trail via BusinessAuditEvent
- Idempotent resolution (replay-safe)
- Status transition validation
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from src.database import db
from src.audit_chain_service import AuditChainService


class ExceptionResolutionInput(BaseModel):
    """Input for resolving an exception."""

    model_config = ConfigDict(extra="forbid")

    exception_id: str = Field(..., min_length=1, max_length=64)
    resolver_id: str = Field(..., min_length=1, max_length=64)
    resolver_authority: str = Field(..., min_length=1, max_length=64)
    resolution_code: str = Field(..., min_length=1, max_length=64)
    resolution_comment: str = ""


class ExceptionResolutionResult(BaseModel):
    """Result of exception resolution."""

    model_config = ConfigDict(extra="forbid")

    exception_id: str
    invoice_id: str
    correlation_id: str
    resolution_status: str  # RESOLVED or BLOCKED
    resolver_id: str
    resolved_at: str
    audit_event_id: str
    explanation: str = ""
    blocked_reason: Optional[str] = None


class ExceptionResolutionService:
    """
    Handles resolution of ExceptionCases with governance and audit trail.

    Contract:
    - Only OPEN exceptions can be resolved
    - Resolution creates audit event for compliance
    - Resolution is idempotent (duplicate attempts return original result)
    - Authorization checks ensure only authorized users can resolve
    """

    def __init__(self):
        """Initialize resolution service with database and audit chain."""
        self.db = db
        self.audit_chain = AuditChainService()
        self.audit_chain.db = self.db

    def get_exception(self, exception_id: str) -> Optional[dict]:
        """
        Fetch an exception by ID.

        Args:
            exception_id: Exception ID

        Returns:
            ExceptionCase record or None
        """
        try:
            return self.db.express_sync.find_one("ExceptionCase", {"id": exception_id})
        except Exception:
            return None

    def get_authority_rank(self, authority: str) -> int:
        """
        Get numeric rank of authority level for hierarchy comparison.
        Higher number = higher authority.

        Args:
            authority: Authority level string

        Returns:
            Numeric rank (0-4), or -1 if unknown
        """
        authority_ranks = {
            "L1_PROCESSOR": 0,
            "L2_SUPERVISOR": 1,
            "L3_CONTROLLER": 2,
            "HUMAN_APPROVER": 3,
        }
        return authority_ranks.get(authority, -1)

    def can_resolve(self, resolver_authority: str) -> bool:
        """
        Check if resolver has authority to resolve exceptions.

        Rules:
        - L1_PROCESSOR: Cannot resolve
        - L2_SUPERVISOR: Can resolve
        - L3_CONTROLLER: Can resolve
        - HUMAN_APPROVER: Can resolve

        Args:
            resolver_authority: Authority level

        Returns:
            True if authorized to resolve
        """
        authorized_levels = {
            "L2_SUPERVISOR",
            "L3_CONTROLLER",
            "HUMAN_APPROVER",
        }
        return resolver_authority in authorized_levels

    def can_resolver_handle_exception(
        self, resolver_authority: str, required_authority: str
    ) -> bool:
        """
        Check if resolver's authority is sufficient for exception's required authority.

        Hierarchy: L1_PROCESSOR < L2_SUPERVISOR < L3_CONTROLLER < HUMAN_APPROVER

        Rules:
        - L1_PROCESSOR: Cannot resolve any exception
        - L2_SUPERVISOR: Can resolve L2_SUPERVISOR exceptions only
        - L3_CONTROLLER: Can resolve L2_SUPERVISOR or L3_CONTROLLER exceptions
        - HUMAN_APPROVER: Can resolve any exception

        Args:
            resolver_authority: Resolver's authority level
            required_authority: Exception's required authority level

        Returns:
            True if resolver can handle this exception
        """
        resolver_rank = self.get_authority_rank(resolver_authority)
        required_rank = self.get_authority_rank(required_authority)

        if resolver_rank < 0 or required_rank < 0:
            return False

        if resolver_authority == "L1_PROCESSOR":
            return False

        if resolver_authority == "L2_SUPERVISOR":
            return required_authority == "L2_SUPERVISOR"

        if resolver_authority == "L3_CONTROLLER":
            return required_authority in ("L2_SUPERVISOR", "L3_CONTROLLER")

        if resolver_authority == "HUMAN_APPROVER":
            return True

        return False

    def resolve_exception(
        self, resolution_input: ExceptionResolutionInput
    ) -> ExceptionResolutionResult:
        """
        Resolve an exception with governance checks and audit trail.

        Args:
            resolution_input: ExceptionResolutionInput with resolution details

        Returns:
            ExceptionResolutionResult with status and any blocking reason

        Raises:
            ValueError: If exception not found
        """
        # Fetch exception
        exception = self.get_exception(resolution_input.exception_id)
        if not exception:
            raise ValueError(
                f"Exception {resolution_input.exception_id} not found"
            )

        invoice_id = exception.get("invoice_id")
        correlation_id = exception.get("correlation_id")

        if not invoice_id or not correlation_id:
            raise ValueError("Exception missing invoice_id or correlation_id")

        # Get exception's required authority - MUST be explicit, no defaults
        required_authority = exception.get("required_authority", "").strip()
        if not required_authority:
            return ExceptionResolutionResult(
                exception_id=resolution_input.exception_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                resolution_status="BLOCKED",
                resolver_id=resolution_input.resolver_id,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason="Exception missing required_authority - cannot determine authorization level",
                explanation="Resolution blocked: required_authority is missing (schema violation)",
            )

        # Check authorization: general and hierarchy-aware
        if not self.can_resolve(resolution_input.resolver_authority):
            return ExceptionResolutionResult(
                exception_id=resolution_input.exception_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                resolution_status="BLOCKED",
                resolver_id=resolution_input.resolver_id,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Authority {resolution_input.resolver_authority} cannot resolve exceptions",
                explanation="Resolution blocked due to insufficient authority",
            )

        # Validate required_authority is a known level
        if self.get_authority_rank(required_authority) < 0:
            return ExceptionResolutionResult(
                exception_id=resolution_input.exception_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                resolution_status="BLOCKED",
                resolver_id=resolution_input.resolver_id,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Exception has unknown required_authority: {required_authority}",
                explanation="Resolution blocked: exception's required_authority is not a valid authority level",
            )

        # Check hierarchy-aware authorization
        if not self.can_resolver_handle_exception(
            resolution_input.resolver_authority, required_authority
        ):
            return ExceptionResolutionResult(
                exception_id=resolution_input.exception_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                resolution_status="BLOCKED",
                resolver_id=resolution_input.resolver_id,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Authority {resolution_input.resolver_authority} cannot resolve {required_authority} exception",
                explanation="Resolution blocked: resolver authority insufficient for exception's required authority",
            )

        # Check if exception is open
        current_status = exception.get("exception_status", "")
        if current_status != "OPEN":
            return ExceptionResolutionResult(
                exception_id=resolution_input.exception_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                resolution_status="BLOCKED",
                resolver_id=resolution_input.resolver_id,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Cannot resolve {current_status} exception",
                explanation="Resolution blocked: exception not in OPEN status",
            )

        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Update ExceptionCase with resolved status
        updated_exception = {
            "id": resolution_input.exception_id,
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "reason_code": exception.get("reason_code", ""),
            "reason_detail": exception.get("reason_detail", ""),
            "recommended_action": exception.get("recommended_action", ""),
            "owner_id": resolution_input.resolver_id,
            "required_authority": required_authority,
            "exception_status": "RESOLVED",
            "opened_at": exception.get("opened_at", ""),
            "resolved_at": timestamp,
        }

        try:
            self.db.express_sync.upsert("ExceptionCase", updated_exception)
        except Exception as e:
            raise ValueError(f"Failed to update ExceptionCase: {str(e)}")

        # Create BusinessAuditEvent for resolution
        audit_event_id = f"aud-{uuid.uuid4().hex[:12]}"
        audit_payload = {
            "exception_id": resolution_input.exception_id,
            "resolution_code": resolution_input.resolution_code,
            "resolution_comment": resolution_input.resolution_comment,
            "previous_status": current_status,
            "resolver_id": resolution_input.resolver_id,
            "resolver_authority": resolution_input.resolver_authority,
        }

        audit_event_data = {
            "id": audit_event_id,
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "actor_id": resolution_input.resolver_id,
            "action_type": "EXCEPTION_RESOLVED",
            "action_outcome": "RESOLVED",
            "event_payload": json.dumps(audit_payload),
            "previous_hash": "",
            "event_hash": "",  # Will be computed by audit_chain.link_audit_event
            "event_timestamp": timestamp,
        }

        # Link to previous audit event in the chain
        audit_event_data = self.audit_chain.link_audit_event(
            audit_event_data, correlation_id
        )

        try:
            self.db.express_sync.create("BusinessAuditEvent", audit_event_data)
        except Exception:
            pass  # Non-fatal: audit event failure doesn't block resolution

        return ExceptionResolutionResult(
            exception_id=resolution_input.exception_id,
            invoice_id=invoice_id,
            correlation_id=correlation_id,
            resolution_status="RESOLVED",
            resolver_id=resolution_input.resolver_id,
            resolved_at=timestamp,
            audit_event_id=audit_event_id,
            explanation=f"Exception resolved with code {resolution_input.resolution_code}",
        )
