"""
Human Approval Workflow Service for LdcDemo.

Processes pending ApprovalRequests with complete governance controls:
1. Maker-Checker: Approver cannot equal request creator
2. Authority Enforcement: Approver authority level must meet requirement
3. Decision Immutability: Cannot change APPROVED→REJECTED or vice versa
4. Rejection Visibility: REJECT creates one ExceptionCase (replay-safe)
5. Posting Protection: No PostingRecord created from approval
6. Audit Trail: Structured events with compliance metadata

Uses Kailash DataFlow ORM for persistence with complete audit trail.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict

from src.database import db
from src.authority_hierarchy import AuthorityLevel
from src.audit_chain_service import AuditChainService


class ApprovalDecisionInput(BaseModel):
    """Input for recording an approval decision."""

    model_config = ConfigDict(extra="forbid")

    approval_request_id: str = Field(..., min_length=1, max_length=64)
    approver_id: str = Field(..., min_length=1, max_length=64)
    approver_authority: str = Field(..., min_length=1, max_length=64)
    decision: str = Field(..., pattern="^(APPROVED|REJECTED)$")
    approver_comment: str = ""


class ApprovalWorkflowResult(BaseModel):
    """Result of approval workflow processing."""

    model_config = ConfigDict(extra="forbid")

    approval_request_id: str = Field(..., min_length=1, max_length=64)
    invoice_id: str = Field(..., min_length=1, max_length=64)
    correlation_id: str = Field(..., min_length=1, max_length=64)
    decision: str  # APPROVED, REJECTED, or BLOCKED
    approver_id: str
    invoice_case_status: str  # APPROVED, REJECTED, or PENDING (if blocked)
    approval_request_status: str  # APPROVED, REJECTED, or PENDING (if blocked)
    decided_at: str
    audit_event_id: str
    exception_case_id: Optional[str] = None
    explanation: str = ""
    blocked_reason: Optional[str] = None


class ApprovalWorkflow:
    """
    Orchestrates human approval workflows with complete governance controls.

    Enforces:
    1. Maker-checker (approver != request creator)
    2. Authority enforcement (approver level >= required level)
    3. Decision immutability (no status reversals)
    4. Rejection visibility (one ExceptionCase per rejection)
    5. Posting protection (no PostingRecord creation)
    6. Audit compliance (structured events)
    """

    def __init__(self):
        """Initialize approval workflow with database connection and audit chain service."""
        self.db = db
        self.audit_chain = AuditChainService()
        self.audit_chain.db = self.db  # Ensure audit chain uses same db

    def get_pending_approvals(
        self, requested_from: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        """
        Fetch pending approval requests.

        Args:
            requested_from: Optional authority level filter (e.g., "L2_SUPERVISOR")
            limit: Max number of records to return

        Returns:
            List of pending ApprovalRequest records
        """
        try:
            if requested_from:
                records = self.db.express_sync.list(
                    "ApprovalRequest",
                    {
                        "approval_status": "PENDING",
                        "requested_from": requested_from,
                    },
                    limit=limit,
                )
            else:
                records = self.db.express_sync.list(
                    "ApprovalRequest",
                    {"approval_status": "PENDING"},
                    limit=limit,
                )
            return records or []
        except Exception:
            return []

    def get_approval_request(self, approval_request_id: str) -> Optional[dict]:
        """
        Fetch a specific approval request.

        Args:
            approval_request_id: Approval request ID

        Returns:
            ApprovalRequest record or None
        """
        try:
            return self.db.express_sync.find_one(
                "ApprovalRequest", {"id": approval_request_id}
            )
        except Exception:
            return None

    def record_approval_decision(
        self, decision_input: ApprovalDecisionInput
    ) -> ApprovalWorkflowResult:
        """
        Record an approval decision with complete governance controls.

        Enforces:
        1. Maker-Checker: approver_id != requested_by
        2. Authority Enforcement: approver authority >= required authority
        3. Decision Immutability: current status must be PENDING
        4. Rejection creates ExceptionCase (replay-safe)
        5. No PostingRecord created

        Args:
            decision_input: ApprovalDecisionInput with decision details

        Returns:
            ApprovalWorkflowResult with status and any blocking reason

        Raises:
            ValueError: If approval request not found or invalid
        """
        # Fetch approval request
        approval_request = self.get_approval_request(
            decision_input.approval_request_id
        )
        if not approval_request:
            raise ValueError(
                f"Approval request {decision_input.approval_request_id} not found"
            )

        invoice_id = approval_request.get("invoice_id")
        correlation_id = approval_request.get("correlation_id")

        if not invoice_id or not correlation_id:
            raise ValueError("Approval request missing invoice_id or correlation_id")

        # Control 1: Maker-Checker - approver cannot equal request creator
        requested_by = approval_request.get("requested_by", "")
        if decision_input.approver_id == requested_by:
            return ApprovalWorkflowResult(
                approval_request_id=decision_input.approval_request_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                decision="BLOCKED",
                approver_id=decision_input.approver_id,
                invoice_case_status="PENDING",
                approval_request_status="PENDING",
                decided_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Maker-Checker violation: approver cannot equal request creator ({requested_by})",
                explanation="Decision blocked due to maker-checker control",
            )

        # Control 2: Authority Enforcement - approver authority must meet requirement
        required_authority = approval_request.get("requested_from", "")
        if not self._is_authority_sufficient(decision_input.approver_authority, required_authority):
            return ApprovalWorkflowResult(
                approval_request_id=decision_input.approval_request_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                decision="BLOCKED",
                approver_id=decision_input.approver_id,
                invoice_case_status="PENDING",
                approval_request_status="PENDING",
                decided_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Authority {decision_input.approver_authority} insufficient for {required_authority}",
                explanation="Decision blocked due to insufficient authority",
            )

        # Control 3: Decision Immutability - current status must be PENDING
        current_status = approval_request.get("approval_status", "")
        if current_status != "PENDING":
            return ApprovalWorkflowResult(
                approval_request_id=decision_input.approval_request_id,
                invoice_id=invoice_id,
                correlation_id=correlation_id,
                decision="BLOCKED",
                approver_id=decision_input.approver_id,
                invoice_case_status=approval_request.get("current_invoice_status", "PENDING"),
                approval_request_status=current_status,
                decided_at=datetime.now(timezone.utc).isoformat(),
                audit_event_id="",
                blocked_reason=f"Cannot change {current_status} decision",
                explanation="Decision blocked due to immutability control",
            )

        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()

        # Map decision to invoice case status
        invoice_case_status = (
            "APPROVED"
            if decision_input.decision == "APPROVED"
            else "REJECTED"
        )

        # Update ApprovalRequest
        approval_request_data = {
            "id": decision_input.approval_request_id,
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "requested_by": approval_request.get("requested_by", ""),
            "requested_from": approval_request.get("requested_from", ""),
            "approval_reason": approval_request.get("approval_reason", ""),
            "approval_status": decision_input.decision,  # APPROVED or REJECTED
            "approver_id": decision_input.approver_id,
            "approver_comment": decision_input.approver_comment,
            "requested_at": approval_request.get("requested_at", timestamp),
            "decided_at": timestamp,
        }

        try:
            self.db.express_sync.upsert("ApprovalRequest", approval_request_data)
        except Exception as e:
            raise ValueError(f"Failed to update ApprovalRequest: {str(e)}")

        # Update InvoiceCase
        invoice_case = self._get_invoice_case(invoice_id)
        if invoice_case:
            invoice_case_data = {
                "id": invoice_id,
                "correlation_id": correlation_id,
                "event_id": invoice_case.get("event_id", ""),
                "document_path": invoice_case.get("document_path", ""),
                "document_hash": invoice_case.get("document_hash", ""),
                "supplier_name": invoice_case.get("supplier_name", ""),
                "invoice_number": invoice_case.get("invoice_number", ""),
                "legal_entity": invoice_case.get("legal_entity", ""),
                "invoice_date": invoice_case.get("invoice_date", ""),
                "currency": invoice_case.get("currency", ""),
                "gross_amount": invoice_case.get("gross_amount", 0.0),
                "extraction_confidence": invoice_case.get(
                    "extraction_confidence", 0.0
                ),
                "target_system": invoice_case.get("target_system", ""),
                "accounting_code": invoice_case.get("accounting_code", ""),
                "case_status": invoice_case_status,
                "duplicate_of": invoice_case.get("duplicate_of", ""),
                "current_owner": decision_input.approver_id,
            }

            try:
                self.db.express_sync.upsert("InvoiceCase", invoice_case_data)
            except Exception:
                pass

        # Control 4: Rejection Visibility - create ExceptionCase only on first REJECT (replay-safe)
        exception_case_id = None
        if decision_input.decision == "REJECTED":
            exception_case_id = self._persist_exception_case(
                invoice_id, correlation_id, decision_input
            )

        # Create BusinessAuditEvent with hash-linked audit chain
        audit_event_id = f"aud-{uuid.uuid4().hex[:12]}"
        audit_payload = {
            "approval_request_id": decision_input.approval_request_id,
            "decision": decision_input.decision,
            "approver_id": decision_input.approver_id,
            "approver_authority": decision_input.approver_authority,
            "approver_comment": decision_input.approver_comment,
            "previous_status": current_status,
            "maker_checker_enforced": requested_by,
            "authority_enforced": required_authority,
        }

        audit_event_data = {
            "id": audit_event_id,
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "actor_id": decision_input.approver_id,
            "action_type": "APPROVAL_DECISION",
            "action_outcome": decision_input.decision,
            "event_payload": json.dumps(audit_payload),
            "previous_hash": "",
            "event_hash": "",  # Will be computed by audit_chain.link_audit_event
            "event_timestamp": timestamp,
        }

        # Link to previous audit event in the chain (Step 7A)
        # This computes the correct full 64-character hash including all event content
        audit_event_data = self.audit_chain.link_audit_event(
            audit_event_data, correlation_id
        )

        try:
            self.db.express_sync.create("BusinessAuditEvent", audit_event_data)
        except Exception:
            pass

        return ApprovalWorkflowResult(
            approval_request_id=decision_input.approval_request_id,
            invoice_id=invoice_id,
            correlation_id=correlation_id,
            decision=decision_input.decision,
            approver_id=decision_input.approver_id,
            invoice_case_status=invoice_case_status,
            approval_request_status=decision_input.decision,
            decided_at=timestamp,
            audit_event_id=audit_event_id,
            exception_case_id=exception_case_id,
            explanation=f"Approval decision recorded: {decision_input.decision} for {invoice_id}",
        )

    def get_pending_approvals_for_authority(
        self, authority_level: AuthorityLevel, limit: int = 100
    ) -> List[dict]:
        """
        Fetch pending approvals for a specific authority level.

        Args:
            authority_level: AuthorityLevel (L1_PROCESSOR, L2_SUPERVISOR, etc.)
            limit: Max records to return

        Returns:
            List of pending ApprovalRequest records for the authority
        """
        return self.get_pending_approvals(
            requested_from=authority_level.value, limit=limit
        )

    def _is_authority_sufficient(
        self, approver_authority: str, required_authority: str
    ) -> bool:
        """
        Check if approver's authority level is sufficient.

        Rules:
        - L1_PROCESSOR: Can approve L1 requests only
        - L2_SUPERVISOR: Can approve L1 and L2 requests
        - L3_CONTROLLER: Can approve L1, L2, and L3 requests
        - HUMAN_APPROVER: Can approve any request

        Args:
            approver_authority: Approver's authority level
            required_authority: Required authority level for the request

        Returns:
            True if approver authority >= required authority
        """
        authority_rank = {
            "L1_PROCESSOR": 1,
            "L2_SUPERVISOR": 2,
            "L3_CONTROLLER": 3,
            "HUMAN_APPROVER": 4,
        }

        approver_rank = authority_rank.get(approver_authority, 0)
        required_rank = authority_rank.get(required_authority, 4)

        return approver_rank >= required_rank

    def _persist_exception_case(
        self, invoice_id: str, correlation_id: str, decision_input: ApprovalDecisionInput
    ) -> Optional[str]:
        """
        Create ExceptionCase for REJECT decision (replay-safe).

        Only creates if not already present.

        Args:
            invoice_id: Invoice ID
            correlation_id: Correlation ID
            decision_input: Decision input for metadata

        Returns:
            ExceptionCase ID or None
        """
        # Check for existing ExceptionCase (replay protection)
        existing = self._check_existing_exception_case(invoice_id)
        if existing:
            return existing.get("id")

        exception_case_id = f"exc-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        exception_case_data = {
            "id": exception_case_id,
            "invoice_id": invoice_id,
            "correlation_id": correlation_id,
            "reason_code": "APPROVAL_REJECTED",
            "reason_detail": f"Rejected by {decision_input.approver_id}: {decision_input.approver_comment}",
            "recommended_action": "MANUAL_REVIEW",
            "owner_id": decision_input.approver_id,
            "exception_status": "OPEN",
            "opened_at": now.isoformat(),
            "resolved_at": "",
        }

        try:
            self.db.express_sync.create("ExceptionCase", exception_case_data)
        except Exception:
            pass

        return exception_case_id

    def _check_existing_exception_case(self, invoice_id: str) -> Optional[dict]:
        """Check if ExceptionCase already exists for invoice."""
        try:
            existing = self.db.express_sync.find_one(
                "ExceptionCase",
                {"invoice_id": invoice_id, "exception_status": "OPEN"},
            )
            return existing
        except Exception:
            return None

    def _get_invoice_case(self, invoice_id: str) -> Optional[dict]:
        """Fetch an InvoiceCase by ID."""
        try:
            return self.db.express_sync.find_one("InvoiceCase", {"id": invoice_id})
        except Exception:
            return None
