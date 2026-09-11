"""
Finance Authority Hierarchy for LdcDemo invoice approval workflows.

Provides deterministic mapping of invoice characteristics to required approval authorities:

Authority Levels (by approval capacity):
- L1_PROCESSOR: Approves invoices < $10,000
- L2_SUPERVISOR: Approves invoices $10,000–$50,000
- L3_CONTROLLER: Approves invoices > $50,000
- HUMAN_APPROVER: Handles critical risks (duplicates, bank changes, conflicts)

Amount-based thresholds:
- $0 – $9,999 → L1_PROCESSOR
- $10,000 – $49,999 → L2_SUPERVISOR
- $50,000+ → L3_CONTROLLER
- CRITICAL risk or ROUTING_CONFLICT → HUMAN_APPROVER (regardless of amount)

Risk-escalation rules:
- DUPLICATE_INVOICE, BANK_DETAILS_CHANGED, MULTI_APPROVER_REQUIRED → HUMAN_APPROVER
- UNKNOWN_VENDOR, PRICE_MISMATCH, MISSING_REFERENCE → L3_CONTROLLER
- LOW_CONFIDENCE, AMOUNT_THRESHOLD, ROUTING_CONFLICT → L2_SUPERVISOR

No Aegis, database, LLM, PDF, or external API access. Deterministic with no side effects.

DESIGN NOTE: Uses pure Python data structures (enums, dataclass) rather than Kailash SDK
persistence models (@db.model) because the "no database" requirement in Step 5.2C
specification explicitly excludes database persistence. This module is a foundational
data model for authority hierarchy; persistence and workflow orchestration are deferred
to Step 5.3A.
"""

from enum import Enum
from typing import List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone


class AuthorityLevel(str, Enum):
    """Finance authority levels in organizational hierarchy."""

    L1_PROCESSOR = "L1_PROCESSOR"
    L2_SUPERVISOR = "L2_SUPERVISOR"
    L3_CONTROLLER = "L3_CONTROLLER"
    HUMAN_APPROVER = "HUMAN_APPROVER"


