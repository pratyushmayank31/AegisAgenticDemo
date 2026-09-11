"""
Step 8B: Controlled Simulated Posting Service.

Orchestrates controlled simulated posting to finance systems for approved invoices:
1. Validates authorization: FinanceDecision (ALLOW) or ApprovalRequest (APPROVED)
2. Checks eligibility: no HOLD, no DENY, no exceptions, no kill-switch, target match
3. Detects replays: idempotent posting (no duplicates)
4. Simulates posting to target system (VESON_IMOS, SMARTPAL, ORACLE_FUSION)
5. Creates PostingRecord with simulated posting reference (clearly marked SIM-*)
6. Creates audit trail (blocking on audit failure, not fail-open)
7. Executes via Kailash WorkflowBuilder + LocalRuntime with stable node ID

CRITICAL:
- No real external calls - all posting is simulated locally
- Posting must be authorized via persisted FinanceDecision or ApprovalRequest
- InvoiceCase status alone is NOT sufficient proof of authorization
- Audit system failures BLOCK posting (must not fail-open)
- Replay detection prevents duplicate PostingRecords
"""

import json
import hashlib
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

from kailash import WorkflowBuilder, LocalRuntime
from kailash.nodes.code import PythonCodeNode

from src.database import db
from src.intake_service import generate_case_id, generate_audit_id, generate_correlation_id
from src.audit_chain_service import AuditChainService
from src.kill_switch_service import KillSwitchService


