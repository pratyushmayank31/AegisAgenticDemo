from pathlib import Path

from dataflow import DataFlow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIRECTORY = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIRECTORY / "finance_demo.db"

DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

db = DataFlow(DATABASE_URL)


@db.model
class InvoiceCase:
    """Current consolidated state of an invoice."""

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
class FinanceDecision:
    """Stores routing, coding and governance decisions."""

    id: str
    invoice_id: str
    correlation_id: str
    decision_type: str
    recommended_value: str

    confidence: float = 0.0
    rationale: str = ""
    evidence: str = ""

    agent_id: str = ""
    policy_outcome: str = "PENDING"
    decision_timestamp: str = ""


@db.model
class ApprovalRequest:
    """Records human approval requests and decisions."""

    id: str
    invoice_id: str
    correlation_id: str

    requested_by: str
    requested_from: str
    approval_reason: str

    approval_status: str = "PENDING"
    approver_id: str = ""
    approver_comment: str = ""
    requested_at: str = ""
    decided_at: str = ""


@db.model
class ExceptionCase:
    """Ensures that no invoice disappears silently."""

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
class PostingRecord:
    """Stores the simulated finance posting result."""

    id: str
    invoice_id: str
    correlation_id: str

    target_system: str
    accounting_code: str
    posting_status: str

    posting_reference: str = ""
    approved_by: str = ""
    posted_by_agent: str = ""
    posted_at: str = ""


@db.model
class EventReceipt:
    """Prevents the same incoming event from creating two cases."""

    id: str
    invoice_id: str
    correlation_id: str

    payload_hash: str
    receipt_status: str
    received_at: str


@db.model
class BusinessAuditEvent:
    """Stores business evidence separately from agent memory."""

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
class SystemControlState:
    """Tracks system-level control state including kill switch status."""

    id: str
    control_name: str
    is_enabled: bool
    enabled_by: str
    enabled_at: str
    disabled_by: str = ""
    disabled_at: str = ""
    reason: str = ""
    last_modified_at: str = ""