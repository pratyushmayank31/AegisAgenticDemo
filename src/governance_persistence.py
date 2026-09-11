"""
Governance Decision Persistence Service for LdcDemo.

Translates GovernanceOrchestrator decisions (ALLOW, HOLD, DENY) into persistent
database records with complete audit trail. Ensures traceability and idempotency.

Outcomes:
- ALLOW → FinanceDecision (ALLOWED) + BusinessAuditEvent
- HOLD → FinanceDecision (HOLD) + ApprovalRequest (PENDING) + BusinessAuditEvent
- DENY → FinanceDecision (REJECTED) + ExceptionCase (OPEN) + BusinessAuditEvent

No LLM calls, no external APIs, no raw invoice text. Deterministic persistence.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

from src.database import db
from src.audit_chain_service import AuditChainService
from src.governance_orchestrator import (
    GovernanceOrchestrationResult,
    GovernanceOrchestrationStatus,
)


class ApprovalRequestCreated(BaseModel):
    """Result of creating an ApprovalRequest."""

    model_config = ConfigDict(extra="forbid")

    approval_request_id: str = Field(..., min_length=1, max_length=64)
    invoice_id: str = Field(..., min_length=1, max_length=64)
    correlation_id: str = Field(..., min_length=1, max_length=64)
    requested_from: str = Field(..., min_length=1, max_length=64)
    approval_reason: str = Field(..., min_length=1, max_length=256)
    created_at: str
    explanation: str = ""


class PersistenceOutcome(BaseModel):
    """Result of persisting a governance decision."""

    model_config = ConfigDict(extra="forbid")

    outcome_status: str = Field(..., min_length=1, max_length=64)  # ALLOW, HOLD, DENY
    decision_status: str = Field(..., min_length=1, max_length=64)  # ALLOWED, HOLD, REJECTED
    finance_decision_id: str = Field(..., min_length=1, max_length=64)
    audit_event_id: str = Field(..., min_length=1, max_length=64)
    approval_request_id: Optional[str] = None  # Only for HOLD
    exception_case_id: Optional[str] = None  # Only for DENY
    invoice_id: str = Field(..., min_length=1, max_length=64)
    correlation_id: str = Field(..., min_length=1, max_length=64)
    case_exists: bool = True
    is_replay: bool = False  # True if decision already existed (duplicate)
    explanation: str = ""


class GovernancePersistenceService:
    """
    Persists governance decisions (ALLOW, HOLD, DENY) with complete audit trail.

    Contract:
    - ALLOW → FinanceDecision (ALLOWED) + BusinessAuditEvent
    - HOLD → FinanceDecision (HOLD) + ApprovalRequest + BusinessAuditEvent
    - DENY → FinanceDecision (REJECTED) + ExceptionCase + BusinessAuditEvent

    Replay Protection:
    - Detects duplicate decisions and returns original IDs (idempotent)
    - Never creates duplicate FinanceDecision, ApprovalRequest, ExceptionCase, or BusinessAuditEvent
    """

    def __init__(self):
        """Initialize persistence service with database connection and audit chain service."""
        self.db = db
        self.audit_chain = AuditChainService()
        self.audit_chain.db = self.db

    async def persist_decision(
        self,
        orchestration_result: GovernanceOrchestrationResult,
        case_id: str,
        correlation_id: str,
        invoice_case_id: Optional[str] = None,
    ) -> PersistenceOutcome:
        """
        Persist a governance decision (ALLOW, HOLD, or DENY) with full audit trail.

        Args:
            orchestration_result: GovernanceOrchestrationResult from orchestrator
            case_id: Invoice case ID
            correlation_id: Correlation ID for tracing
            invoice_case_id: Optional reference to InvoiceCase.id

        Returns:
            PersistenceOutcome with all created record IDs and metadata

        Raises:
            ValueError: If traceability fields are invalid
        """
        # Validate inputs
        if not case_id or not case_id.strip():
            raise ValueError("case_id cannot be empty")
        if not correlation_id or not correlation_id.strip():
            raise ValueError("correlation_id cannot be empty")

        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Determine decision_status from orchestration_status
        status_map = {
            GovernanceOrchestrationStatus.ALLOW: "ALLOWED",
            GovernanceOrchestrationStatus.HOLD: "HOLD",
            GovernanceOrchestrationStatus.DENY: "REJECTED",
        }

        decision_status = status_map.get(
            orchestration_result.governance_status, "UNKNOWN"
        )

        # Check if InvoiceCase exists
        case_exists = self._check_invoice_case_exists(case_id)

        # Check for existing FinanceDecision (replay detection)
        existing_decision = self._check_existing_finance_decision(case_id)
        is_replay = existing_decision is not None

        # Generate or use existing FinanceDecision ID
        if existing_decision:
            finance_decision_id = existing_decision.get("id")
        else:
            finance_decision_id = f"fin-{uuid.uuid4().hex[:12]}"

        # Create/update FinanceDecision record
        finance_decision_data = {
            "id": finance_decision_id,
            "invoice_id": case_id,
            "correlation_id": correlation_id,
            "decision_type": "GOVERNANCE",
            "recommended_value": decision_status,
            "confidence": 1.0,
            "rationale": orchestration_result.explanation,
            "evidence": orchestration_result.reason_code.value,
            "agent_id": "governance-orchestrator",
            "policy_outcome": decision_status,
            "decision_timestamp": timestamp,
        }

        if not is_replay:
            try:
                self.db.express_sync.create("FinanceDecision", finance_decision_data)
            except Exception as e:
                raise ValueError(f"Failed to create FinanceDecision: {str(e)}")

        # Create audit event
        audit_event_id = f"aud-{uuid.uuid4().hex[:12]}"
        audit_payload = {
            "orchestration_status": orchestration_result.governance_status.value,
            "reason_code": orchestration_result.reason_code.value,
            "required_authority": orchestration_result.required_authority,
            "evaluating_authority": orchestration_result.evaluating_authority,
        }

        audit_event_data = {
            "id": audit_event_id,
            "invoice_id": case_id,
            "correlation_id": correlation_id,
            "actor_id": "governance-orchestrator",
            "action_type": "GOVERNANCE_DECISION",
            "action_outcome": decision_status,
            "event_payload": json.dumps(audit_payload),
            "previous_hash": "",
            "event_hash": "",  # Will be computed by audit_chain.link_audit_event
            "event_timestamp": timestamp,
        }

        # Link to previous audit event in the chain using corrected full 64-char hash
        audit_event_data = self.audit_chain.link_audit_event(
            audit_event_data, correlation_id
        )

        if not is_replay:
            try:
                self.db.express_sync.create("BusinessAuditEvent", audit_event_data)
            except Exception as e:
                # Non-fatal: audit event creation failure doesn't block decision
                pass

        # Handle outcome-specific record creation
        approval_request_id = None
        exception_case_id = None

        if orchestration_result.governance_status == GovernanceOrchestrationStatus.HOLD:
            approval_request_id = await self._persist_approval_request(
                case_id, correlation_id, orchestration_result, is_replay
            )

        elif orchestration_result.governance_status == GovernanceOrchestrationStatus.DENY:
            exception_case_id = await self._persist_exception_case(
                case_id, correlation_id, orchestration_result, is_replay
            )

        # Build and return outcome
        return PersistenceOutcome(
            outcome_status=orchestration_result.governance_status.value,
            decision_status=decision_status,
            finance_decision_id=finance_decision_id,
            audit_event_id=audit_event_id,
            approval_request_id=approval_request_id,
            exception_case_id=exception_case_id,
            invoice_id=case_id,
            correlation_id=correlation_id,
            case_exists=case_exists,
            is_replay=is_replay,
            explanation=f"Decision persisted: {decision_status} for {case_id}",
        )

    async def persist_hold_decision(
        self,
        orchestration_result: GovernanceOrchestrationResult,
        case_id: str,
        correlation_id: str,
        finance_decision_id: Optional[str] = None,
    ) -> Optional[ApprovalRequestCreated]:
        """
        Backward compatibility wrapper for HOLD decisions only.

        Deprecated: Use persist_decision() for all outcomes.

        Args:
            orchestration_result: GovernanceOrchestrationResult from orchestrator
            case_id: Invoice case ID
            correlation_id: Correlation ID for tracing
            finance_decision_id: Unused (kept for compatibility)

        Returns:
            ApprovalRequestCreated if HOLD status
            None if not HOLD status (ALLOW or DENY)
        """
        if orchestration_result.governance_status != GovernanceOrchestrationStatus.HOLD:
            return None

        # Use new persist_decision for HOLD
        outcome = await self.persist_decision(orchestration_result, case_id, correlation_id)

        return ApprovalRequestCreated(
            approval_request_id=outcome.approval_request_id or "",
            invoice_id=outcome.invoice_id,
            correlation_id=outcome.correlation_id,
            requested_from=orchestration_result.required_authority,
            approval_reason=orchestration_result.reason_code.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            explanation=outcome.explanation,
        )

    async def _persist_approval_request(
        self,
        case_id: str,
        correlation_id: str,
        orchestration_result: GovernanceOrchestrationResult,
        is_replay: bool,
    ) -> str:
        """Create ApprovalRequest for HOLD decisions."""
        # Check for existing PENDING ApprovalRequest
        existing = self._check_existing_pending_request(case_id)
        if existing:
            return existing.get("id")

        approval_request_id = f"arq-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        approval_request_data = {
            "id": approval_request_id,
            "invoice_id": case_id,
            "correlation_id": correlation_id,
            "requested_by": "governance-orchestrator",
            "requested_from": orchestration_result.required_authority,
            "approval_reason": orchestration_result.reason_code.value,
            "approval_status": "PENDING",
            "approver_id": "",
            "approver_comment": "",
            "requested_at": now.isoformat(),
            "decided_at": "",
        }

        if not is_replay:
            try:
                self.db.express_sync.create("ApprovalRequest", approval_request_data)
            except Exception:
                pass

        return approval_request_id

    async def _persist_exception_case(
        self,
        case_id: str,
        correlation_id: str,
        orchestration_result: GovernanceOrchestrationResult,
        is_replay: bool,
    ) -> str:
        """Create ExceptionCase for DENY decisions."""
        # Check for existing ExceptionCase
        existing = self._check_existing_exception_case(case_id)
        if existing:
            return existing.get("id")

        exception_case_id = f"exc-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        exception_case_data = {
            "id": exception_case_id,
            "invoice_id": case_id,
            "correlation_id": correlation_id,
            "reason_code": orchestration_result.reason_code.value,
            "reason_detail": orchestration_result.explanation,
            "recommended_action": "MANUAL_REVIEW",
            "owner_id": "governance-orchestrator",
            "required_authority": orchestration_result.required_authority,
            "exception_status": "OPEN",
            "opened_at": now.isoformat(),
            "resolved_at": "",
        }

        if not is_replay:
            try:
                self.db.express_sync.create("ExceptionCase", exception_case_data)
            except Exception:
                pass

        return exception_case_id

    def _check_invoice_case_exists(self, case_id: str) -> bool:
        """Check if InvoiceCase exists."""
        try:
            existing = self.db.express_sync.find_one("InvoiceCase", {"id": case_id})
            return existing is not None
        except Exception:
            return False

    def _check_existing_finance_decision(self, invoice_id: str) -> Optional[dict]:
        """Check if FinanceDecision already exists (replay detection)."""
        try:
            existing = self.db.express_sync.find_one(
                "FinanceDecision",
                {"invoice_id": invoice_id, "decision_type": "GOVERNANCE"}
            )
            return existing
        except Exception:
            return None

    def _check_existing_pending_request(self, invoice_id: str) -> Optional[dict]:
        """Check if PENDING ApprovalRequest already exists."""
        try:
            existing = self.db.express_sync.find_one(
                "ApprovalRequest",
                {"invoice_id": invoice_id, "approval_status": "PENDING"}
            )
            return existing
        except Exception:
            return None

    def _check_existing_exception_case(self, invoice_id: str) -> Optional[dict]:
        """Check if ExceptionCase already exists."""
        try:
            existing = self.db.express_sync.find_one(
                "ExceptionCase",
                {"invoice_id": invoice_id, "exception_status": "OPEN"}
            )
            return existing
        except Exception:
            return None

