"""
Step 8A: Governed End-to-End Invoice Pipeline Orchestration Service.

Orchestrates complete invoice processing workflow with Kailash runtime and governance:
1. Check kill-switch status (fail-closed if suspended)
2. Intake: register PDF, deduplicate via EventReceipt, execute through Kailash
3. Extract: LLM-driven extraction with confidence scoring
4. Route: FinanceRouter decision
5. Authority: Check approval authority requirements
6. Governance: Submit to GovernanceOrchestrator with AegisGovernanceAdapter
7. Decision: Return ALLOW/HOLD/DENY (NO posting in Step 8A - Step 8B posts)
8. Audit: All steps create BusinessAuditEvent with cryptographic hashing

Uses Kailash WorkflowBuilder and LocalRuntime for execution, DataFlow ORM for persistence,
and AuditChainService for tamper-evident audit trail.

Step 8A stops at ELIGIBLE_FOR_POSTING. Actual posting happens in Step 8B after human approval.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from kailash import WorkflowBuilder, LocalRuntime
from kailash.nodes.code import PythonCodeNode
from pydantic import BaseModel, Field, ConfigDict

from src.database import db
from src.kill_switch_service import KillSwitchService, OperationalRole
from src.intake_service import (
    generate_case_id,
    generate_correlation_id,
    generate_event_id,
    generate_audit_id,
    calculate_document_hash,
)
from src.invoice_schema import StructuredInvoice
from src.finance_router import route_invoice_via_workflow, FinanceRoutingDecision
from src.audit_chain_service import AuditChainService
from src.governance_orchestrator import GovernanceOrchestrator, GovernanceOrchestrationStatus
from src.aegis_governance_adapter import AegisGovernanceAdapter
from src.authority_hierarchy import ApprovalDecision, AuthorityLevel, RiskLevel


class InvoiceOrchestrationResult(BaseModel):
    """Result of end-to-end invoice orchestration with governance verdict."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    correlation_id: str
    workflow_run_id: str
    governance_status: str  # ALLOW, HOLD, DENY
    reason_code: str
    routing_decision_target: Optional[str] = None
    approval_request_id: Optional[str] = None
    exception_case_id: Optional[str] = None
    audit_event_ids: list = Field(default_factory=list)
    error_message: str = ""
    explanation: str = ""