class PostingInput(BaseModel):
    """Input for posting an approved invoice."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: str = Field(..., min_length=1, max_length=64)
    correlation_id: str = Field(..., min_length=1, max_length=64)
    approver_id: str = Field(..., min_length=1, max_length=64)
    actor_role: str = ""


class PostingResult(BaseModel):
    """Result of controlled simulated posting."""

    model_config = ConfigDict(extra="forbid")

    posting_record_id: str
    invoice_id: str
    correlation_id: str
    posting_status: str  # SIMULATED_POSTED, FAILED, BLOCKED, REPLAY_IGNORED
    posting_reference: str = ""
    target_system: str = ""
    accounting_code: str = ""
    posted_at: str = ""
    workflow_run_id: str = ""
    explanation: str = ""
    audit_event_id: str = ""


class PostingService:
    """
    Orchestrates controlled simulated posting to finance systems.

    Authorization: Requires EITHER
    1. Persisted FinanceDecision with policy_outcome="ALLOW", OR
    2. Persisted ApprovalRequest with approval_status="APPROVED"

    Blocks: HOLD (pending), DENY, missing FinanceDecision, open ExceptionCase,
    target mismatch, kill switch engaged

    Idempotency: Returns REPLAY_IGNORED if PostingRecord exists for same case

    Audit: MUST NOT fail-open. Blocks posting if audit system fails.

    Execution: Uses Kailash WorkflowBuilder + LocalRuntime (stable node ID)
    """

    SUPPORTED_SYSTEMS = {"VESON_IMOS", "SMARTPAL", "ORACLE_FUSION"}

    def __init__(self):
        """Initialize posting service with database, audit chain, and kill switch."""
        self.db = db
        self.audit_chain = AuditChainService()
        self.audit_chain.db = self.db
        self.kill_switch = KillSwitchService()

    def post_approved_invoice(self, posting_input: PostingInput) -> PostingResult:
        """
        Post an approved invoice via Kailash workflow with controlled simulation.

        Authorization Flow:
        1. Fetch FinanceDecision → check policy_outcome == "ALLOW", OR
        2. Fetch ApprovalRequest → check approval_status == "APPROVED"
        3. Validate target_system against FinanceDecision.recommended_value
        4. Check kill switch, ExceptionCase, approval state
        5. Execute Kailash workflow: simulate_finance_posting
        6. Create PostingRecord with SIM-* reference
        7. Update InvoiceCase status to POSTED

        Args:
            posting_input: PostingInput with invoice_id, correlation_id, approver_id

        Returns:
            PostingResult with posting_record_id, workflow_run_id, status

        Raises:
            Returns BLOCKED/FAILED result (never raises, always returns result)
        """
        result = PostingResult(
            posting_record_id="",
            invoice_id=posting_input.invoice_id,
            correlation_id=posting_input.correlation_id,
            posting_status="BLOCKED",
            workflow_run_id="",
            explanation="",
        )

        try:
            # ============ Stage 1: Check Kill Switch ============
            if not self.kill_switch.is_processing_enabled_sync():
                result.posting_status = "BLOCKED"
                result.explanation = "Kill switch engaged: posting suspended"
                # Do not create audit for kill-switch blocks (handled by kill switch service)
                return result

            # ============ Stage 2: Fetch Invoice Case ============
            invoice_case = self.db.express_sync.find_one(
                "InvoiceCase", {"id": posting_input.invoice_id}
            )

            if not invoice_case:
                result.posting_status = "FAILED"
                result.explanation = f"InvoiceCase not found: {posting_input.invoice_id}"
                return result

            target_system = invoice_case.get("target_system")
            correlation_id = posting_input.correlation_id

            # ============ Stage 3: Check FinanceDecision for Authorization ============
            finance_decision = self.db.express_sync.find_one(
                "FinanceDecision",
                {"invoice_id": posting_input.invoice_id, "decision_type": "ROUTING"},
            )

            if not finance_decision:
                result.posting_status = "BLOCKED"
                result.explanation = "No FinanceDecision found: authorization required"
                return result

            # Validate target system match
            recommended_system = finance_decision.get("recommended_value")
            if recommended_system != target_system:
                result.posting_status = "BLOCKED"
                result.explanation = f"Target mismatch: case has {target_system}, decision requires {recommended_system}"
                return result

            # Check if decision grants ALLOW
            decision_outcome = finance_decision.get("policy_outcome", "PENDING")

            if decision_outcome == "ALLOW":
                # Direct ALLOW - can post immediately
                authorized = True
                authorization_source = "FinanceDecision.ALLOW"
            elif decision_outcome == "PENDING" or decision_outcome == "HOLD":
                # Check for ApprovalRequest with APPROVED status
                approval_request = self.db.express_sync.find_one(
                    "ApprovalRequest",
                    {"invoice_id": posting_input.invoice_id, "approval_status": "APPROVED"},
                )
                if approval_request:
                    authorized = True
                    authorization_source = "ApprovalRequest.APPROVED"
                else:
                    # Check if there's a pending approval
                    pending_approval = self.db.express_sync.find_one(
                        "ApprovalRequest",
                        {"invoice_id": posting_input.invoice_id, "approval_status": "PENDING"},
                    )
                    if pending_approval:
                        result.posting_status = "BLOCKED"
                        result.explanation = "Approval pending: cannot post until approved"
                        return result
                    else:
                        result.posting_status = "BLOCKED"
                        result.explanation = "No approval found: decision requires human review"
                        return result
            else:
                # DENY or other non-ALLOW state
                result.posting_status = "BLOCKED"
                result.explanation = f"Decision outcome {decision_outcome} does not permit posting"
                return result

            # ============ Stage 4: Check for ExceptionCase ============
            exception_case = self.db.express_sync.find_one(
                "ExceptionCase",
                {"invoice_id": posting_input.invoice_id, "exception_status": "OPEN"},
            )
            if exception_case:
                result.posting_status = "BLOCKED"
                result.explanation = "Open exception case prevents posting"
                return result

            # ============ Stage 5: Check for Replay (Idempotency) ============
            existing_posting = self.db.express_sync.find_one(
                "PostingRecord", {"invoice_id": posting_input.invoice_id}
            )
            if existing_posting:
                result.posting_status = "REPLAY_IGNORED"
                result.posting_record_id = existing_posting.get("id")
                result.posting_reference = existing_posting.get("posting_reference")
                result.explanation = "PostingRecord already exists for this invoice"
                return result

            # ============ Stage 6: Validate Target System ============
            if target_system not in self.SUPPORTED_SYSTEMS:
                result.posting_status = "BLOCKED"
                result.explanation = f"Unsupported target system: {target_system}"
                return result

            result.target_system = target_system

            # ============ Stage 7: Create Audit Event (BEFORE execution) ============
            audit_id = self._create_audit_event_blocking(
                correlation_id,
                posting_input.invoice_id,
                "POSTING_AUTHORIZED",
                "APPROVED",
                {
                    "target_system": target_system,
                    "authorization_source": authorization_source,
                    "approver_id": posting_input.approver_id,
                },
            )
            if not audit_id:
                result.posting_status = "FAILED"
                result.explanation = "Audit system failure: cannot post"
                return result

            # ============ Stage 8: Execute Kailash Workflow ============
            posting_record_id, workflow_run_id, posting_reference = (
                self._execute_posting_workflow(
                    posting_input.invoice_id,
                    correlation_id,
                    target_system,
                    invoice_case.get("accounting_code", ""),
                    posting_input.approver_id,
                )
            )

            if not posting_record_id:
                result.posting_status = "FAILED"
                result.explanation = "Workflow execution failed"
                self._create_audit_event_blocking(
                    correlation_id,
                    posting_input.invoice_id,
                    "POSTING_WORKFLOW_FAILED",
                    "FAILED",
                    {"error": "Workflow returned no posting_record_id"},
                )
                return result

            # ============ Stage 9: Update InvoiceCase Status ============
            self.db.express_sync.upsert(
                "InvoiceCase",
                {
                    "id": posting_input.invoice_id,
                    "correlation_id": correlation_id,
                    "event_id": invoice_case.get("event_id"),
                    "document_path": invoice_case.get("document_path"),
                    "document_hash": invoice_case.get("document_hash"),
                    "supplier_name": invoice_case.get("supplier_name", ""),
                    "invoice_number": invoice_case.get("invoice_number", ""),
                    "legal_entity": invoice_case.get("legal_entity", ""),
                    "invoice_date": invoice_case.get("invoice_date", ""),
                    "currency": invoice_case.get("currency", ""),
                    "gross_amount": invoice_case.get("gross_amount", 0.0),
                    "extraction_confidence": invoice_case.get("extraction_confidence", 0.0),
                    "target_system": target_system,
                    "accounting_code": invoice_case.get("accounting_code", ""),
                    "case_status": "POSTED",
                    "duplicate_of": invoice_case.get("duplicate_of", ""),
                    "current_owner": posting_input.approver_id,
                },
            )

            # ============ Stage 10: Create Success Audit Event ============
            success_audit = self._create_audit_event_blocking(
                correlation_id,
                posting_input.invoice_id,
                "POSTING_COMPLETED",
                "SIMULATED_POSTED",
                {
                    "posting_record_id": posting_record_id,
                    "posting_reference": posting_reference,
                    "target_system": target_system,
                    "workflow_run_id": workflow_run_id,
                },
            )
            if not success_audit:
                # Posting completed but audit failed - return partial result
                result.posting_status = "FAILED"
                result.explanation = "Posting executed but audit creation failed"
                return result

            result.posting_record_id = posting_record_id
            result.posting_status = "SIMULATED_POSTED"
            result.posting_reference = posting_reference
            result.workflow_run_id = workflow_run_id
            result.explanation = f"Simulated posting to {target_system}"
            result.audit_event_id = success_audit

            return result

        except Exception as e:
            result.posting_status = "FAILED"
            result.explanation = f"Posting failed: {str(e)}"
            return result

    def _execute_posting_workflow(
        self,
        invoice_id: str,
        correlation_id: str,
        target_system: str,
        accounting_code: str,
        approver_id: str,
    ) -> tuple[str, str, str]:
        """
        Execute Kailash workflow to simulate posting.

        Uses WorkflowBuilder + LocalRuntime with stable node ID: simulate_finance_posting

        Returns:
            (posting_record_id, workflow_run_id, posting_reference)
            or ("", "", "") on failure
        """
        try:
            # Define posting handler function (executes within workflow node)
            def posting_handler() -> dict:
                """Simulate posting and create PostingRecord."""
                posting_id = generate_case_id()
                now = datetime.now(timezone.utc).isoformat()

                # Generate simulated posting reference (format: SIM-{system}-{hex})
                posting_ref = f"SIM-{target_system}-{posting_id[-8:].upper()}"

                # Create PostingRecord via DataFlow
                db.express_sync.create(
                    "PostingRecord",
                    {
                        "id": posting_id,
                        "invoice_id": invoice_id,
                        "correlation_id": correlation_id,
                        "target_system": target_system,
                        "accounting_code": accounting_code,
                        "posting_status": "SIMULATED_POSTED",
                        "posting_reference": posting_ref,
                        "approved_by": approver_id,
                        "posted_by_agent": "posting_service_v1",
                        "posted_at": now,
                    },
                )

                return {
                    "posting_record_id": posting_id,
                    "posting_reference": posting_ref,
                    "timestamp": now,
                }

            # Build workflow with stable node ID
            builder = WorkflowBuilder()
            posting_node = PythonCodeNode(posting_handler)
            node_id = builder.add_node(posting_node, "simulate_finance_posting")
            workflow = builder.build(workflow_id="posting_workflow")

            # Execute workflow with LocalRuntime
            runtime = LocalRuntime()
            execution_result, workflow_run_id = runtime.execute(workflow, parameters={})

            # Extract result from execution - result is keyed by node ID
            node_result = execution_result.get("simulate_finance_posting") if execution_result else None
            if node_result and "posting_record_id" in node_result:
                return (
                    node_result.get("posting_record_id", ""),
                    workflow_run_id or "",
                    node_result.get("posting_reference", ""),
                )
            else:
                return "", workflow_run_id or "", ""

        except Exception as e:
            # Workflow execution failed
            return "", "", ""

    def _create_audit_event_blocking(
        self,
        correlation_id: str,
        invoice_id: str,
        action_type: str,
        outcome: str,
        payload: dict,
    ) -> str:
        """
        Create audit event with cryptographic hashing.

        BLOCKING: Returns empty string if audit creation fails (does NOT fail-open).

        Args:
            correlation_id: Correlation ID for tracing
            invoice_id: Invoice case ID
            action_type: Type of action
            outcome: Outcome status
            payload: Event payload as dict

        Returns:
            Audit event ID on success, empty string on failure
        """
        try:
            audit_id = generate_audit_id()
            now = datetime.now(timezone.utc).isoformat()

            # Sanitize payload
            safe_payload = {
                k: v
                for k, v in payload.items()
                if k not in ["bank_details", "extracted_text", "raw_document_data"]
            }

            audit_data = {
                "id": audit_id,
                "invoice_id": invoice_id,
                "correlation_id": correlation_id,
                "actor_id": "posting_service_v1",
                "action_type": action_type,
                "action_outcome": outcome,
                "event_payload": json.dumps(safe_payload),
                "previous_hash": "",
                "event_hash": "",
                "event_timestamp": now,
            }

            # Link and hash - this will raise if it fails
            audit_data = self.audit_chain.link_audit_event(audit_data, correlation_id)
            created = self.db.express_sync.create("BusinessAuditEvent", audit_data)

            return audit_id if created else ""
        except Exception:
            # Audit creation failed - return empty string (blocking, not fail-open)
            return ""
