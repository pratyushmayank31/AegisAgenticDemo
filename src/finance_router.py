"""
Conservative deterministic finance target-system router.

Maps StructuredInvoice business content to target-system candidates using
controlled category vocabulary and explicit routing rules. No LLM, Aegis,
database, or file-path access.

Executes via Kailash workflow for genuine run-id tracking.
"""

from enum import Enum
from typing import List, Optional, Set
from dataclasses import dataclass
from pydantic import BaseModel, Field, ConfigDict
import re

from kailash import WorkflowBuilder
from kailash.nodes import HandlerNode
from kailash import LocalRuntime

from src.invoice_schema import StructuredInvoice


class FinanceTargetSystem(str, Enum):
    """Approved target-system candidates."""
    VESON_IMOS = "VESON_IMOS"
    SMARTPAL = "SMARTPAL"
    ORACLE_FUSION = "ORACLE_FUSION"
    UNDETERMINED = "UNDETERMINED"


class FinanceBusinessCategory(str, Enum):
    """Controlled business category vocabulary."""
    BUNKER = "BUNKER"
    FREIGHT = "FREIGHT"
    DEMURRAGE = "DEMURRAGE"
    CHARTER_HIRE = "CHARTER_HIRE"
    PORT_AGENCY = "PORT_AGENCY"
    VESSEL_REPAIR = "VESSEL_REPAIR"
    VESSEL_MAINTENANCE = "VESSEL_MAINTENANCE"
    VESSEL_SPARES = "VESSEL_SPARES"
    SOFTWARE = "SOFTWARE"
    LEGAL = "LEGAL"
    OFFICE_RENT = "OFFICE_RENT"
    CORPORATE_RECHARGE = "CORPORATE_RECHARGE"
    UNKNOWN = "UNKNOWN"


class RoutingStatus(str, Enum):
    """Routing decision status."""
    ROUTE_CANDIDATE = "ROUTE_CANDIDATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class RoutingReason(str, Enum):
    """Reason code for routing decision."""
    SINGLE_CATEGORY_MATCH = "SINGLE_CATEGORY_MATCH"
    MULTIPLE_CATEGORIES_SAME_SYSTEM = "MULTIPLE_CATEGORIES_SAME_SYSTEM"
    ROUTING_CONFLICT = "ROUTING_CONFLICT"
    INSUFFICIENT_CATEGORY_EVIDENCE = "INSUFFICIENT_CATEGORY_EVIDENCE"
    LOW_DOCUMENT_CONFIDENCE = "LOW_DOCUMENT_CONFIDENCE"
    EXISTING_HUMAN_REVIEW_REQUIREMENT = "EXISTING_HUMAN_REVIEW_REQUIREMENT"


class FinanceRoutingDecision(BaseModel):
    """Routing decision with evidence."""
    model_config = ConfigDict(extra="forbid")

    target_system: FinanceTargetSystem
    routing_status: RoutingStatus
    reason_code: RoutingReason
    matched_categories: List[FinanceBusinessCategory] = Field(default_factory=list)
    matched_evidence_terms: List[str] = Field(default_factory=list)
    route_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = ""
    workflow_run_id: str = ""


# Controlled vocabulary for category detection
VESON_IMOS_KEYWORDS = {"bunker", "freight", "demurrage", "charter", "port agency"}
SMARTPAL_KEYWORDS = {"repair", "maintenance", "spare", "spares"}
ORACLE_FUSION_KEYWORDS = {"software", "legal", "rent", "recharge", "arrendamiento"}  # arrendamiento = Spanish for rent