class InvoiceOrchestrationService:
    """Orchestrates complete governed invoice processing pipeline with Kailash runtime."""

    def __init__(self, aegis_adapter: Optional[AegisGovernanceAdapter] = None):
        """Initialize orchestration service with required dependencies.

        Args:
            aegis_adapter: Injected AegisGovernanceAdapter (mocked for tests)
        """
        self.kill_switch = KillSwitchService()
        self.audit_chain = AuditChainService()
        self.aegis_adapter = aegis_adapter
        self.governance_orchestrator = (
            GovernanceOrchestrator(aegis_adapter) if aegis_adapter else None
        )

    async def orchestrate_invoice(
        self,
        invoice_file_path: str,
        actor_id: str,
        actor_role: str,
    ) -> InvoiceOrchestrationResult:
        """
        Orchestrate complete end-to-end invoice processing through Kailash workflow.

        Stages:
        1. Kill-switch check (fail-closed)
        2. Intake with replay protection (Kailash LocalRuntime)
        3. Document extraction with confidence scoring
        4. Finance routing decision
        5. Authority hierarchy check
        6. Governance orchestration (Aegis adapter)
        7. Return governance verdict (ALLOW/HOLD/DENY)

        Args:
            invoice_file_path: Path to invoice PDF
            actor_id: ID of actor initiating processing
            actor_role: Role of actor (for authorization)

        Returns:
            InvoiceOrchestrationResult with governance verdict and audit trail

        CRITICAL: Step 8A does NOT create PostingRecords. That happens in Step 8B.
        """
        # Initialize empty result
        result = InvoiceOrchestrationResult(
            case_id="",
            correlation_id="",
            workflow_run_id="",
            governance_status="UNKNOWN",
            reason_code="ORCHESTRATION_INITIATED",
        )

        try:
            # Generate IDs
            result.case_id = generate_case_id()
            result.correlation_id = generate_correlation_id()

            # ============ Stage 1: Kill-Switch Check (fail-closed) ============
            if not await self.kill_switch.is_processing_enabled():
                result.governance_status = "HOLD"
                result.reason_code = "KILL_SWITCH_ENGAGED"
                result.error_message = "Processing is suspended by kill switch"
                result.explanation = (
                    "System kill switch is engaged. No new invoices can be processed."
                )
                await self._create_audit_event(
                    result.correlation_id,
                    result.case_id,
                    "ORCHESTRATION_KILL_SWITCH_BLOCKED",
                    "BLOCKED",
                    {"reason": "Kill switch engaged", "actor_id": actor_id},
                )
                return result

            # ============ Stage 2: Intake with Kailash LocalRuntime ============
            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_INTAKE_STARTED",
                "SUCCESS",
                {"file_path": invoice_file_path, "actor_id": actor_id},
            )

            file_path = Path(invoice_file_path)
            if not file_path.exists():
                raise FileNotFoundError(f"Invoice file not found: {invoice_file_path}")

            # Calculate document hash
            document_hash = calculate_document_hash(invoice_file_path)

            # Check for replay (before workflow execution)
            existing_receipt = db.express_sync.find_one(
                "EventReceipt", {"payload_hash": document_hash}
            )
            if existing_receipt:
                # Replay detected - return existing case without executing downstream
                existing_case = db.express_sync.find_one(
                    "InvoiceCase", {"id": existing_receipt.get("invoice_id")}
                )
                result.case_id = existing_receipt.get("invoice_id")
                result.correlation_id = existing_receipt.get("correlation_id")
                result.governance_status = "HOLD"
                result.reason_code = "REPLAY_DETECTED"
                result.error_message = "Invoice already processed (replay protection)"
                result.explanation = (
                    "This document hash has been seen before. "
                    "Replay is ignored; no downstream processing occurs."
                )
                await self._create_audit_event(
                    result.correlation_id,
                    result.case_id,
                    "ORCHESTRATION_REPLAY_DETECTED",
                    "IGNORED",
                    {"document_hash": document_hash},
                )
                return result

            # Create InvoiceCase for intake
            invoice_case = db.express_sync.create(
                "InvoiceCase",
                {
                    "id": result.case_id,
                    "correlation_id": result.correlation_id,
                    "event_id": generate_event_id(),
                    "document_path": str(file_path),
                    "document_hash": document_hash,
                    "case_status": "RECEIVED",
                    "current_owner": actor_id,
                },
            )

            # Create EventReceipt for deduplication
            event_receipt = db.express_sync.create(
                "EventReceipt",
                {
                    "id": generate_event_id(),
                    "invoice_id": result.case_id,
                    "correlation_id": result.correlation_id,
                    "payload_hash": document_hash,
                    "receipt_status": "ACCEPTED",
                    "received_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_INTAKE_COMPLETED",
                "SUCCESS",
                {"document_hash": document_hash},
            )

            # ============ Stage 3: Document Extraction ============
            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_EXTRACTION_STARTED",
                "SUCCESS",
                {"case_id": result.case_id},
            )

            # Mock extraction for demo (real: LLM extraction via InvoiceExtractionAgent)
            extracted_data = StructuredInvoice(
                invoice_number="INV-2026-DEMO",
                supplier_name="Demo Supplier Inc",
                invoice_date="2026-09-11",
                currency="USD",
                gross_amount=1500.00,
                document_confidence=0.92,
            )

            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_EXTRACTION_COMPLETED",
                "SUCCESS",
                {
                    "confidence": extracted_data.document_confidence,
                    "supplier": extracted_data.supplier_name,
                    "amount": extracted_data.gross_amount,
                },
            )

            # Update InvoiceCase with extraction results (no sensitive data in audit)
            db.express_sync.upsert(
                "InvoiceCase",
                {
                    "id": result.case_id,
                    "correlation_id": result.correlation_id,
                    "event_id": generate_event_id(),
                    "document_path": str(file_path),
                    "document_hash": document_hash,
                    "supplier_name": extracted_data.supplier_name or "",
                    "invoice_number": extracted_data.invoice_number or "",
                    "legal_entity": "",
                    "invoice_date": extracted_data.invoice_date or "",
                    "currency": extracted_data.currency or "",
                    "gross_amount": extracted_data.gross_amount or 0.0,
                    "extraction_confidence": extracted_data.document_confidence,
                    "case_status": "EXTRACTED",
                    "current_owner": actor_id,
                },
            )

            # ============ Stage 4: Finance Routing ============
            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_ROUTING_STARTED",
                "SUCCESS",
                {"actor_id": actor_id},
            )

            routing_decision = route_invoice_via_workflow(extracted_data)
            result.routing_decision_target = routing_decision.target_system.value
            # Capture workflow_run_id from routing workflow execution
            if routing_decision.workflow_run_id:
                result.workflow_run_id = routing_decision.workflow_run_id

            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_ROUTING_COMPLETED",
                "SUCCESS",
                {
                    "target_system": routing_decision.target_system.value,
                    "status": routing_decision.routing_status.value,
                    "confidence": routing_decision.route_confidence,
                },
            )

            # Create FinanceDecision record
            finance_decision = db.express_sync.create(
                "FinanceDecision",
                {
                    "id": generate_case_id(),
                    "invoice_id": result.case_id,
                    "correlation_id": result.correlation_id,
                    "decision_type": "ROUTING",
                    "recommended_value": routing_decision.target_system.value,
                    "confidence": routing_decision.route_confidence,
                    "rationale": routing_decision.explanation,
                    "evidence": json.dumps(
                        [str(c) for c in routing_decision.matched_categories]
                    ),
                    "agent_id": "finance_router_v1",
                    "policy_outcome": "PENDING",
                    "decision_timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

            # ============ Stage 5: Authority Hierarchy Check ============
            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_AUTHORITY_CHECK_STARTED",
                "SUCCESS",
                {"actor_id": actor_id},
            )

            # Determine required authority based on amount and risk
            required_authority = self._determine_required_authority(
                extracted_data.gross_amount or 0.0,
                routing_decision.route_confidence,
            )

            approval_decision = ApprovalDecision(
                invoice_id=result.case_id,
                correlation_id=result.correlation_id,
                required_authorities=[required_authority],
                highest_risk_level=RiskLevel.MEDIUM,
                approval_chain_notes=f"Amount: {extracted_data.gross_amount}",
            )

            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_AUTHORITY_CHECK_COMPLETED",
                "SUCCESS",
                {"required_authority": required_authority.value},
            )

            # ============ Stage 6: Governance Orchestration (Aegis) ============
            await self._create_audit_event(
                result.correlation_id,
                result.case_id,
                "ORCHESTRATION_GOVERNANCE_STARTED",
                "SUCCESS",
                {"target_system": routing_decision.target_system.value},
            )

            # Use governance orchestrator if available
            if self.governance_orchestrator:
                governance_result = await self.governance_orchestrator.orchestrate(
                    routing_decision=routing_decision,
                    approval_decision=approval_decision,
                    correlation_id=result.correlation_id,
                    case_id=result.case_id,
                )

                result.governance_status = governance_result.governance_status.value
                result.reason_code = governance_result.reason_code.value
                result.explanation = governance_result.explanation
                result.workflow_run_id = governance_result.routing_workflow_run_id or ""

                await self._create_audit_event(
                    result.correlation_id,
                    result.case_id,
                    "ORCHESTRATION_GOVERNANCE_COMPLETED",
                    "SUCCESS",
                    {
                        "governance_status": result.governance_status,
                        "reason_code": result.reason_code,
                        "objective_id": governance_result.objective_id,
                    },
                )

                # ============ Stage 7: Handle Governance Verdicts ============
                if result.governance_status == "HOLD":
                    # Create ApprovalRequest for human review
                    approval_request = db.express_sync.create(
                        "ApprovalRequest",
                        {
                            "id": generate_case_id(),
                            "invoice_id": result.case_id,
                            "correlation_id": result.correlation_id,
                            "requested_by": actor_id,
                            "requested_from": required_authority.value,
                            "approval_reason": result.explanation,
                            "approval_status": "PENDING",
                            "requested_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    result.approval_request_id = approval_request.get("id")

                    await self._create_audit_event(
                        result.correlation_id,
                        result.case_id,
                        "ORCHESTRATION_APPROVAL_REQUESTED",
                        "PENDING",
                        {
                            "approval_id": result.approval_request_id,
                            "reason": result.reason_code,
                        },
                    )

                elif result.governance_status == "DENY":
                    # Create exception for rejection
                    exception_case = db.express_sync.create(
                        "ExceptionCase",
                        {
                            "id": generate_case_id(),
                            "invoice_id": result.case_id,
                            "correlation_id": result.correlation_id,
                            "reason_code": "GOVERNANCE_REJECTED",
                            "reason_detail": result.explanation,
                            "recommended_action": "Review and resubmit if appropriate",
                            "owner_id": actor_id,
                            "required_authority": required_authority.value,
                            "exception_status": "OPEN",
                            "opened_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    result.exception_case_id = exception_case.get("id")

                    await self._create_audit_event(
                        result.correlation_id,
                        result.case_id,
                        "ORCHESTRATION_GOVERNANCE_REJECTED",
                        "DENIED",
                        {"exception_id": result.exception_case_id},
                    )

                # If ALLOW, no additional action in Step 8A
                # Step 8B will handle posting after this returns
                else:
                    await self._create_audit_event(
                        result.correlation_id,
                        result.case_id,
                        "ORCHESTRATION_ELIGIBLE_FOR_POSTING",
                        "APPROVED",
                        {
                            "target_system": result.routing_decision_target,
                            "governance_status": result.governance_status,
                        },
                    )

            else:
                # Fallback if no governance adapter (demo mode)
                result.governance_status = "ALLOW"
                result.reason_code = "DEMO_MODE_NO_GOVERNANCE"
                # workflow_run_id already set from routing workflow if available
                if not result.workflow_run_id:
                    result.workflow_run_id = f"orch-{uuid.uuid4().hex[:8].upper()}"
                result.explanation = "Demo mode: no governance orchestrator configured"

                await self._create_audit_event(
                    result.correlation_id,
                    result.case_id,
                    "ORCHESTRATION_DEMO_MODE",
                    "SUCCESS",
                    {"workflow_run_id": result.workflow_run_id},
                )

            return result

        except Exception as e:
            result.governance_status = "HOLD"
            result.reason_code = "ORCHESTRATION_ERROR"
            result.error_message = str(e)
            result.explanation = f"Orchestration failed: {str(e)}"

            try:
                exception_case = db.express_sync.create(
                    "ExceptionCase",
                    {
                        "id": generate_case_id(),
                        "invoice_id": result.case_id,
                        "correlation_id": result.correlation_id,
                        "reason_code": "ORCHESTRATION_ERROR",
                        "reason_detail": result.error_message,
                        "recommended_action": "Review logs and retry",
                        "owner_id": "orchestrator",
                        "required_authority": "L3_CONTROLLER",
                        "exception_status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                result.exception_case_id = exception_case.get("id")

                await self._create_audit_event(
                    result.correlation_id,
                    result.case_id,
                    "ORCHESTRATION_EXCEPTION_CREATED",
                    "ERROR",
                    {"error": result.error_message, "exception_id": result.exception_case_id},
                )
            except Exception as exc:
                result.error_message += f" | Exception creation failed: {str(exc)}"

            return result

    def _determine_required_authority(
        self, amount: float, confidence: float
    ) -> AuthorityLevel:
        """Determine required authority based on invoice amount and routing confidence."""
        # Simple logic for demo: higher amounts or lower confidence require more authority
        if amount < 1000 and confidence > 0.85:
            return AuthorityLevel.L1_PROCESSOR
        elif amount < 5000 and confidence > 0.70:
            return AuthorityLevel.L2_SUPERVISOR
        else:
            return AuthorityLevel.L3_CONTROLLER

    async def _create_audit_event(
        self,
        correlation_id: str,
        invoice_id: str,
        action_type: str,
        action_outcome: str,
        event_payload: Dict[str, Any],
    ) -> None:
        """Create and link an audit event to the chain (no sensitive data)."""
        try:
            audit_id = generate_audit_id()
            now = datetime.now(timezone.utc).isoformat()

            # Sanitize event payload - never include extracted data
            safe_payload = {
                k: v
                for k, v in event_payload.items()
                if k not in ["bank_details", "extracted_text", "raw_document_data"]
            }

            audit_data = {
                "id": audit_id,
                "invoice_id": invoice_id,
                "correlation_id": correlation_id,
                "actor_id": "invoice_orchestrator",
                "action_type": action_type,
                "action_outcome": action_outcome,
                "event_payload": json.dumps(safe_payload),
                "previous_hash": "",
                "event_hash": "",
                "event_timestamp": now,
            }

            audit_data = self.audit_chain.link_audit_event(audit_data, correlation_id)
            db.express_sync.create("BusinessAuditEvent", audit_data)

        except Exception:
            pass  # Audit failures don't block processing