class RiskLevel(str, Enum):
    """Risk level classification for escalation."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ApprovalReason(str, Enum):
    """Reason why approval is required."""

    AMOUNT_THRESHOLD = "AMOUNT_THRESHOLD"
    UNKNOWN_VENDOR = "UNKNOWN_VENDOR"
    DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
    BANK_CHANGE = "BANK_CHANGE"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    MISSING_REFERENCE = "MISSING_REFERENCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    ROUTING_CONFLICT = "ROUTING_CONFLICT"
    HIGH_RISK_CATEGORY = "HIGH_RISK_CATEGORY"
    MULTI_APPROVER_REQUIRED = "MULTI_APPROVER_REQUIRED"


@dataclass
class ApprovalRequirement:
    """Single approval requirement for an invoice."""

    approval_reason: ApprovalReason
    required_authority_level: AuthorityLevel
    risk_level: RiskLevel
    escalation_notes: str = ""


@dataclass
class ApprovalDecision:
    """Complete approval decision for an invoice."""

    invoice_id: str
    correlation_id: str

    required_authorities: List[AuthorityLevel] = field(default_factory=list)
    approval_reasons: List[ApprovalReason] = field(default_factory=list)
    highest_risk_level: RiskLevel = RiskLevel.LOW
    total_risk_count: int = 0

    requires_multi_approval: bool = False
    approval_chain_notes: str = ""
    deterministic_hash: str = ""

    def __post_init__(self):
        """Compute deterministic hash for reproducibility."""
        import hashlib

        content = (
            f"{self.invoice_id}:{','.join(sorted(a.value for a in self.required_authorities))}"
            f":{','.join(sorted(r.value for r in self.approval_reasons))}"
            f":{self.highest_risk_level.value}"
        )
        self.deterministic_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class FinanceAuthorityHierarchy:
    """
    Deterministic authority hierarchy for finance invoice approvals.

    Maps invoice risk/amount characteristics to required approval authorities.
    """

    # Amount thresholds in USD (base currency)
    AMOUNT_THRESHOLDS = {
        AuthorityLevel.L1_PROCESSOR: 10_000.0,  # Processor can approve up to $10k
        AuthorityLevel.L2_SUPERVISOR: 50_000.0,  # Supervisor up to $50k
        AuthorityLevel.L3_CONTROLLER: float("inf"),  # Controller can approve any amount
        AuthorityLevel.HUMAN_APPROVER: float("inf"),  # Human approver for critical/conflict
    }

    # Risk escalation: critical risk requires human approver
    RISK_TO_AUTHORITY = {
        RiskLevel.LOW: AuthorityLevel.L1_PROCESSOR,
        RiskLevel.MEDIUM: AuthorityLevel.L2_SUPERVISOR,
        RiskLevel.HIGH: AuthorityLevel.L3_CONTROLLER,
        RiskLevel.CRITICAL: AuthorityLevel.HUMAN_APPROVER,
    }

    # Risk reasons and their default levels
    REASON_TO_RISK = {
        ApprovalReason.AMOUNT_THRESHOLD: RiskLevel.MEDIUM,
        ApprovalReason.UNKNOWN_VENDOR: RiskLevel.HIGH,
        ApprovalReason.DUPLICATE_DETECTED: RiskLevel.CRITICAL,
        ApprovalReason.BANK_CHANGE: RiskLevel.CRITICAL,
        ApprovalReason.PRICE_MISMATCH: RiskLevel.HIGH,
        ApprovalReason.MISSING_REFERENCE: RiskLevel.HIGH,
        ApprovalReason.LOW_CONFIDENCE: RiskLevel.MEDIUM,
        ApprovalReason.ROUTING_CONFLICT: RiskLevel.MEDIUM,
        ApprovalReason.HIGH_RISK_CATEGORY: RiskLevel.HIGH,
        ApprovalReason.MULTI_APPROVER_REQUIRED: RiskLevel.CRITICAL,
    }

    # Risk level rankings (for finding maximum)
    RISK_RANK = {
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }

    def __init__(self):
        """Initialize hierarchy with deterministic defaults."""
        pass

    def determine_approval_decision(
        self,
        invoice_id: str,
        correlation_id: str,
        gross_amount: float,
        currency: str = "USD",
        risk_flags: Optional[List[str]] = None,
        requires_multi_approval: bool = False,
    ) -> ApprovalDecision:
        """
        Deterministically determine approval requirements for an invoice.

        Args:
            invoice_id: Invoice identifier
            correlation_id: Correlation ID for tracing
            gross_amount: Invoice amount (in currency)
            currency: Currency code (USD, EUR, etc.)
            risk_flags: List of risk flag strings (e.g., "DUPLICATE_INVOICE")
            requires_multi_approval: If True, escalate to multi-approver

        Returns:
            ApprovalDecision with required authorities and reasons
        """
        if not invoice_id or not invoice_id.strip():
            raise ValueError("invoice_id cannot be empty")
        if not correlation_id or not correlation_id.strip():
            raise ValueError("correlation_id cannot be empty")
        if gross_amount < 0:
            raise ValueError("gross_amount cannot be negative")

        risk_flags = risk_flags or []
        approval_reasons: Set[ApprovalReason] = set()
        risk_levels: List[RiskLevel] = []

        # Convert risk flags to approval reasons
        for flag in risk_flags:
            if flag == "DUPLICATE_INVOICE":
                approval_reasons.add(ApprovalReason.DUPLICATE_DETECTED)
                risk_levels.append(RiskLevel.CRITICAL)
            elif flag == "BANK_DETAILS_CHANGED":
                approval_reasons.add(ApprovalReason.BANK_CHANGE)
                risk_levels.append(RiskLevel.CRITICAL)
            elif flag == "UNKNOWN_VENDOR":
                approval_reasons.add(ApprovalReason.UNKNOWN_VENDOR)
                risk_levels.append(RiskLevel.HIGH)
            elif flag == "MISSING_REFERENCE":
                approval_reasons.add(ApprovalReason.MISSING_REFERENCE)
                risk_levels.append(RiskLevel.HIGH)
            elif flag == "PRICE_MISMATCH":
                approval_reasons.add(ApprovalReason.PRICE_MISMATCH)
                risk_levels.append(RiskLevel.HIGH)
            elif flag == "LOW_CONFIDENCE":
                approval_reasons.add(ApprovalReason.LOW_CONFIDENCE)
                risk_levels.append(RiskLevel.MEDIUM)
            elif flag == "AMOUNT_THRESHOLD":
                approval_reasons.add(ApprovalReason.AMOUNT_THRESHOLD)
                risk_levels.append(RiskLevel.MEDIUM)
            elif flag == "ROUTING_CONFLICT":
                approval_reasons.add(ApprovalReason.ROUTING_CONFLICT)
                risk_levels.append(RiskLevel.MEDIUM)

        # Check amount-based thresholds (always for non-zero amounts)
        if gross_amount >= self.AMOUNT_THRESHOLDS[AuthorityLevel.L1_PROCESSOR]:
            if ApprovalReason.AMOUNT_THRESHOLD not in approval_reasons:
                approval_reasons.add(ApprovalReason.AMOUNT_THRESHOLD)
                risk_levels.append(RiskLevel.MEDIUM)

        # Multi-approver escalation
        if requires_multi_approval:
            approval_reasons.add(ApprovalReason.MULTI_APPROVER_REQUIRED)
            risk_levels.append(RiskLevel.CRITICAL)

        # Determine highest risk level (by rank, not alphabetical)
        if risk_levels:
            highest_risk = max(risk_levels, key=lambda r: self.RISK_RANK.get(r, 0))
        else:
            highest_risk = RiskLevel.LOW

        # Determine required authorities based on amount and risk
        required_authorities = self._determine_authorities(
            gross_amount, highest_risk, approval_reasons
        )

        # Build approval decision
        decision = ApprovalDecision(
            invoice_id=invoice_id,
            correlation_id=correlation_id,
            required_authorities=required_authorities,
            approval_reasons=list(approval_reasons),
            highest_risk_level=highest_risk,
            total_risk_count=len(risk_levels),
            requires_multi_approval=requires_multi_approval,
            approval_chain_notes=self._build_approval_notes(
                gross_amount, highest_risk, approval_reasons
            ),
        )

        return decision

    def _determine_authorities(
        self,
        amount: float,
        risk_level: RiskLevel,
        reasons: Set[ApprovalReason],
    ) -> List[AuthorityLevel]:
        """
        Deterministically determine required authorities.

        Rules (priority order):
        1. If DUPLICATE or BANK_CHANGE or MULTI_APPROVER_REQUIRED or CRITICAL risk → HUMAN_APPROVER
        2. If ROUTING_CONFLICT or any HIGH risk → L3_CONTROLLER
        3. Amount-based thresholds
        4. If no amount/risk triggers → L1_PROCESSOR
        """
        # Rule 1: Critical risks or conflicts bypass normal chain → HUMAN_APPROVER
        if (
            ApprovalReason.DUPLICATE_DETECTED in reasons
            or ApprovalReason.BANK_CHANGE in reasons
            or ApprovalReason.MULTI_APPROVER_REQUIRED in reasons
            or risk_level == RiskLevel.CRITICAL
        ):
            return [AuthorityLevel.HUMAN_APPROVER]

        # Rule 2: Routing conflict escalates to L3_CONTROLLER
        if ApprovalReason.ROUTING_CONFLICT in reasons or risk_level == RiskLevel.HIGH:
            return [AuthorityLevel.L3_CONTROLLER]

        # Rule 3: Amount-based thresholds
        authorities = []

        if amount > self.AMOUNT_THRESHOLDS[AuthorityLevel.L2_SUPERVISOR]:
            # Amount > $50k → L3_CONTROLLER
            authorities.append(AuthorityLevel.L3_CONTROLLER)
        elif amount >= self.AMOUNT_THRESHOLDS[AuthorityLevel.L1_PROCESSOR]:
            # Amount >= $10k and <= $50k → L2_SUPERVISOR
            authorities.append(AuthorityLevel.L2_SUPERVISOR)
        else:
            # Amount < $10k → L1_PROCESSOR
            authorities.append(AuthorityLevel.L1_PROCESSOR)

        # Risk-based escalation: if risk is higher than amount-based level, escalate
        if authorities:
            amount_level = authorities[0]
            risk_required_level = self.RISK_TO_AUTHORITY.get(
                risk_level, AuthorityLevel.L1_PROCESSOR
            )

            # If risk requires higher level, replace with risk-based level
            if self._level_rank(risk_required_level) > self._level_rank(amount_level):
                authorities = [risk_required_level]

        # Rule 4: Default to L1_PROCESSOR if nothing triggered
        if not authorities:
            authorities = [AuthorityLevel.L1_PROCESSOR]

        return authorities

    def _level_rank(self, level: AuthorityLevel) -> int:
        """Return numeric rank (higher = more authority)."""
        ranks = {
            AuthorityLevel.L1_PROCESSOR: 1,
            AuthorityLevel.L2_SUPERVISOR: 2,
            AuthorityLevel.L3_CONTROLLER: 3,
            AuthorityLevel.HUMAN_APPROVER: 4,
        }
        return ranks.get(level, 0)

    def _risk_rank(self, level: RiskLevel) -> int:
        """Return numeric rank for risk level (higher = more severe)."""
        return self.RISK_RANK.get(level, 0)

    def _build_approval_notes(
        self,
        amount: float,
        risk_level: RiskLevel,
        reasons: Set[ApprovalReason],
    ) -> str:
        """Build human-readable approval chain notes."""
        notes_parts = []

        if risk_level == RiskLevel.CRITICAL:
            notes_parts.append(f"CRITICAL RISK ({risk_level.value})")

        if ApprovalReason.DUPLICATE_DETECTED in reasons:
            notes_parts.append("Duplicate invoice detected - CFO review required")

        if ApprovalReason.BANK_CHANGE in reasons:
            notes_parts.append("Bank details changed - CFO review required")

        if ApprovalReason.UNKNOWN_VENDOR in reasons:
            notes_parts.append("Unknown vendor - requires approval")

        if ApprovalReason.AMOUNT_THRESHOLD in reasons:
            notes_parts.append(f"Amount ${amount:.2f} exceeds clerk threshold")

        if ApprovalReason.PRICE_MISMATCH in reasons:
            notes_parts.append("Price mismatch detected - requires review")

        if ApprovalReason.LOW_CONFIDENCE in reasons:
            notes_parts.append("Low document confidence - requires review")

        if ApprovalReason.MULTI_APPROVER_REQUIRED in reasons:
            notes_parts.append("Multiple approvals required per policy")

        return "; ".join(notes_parts) if notes_parts else "Standard approval process"

    def get_authority_by_name(self, name: str) -> Optional[AuthorityLevel]:
        """Look up authority level by string name."""
        try:
            return AuthorityLevel(name)
        except ValueError:
            return None

    def get_approval_reason_by_name(self, name: str) -> Optional[ApprovalReason]:
        """Look up approval reason by string name."""
        try:
            return ApprovalReason(name)
        except ValueError:
            return None

    def rank_authorities(
        self, authorities: List[AuthorityLevel]
    ) -> List[AuthorityLevel]:
        """Sort authorities by rank (lowest to highest)."""
        return sorted(authorities, key=self._level_rank)

    def is_authority_higher_than(
        self, level_a: AuthorityLevel, level_b: AuthorityLevel
    ) -> bool:
        """Check if level_a has higher authority than level_b."""
        return self._level_rank(level_a) > self._level_rank(level_b)