def normalize_text(text: str) -> str:
    """Normalize text: lowercase, strip, remove extra whitespace."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.lower().strip())


def extract_keywords(text: str) -> Set[str]:
    """Extract matched controlled keywords from text."""
    if not text:
        return set()
    normalized = normalize_text(text)
    matches = set()

    # Match exact controlled terms
    for keyword in VESON_IMOS_KEYWORDS | SMARTPAL_KEYWORDS | ORACLE_FUSION_KEYWORDS:
        # Use word boundaries to avoid partial matches
        pattern = rf'\b{re.escape(keyword)}\b'
        if re.search(pattern, normalized):
            matches.add(keyword)

    return matches


def classify_business_category(structured_invoice: StructuredInvoice) -> tuple[Set[FinanceBusinessCategory], Set[str]]:
    """
    Classify business categories from line items and other fields.

    Returns:
        Tuple of (matched_categories, matched_evidence_terms)
    """
    matched_categories = set()
    matched_evidence_terms = set()

    # Extract text from line items
    line_item_text = ""
    if structured_invoice.line_items:
        for item in structured_invoice.line_items:
            if item.item_description:
                line_item_text += " " + item.item_description

    # Combine all text to search
    all_text = line_item_text

    # Extract keywords
    keywords = extract_keywords(all_text)
    matched_evidence_terms.update(keywords)

    # Map keywords to categories
    if keywords & VESON_IMOS_KEYWORDS:
        if "bunker" in keywords:
            matched_categories.add(FinanceBusinessCategory.BUNKER)
        if "freight" in keywords:
            matched_categories.add(FinanceBusinessCategory.FREIGHT)
        if "demurrage" in keywords:
            matched_categories.add(FinanceBusinessCategory.DEMURRAGE)
        if "charter" in keywords:
            matched_categories.add(FinanceBusinessCategory.CHARTER_HIRE)
        if "port agency" in keywords:
            matched_categories.add(FinanceBusinessCategory.PORT_AGENCY)

    if keywords & SMARTPAL_KEYWORDS:
        if "repair" in keywords:
            matched_categories.add(FinanceBusinessCategory.VESSEL_REPAIR)
        if "maintenance" in keywords:
            matched_categories.add(FinanceBusinessCategory.VESSEL_MAINTENANCE)
        if "spare" in keywords or "spares" in keywords:
            matched_categories.add(FinanceBusinessCategory.VESSEL_SPARES)

    if keywords & ORACLE_FUSION_KEYWORDS:
        if "software" in keywords:
            matched_categories.add(FinanceBusinessCategory.SOFTWARE)
        if "legal" in keywords:
            matched_categories.add(FinanceBusinessCategory.LEGAL)
        if "rent" in keywords or "arrendamiento" in keywords:
            matched_categories.add(FinanceBusinessCategory.OFFICE_RENT)
        if "recharge" in keywords:
            matched_categories.add(FinanceBusinessCategory.CORPORATE_RECHARGE)

    if not matched_categories:
        matched_categories.add(FinanceBusinessCategory.UNKNOWN)

    return matched_categories, matched_evidence_terms


def map_categories_to_systems(categories: Set[FinanceBusinessCategory]) -> Set[FinanceTargetSystem]:
    """Map categories to target systems."""
    systems = set()

    veson_categories = {
        FinanceBusinessCategory.BUNKER,
        FinanceBusinessCategory.FREIGHT,
        FinanceBusinessCategory.DEMURRAGE,
        FinanceBusinessCategory.CHARTER_HIRE,
        FinanceBusinessCategory.PORT_AGENCY,
    }

    smartpal_categories = {
        FinanceBusinessCategory.VESSEL_REPAIR,
        FinanceBusinessCategory.VESSEL_MAINTENANCE,
        FinanceBusinessCategory.VESSEL_SPARES,
    }

    oracle_categories = {
        FinanceBusinessCategory.SOFTWARE,
        FinanceBusinessCategory.LEGAL,
        FinanceBusinessCategory.OFFICE_RENT,
        FinanceBusinessCategory.CORPORATE_RECHARGE,
    }

    if categories & veson_categories:
        systems.add(FinanceTargetSystem.VESON_IMOS)
    if categories & smartpal_categories:
        systems.add(FinanceTargetSystem.SMARTPAL)
    if categories & oracle_categories:
        systems.add(FinanceTargetSystem.ORACLE_FUSION)

    return systems


def route_invoice(structured_invoice: StructuredInvoice) -> FinanceRoutingDecision:
    """
    Deterministic routing logic (priority A-G).

    A. Low document confidence → HUMAN_REVIEW
    B. Existing requires_human_review flag → HUMAN_REVIEW
    C-G. Category-based routing
    """

    # Priority A: Check document confidence
    confidence_threshold = 0.5
    if structured_invoice.document_confidence < confidence_threshold:
        return FinanceRoutingDecision(
            target_system=FinanceTargetSystem.UNDETERMINED,
            routing_status=RoutingStatus.HUMAN_REVIEW,
            reason_code=RoutingReason.LOW_DOCUMENT_CONFIDENCE,
            route_confidence=0.0,
            explanation=f"Document confidence {structured_invoice.document_confidence} below threshold {confidence_threshold}",
        )

    # Priority B: Check existing human review requirement
    if structured_invoice.requires_human_review:
        return FinanceRoutingDecision(
            target_system=FinanceTargetSystem.UNDETERMINED,
            routing_status=RoutingStatus.HUMAN_REVIEW,
            reason_code=RoutingReason.EXISTING_HUMAN_REVIEW_REQUIREMENT,
            route_confidence=0.0,
            explanation="Extraction agent marked requires_human_review",
        )

    # Priority C-G: Category detection and mapping
    matched_categories, matched_evidence_terms = classify_business_category(structured_invoice)

    # Map categories to target systems
    mapped_systems = map_categories_to_systems(matched_categories)

    # Priority G: No category evidence
    if FinanceBusinessCategory.UNKNOWN in matched_categories and len(matched_categories) == 1:
        return FinanceRoutingDecision(
            target_system=FinanceTargetSystem.UNDETERMINED,
            routing_status=RoutingStatus.HUMAN_REVIEW,
            reason_code=RoutingReason.INSUFFICIENT_CATEGORY_EVIDENCE,
            matched_categories=list(matched_categories),
            matched_evidence_terms=list(matched_evidence_terms),
            route_confidence=0.0,
            explanation="No controlled category evidence found",
        )

    # Priority D: Conflict (multiple systems)
    if len(mapped_systems) > 1:
        return FinanceRoutingDecision(
            target_system=FinanceTargetSystem.UNDETERMINED,
            routing_status=RoutingStatus.HUMAN_REVIEW,
            reason_code=RoutingReason.ROUTING_CONFLICT,
            matched_categories=list(matched_categories),
            matched_evidence_terms=list(matched_evidence_terms),
            route_confidence=0.0,
            explanation=f"Conflicting target systems: {[s.value for s in mapped_systems]}",
        )

    # Priority E-F: Single system (one or more categories map to same system)
    if len(mapped_systems) == 1:
        target = list(mapped_systems)[0]

        # Calculate confidence
        confidence = 0.80  # Base single-category
        if len([c for c in matched_categories if c != FinanceBusinessCategory.UNKNOWN]) > 1:
            confidence = 0.90  # Multiple categories, same system

        # Add bonus for supporting references (Veson only)
        if target == FinanceTargetSystem.VESON_IMOS:
            if structured_invoice.voyage_reference:
                confidence = min(confidence + 0.05, 1.0)
            if structured_invoice.vessel_name or structured_invoice.vessel_imo:
                confidence = min(confidence + 0.05, 1.0)

        reason = (
            RoutingReason.MULTIPLE_CATEGORIES_SAME_SYSTEM
            if len([c for c in matched_categories if c != FinanceBusinessCategory.UNKNOWN]) > 1
            else RoutingReason.SINGLE_CATEGORY_MATCH
        )

        return FinanceRoutingDecision(
            target_system=target,
            routing_status=RoutingStatus.ROUTE_CANDIDATE,
            reason_code=reason,
            matched_categories=list(matched_categories),
            matched_evidence_terms=list(matched_evidence_terms),
            route_confidence=confidence,
            explanation=f"Categories {[c.value for c in matched_categories]} map to {target.value}",
        )

    # Fallback (should not reach)
    return FinanceRoutingDecision(
        target_system=FinanceTargetSystem.UNDETERMINED,
        routing_status=RoutingStatus.HUMAN_REVIEW,
        reason_code=RoutingReason.INSUFFICIENT_CATEGORY_EVIDENCE,
        route_confidence=0.0,
        explanation="Unable to classify",
    )


def route_invoice_via_workflow(structured_invoice: StructuredInvoice) -> FinanceRoutingDecision:
    """
    Route invoice through Kailash workflow for genuine run-id tracking.

    Returns FinanceRoutingDecision with authentic LocalRuntime workflow_run_id.
    """
    try:
        # Compute routing decision directly (this IS the workflow logic)
        decision = route_invoice(structured_invoice)

        # Execute through LocalRuntime to get authentic workflow_run_id
        builder = WorkflowBuilder()

        # Create handler node that returns the pre-computed decision
        def routing_handler() -> dict:
            """Handler returns pre-computed routing decision."""
            return {
                "target_system": decision.target_system.value,
                "routing_status": decision.routing_status.value,
                "reason_code": decision.reason_code.value,
                "matched_categories": [c.value for c in decision.matched_categories],
                "matched_evidence_terms": list(decision.matched_evidence_terms),
                "route_confidence": decision.route_confidence,
                "explanation": decision.explanation,
            }

        handler_node = HandlerNode(handler=routing_handler)
        node_id = builder.add_node(handler_node, "classify_finance_route")
        workflow = builder.build(workflow_id="finance_routing_workflow")

        # Execute workflow via LocalRuntime to get authentic run_id
        with LocalRuntime() as runtime:
            execution_result, workflow_run_id = runtime.execute(
                workflow,
                parameters={},
            )

        # Extract result from execution
        if not execution_result or "classify_finance_route" not in execution_result:
            return FinanceRoutingDecision(
                target_system=decision.target_system,
                routing_status=decision.routing_status,
                reason_code=decision.reason_code,
                matched_categories=decision.matched_categories,
                matched_evidence_terms=decision.matched_evidence_terms,
                route_confidence=decision.route_confidence,
                explanation=decision.explanation,
                workflow_run_id=workflow_run_id or "",
            )

        node_output = execution_result["classify_finance_route"]

        # Reconstruct decision from workflow output with authentic run_id
        return FinanceRoutingDecision(
            target_system=FinanceTargetSystem(node_output["target_system"]),
            routing_status=RoutingStatus(node_output["routing_status"]),
            reason_code=RoutingReason(node_output["reason_code"]),
            matched_categories=[FinanceBusinessCategory(c) for c in node_output.get("matched_categories", [])],
            matched_evidence_terms=node_output.get("matched_evidence_terms", []),
            route_confidence=node_output.get("route_confidence", 0.0),
            explanation=node_output.get("explanation", ""),
            workflow_run_id=workflow_run_id or "",
        )

    except Exception as e:
        # Fallback: return decision with empty run_id on workflow error
        decision_fallback = route_invoice(structured_invoice)
        decision_fallback.explanation = f"Workflow error (decision computed locally): {str(e)}"
        return decision_fallback
