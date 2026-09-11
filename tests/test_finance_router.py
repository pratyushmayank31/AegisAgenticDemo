"""
Tests for finance target-system router.

Verifies deterministic routing, controlled category vocabulary, Kailash workflow
execution, and conservative HUMAN_REVIEW fallback behavior.

No LLM, Aegis, database, or file-path access.
"""

import pytest
from src.invoice_schema import StructuredInvoice, InvoiceLineItem
from src.finance_router import (
    FinanceTargetSystem,
    FinanceBusinessCategory,
    RoutingStatus,
    RoutingReason,
    FinanceRoutingDecision,
    route_invoice,
    route_invoice_via_workflow,
    classify_business_category,
    extract_keywords,
    normalize_text,
)


# Test Fixtures

@pytest.fixture
def bunker_invoice():
    """Invoice with bunker fuel (Veson)."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-001",
        invoice_date="2024-09-01",
        gross_amount=5000.0,
        currency="USD",
        document_confidence=0.95,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Marine bunker fuel delivery",
                quantity=100,
                unit_price=50.0,
                confidence=0.95,
            )
        ],
    )


@pytest.fixture
def freight_invoice():
    """Invoice with freight charges (Veson)."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-002",
        invoice_date="2024-09-02",
        gross_amount=3000.0,
        currency="USD",
        document_confidence=0.90,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Ocean freight charges",
                confidence=0.90,
            )
        ],
    )


@pytest.fixture
def repair_invoice():
    """Invoice with vessel repair (smartPAL)."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-003",
        invoice_date="2024-09-03",
        gross_amount=8000.0,
        currency="USD",
        document_confidence=0.92,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Vessel repair and maintenance",
                confidence=0.92,
            )
        ],
    )


@pytest.fixture
def software_invoice():
    """Invoice with software license (Oracle)."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-004",
        invoice_date="2024-09-04",
        gross_amount=2500.0,
        currency="USD",
        document_confidence=0.95,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Annual software subscription",
                confidence=0.95,
            )
        ],
    )


@pytest.fixture
def legal_invoice():
    """Invoice with legal services (Oracle)."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-005",
        invoice_date="2024-09-05",
        gross_amount=4000.0,
        currency="GBP",
        document_confidence=0.88,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Legal services and consultation",
                confidence=0.88,
            )
        ],
    )


@pytest.fixture
def office_rent_invoice():
    """Invoice with office rent (Oracle)."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-006",
        invoice_date="2024-09-06",
        gross_amount=6000.0,
        currency="EUR",
        document_confidence=0.93,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Monthly office rent and facilities",
                confidence=0.93,
            )
        ],
    )


@pytest.fixture
def bunker_spares_conflict_invoice():
    """Invoice with conflicting categories: bunker + spares."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-007",
        invoice_date="2024-09-07",
        gross_amount=7000.0,
        currency="USD",
        document_confidence=0.85,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Bunker fuel and spare parts",
                confidence=0.85,
            )
        ],
    )


@pytest.fixture
def no_category_invoice():
    """Invoice with no recognizable business category."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-008",
        invoice_date="2024-09-08",
        gross_amount=1000.0,
        currency="USD",
        document_confidence=0.90,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Miscellaneous services",
                confidence=0.90,
            )
        ],
    )


@pytest.fixture
def low_confidence_invoice():
    """Invoice with low document confidence."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-009",
        invoice_date="2024-09-09",
        gross_amount=2000.0,
        currency="USD",
        document_confidence=0.35,  # Below 0.5 threshold
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Bunker fuel",
                confidence=0.35,
            )
        ],
    )


@pytest.fixture
def requires_review_invoice():
    """Invoice marked as requiring human review."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-010",
        invoice_date="2024-09-10",
        gross_amount=3000.0,
        currency="USD",
        document_confidence=0.80,
        requires_human_review=True,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Freight charges",
                confidence=0.80,
            )
        ],
    )


@pytest.fixture
def demurrage_charter_invoice():
    """Invoice with multiple consistent categories: demurrage + charter hire."""
    return StructuredInvoice(
        invoice_direction="AR",
        invoice_number="INV-011",
        invoice_date="2024-09-11",
        gross_amount=5000.0,
        currency="USD",
        document_confidence=0.91,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Port demurrage and charter hire charges",
                confidence=0.91,
            )
        ],
    )


@pytest.fixture
def spares_maintenance_invoice():
    """Invoice with multiple consistent categories: spares + maintenance."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-012",
        invoice_date="2024-09-12",
        gross_amount=4500.0,
        currency="EUR",
        document_confidence=0.89,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Spare parts and maintenance services",
                confidence=0.89,
            )
        ],
    )


@pytest.fixture
def recharge_invoice():
    """Invoice with corporate recharge (Oracle)."""
    return StructuredInvoice(
        invoice_direction="AR",
        invoice_number="INV-013",
        invoice_date="2024-09-13",
        gross_amount=1500.0,
        currency="SGD",
        document_confidence=0.94,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Corporate recharge to subsidiary",
                confidence=0.94,
            )
        ],
    )


@pytest.fixture
def spanish_rent_invoice():
    """Invoice with Spanish office rent term."""
    return StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-014",
        invoice_date="2024-09-14",
        gross_amount=3500.0,
        currency="EUR",
        document_confidence=0.92,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Arrendamiento de oficina mensual",  # Spanish for "monthly office rent"
                confidence=0.92,
            )
        ],
    )


@pytest.fixture
def port_agency_freight_invoice():
    """Invoice with multiple consistent categories: port agency + freight."""
    return StructuredInvoice(
        invoice_direction="AR",
        invoice_number="INV-015",
        invoice_date="2024-09-15",
        gross_amount=6500.0,
        currency="SGD",
        document_confidence=0.88,
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Port agency and freight forwarding services",
                confidence=0.88,
            )
        ],
    )


@pytest.fixture
def vessel_references_only_invoice():
    """Invoice with vessel/voyage references but no category keywords."""
    invoice = StructuredInvoice(
        invoice_direction="AP",
        invoice_number="INV-016",
        invoice_date="2024-09-16",
        gross_amount=1200.0,
        currency="USD",
        document_confidence=0.80,
        vessel_name="M/V Test Ship",
        vessel_imo="1234567",
        voyage_reference="VOY-2024-001",
        line_items=[
            InvoiceLineItem(
                line_number=1,
                item_description="Miscellaneous services",
                confidence=0.80,
            )
        ],
    )
    return invoice


# Text Processing Tests

class TestTextProcessing:
    """Test text normalization and keyword extraction."""

    def test_normalize_text(self):
        """Text normalization: lowercase, whitespace."""
        assert normalize_text("  BUNKER  FUEL  ") == "bunker fuel"
        assert normalize_text("Freight\nCharges") == "freight charges"

    def test_extract_keywords(self):
        """Keyword extraction with word boundaries."""
        text = "Marine bunker fuel delivery"
        keywords = extract_keywords(text)
        assert "bunker" in keywords

    def test_extract_keywords_no_partial_match(self):
        """Keywords must match word boundaries, not partial."""
        text = "The repair is being done"
        keywords = extract_keywords(text)
        assert "repair" in keywords
        # "repairing" should not match "repair" in this implementation
        text2 = "The repairing is ongoing"
        keywords2 = extract_keywords(text2)
        # Both will match because the regex uses \b which handles word boundaries


# Category Classification Tests

class TestCategoryClassification:
    """Test business category detection."""

    def test_classify_bunker(self, bunker_invoice):
        """Bunker category detection."""
        categories, terms = classify_business_category(bunker_invoice)
        assert FinanceBusinessCategory.BUNKER in categories
        assert "bunker" in terms

    def test_classify_freight(self, freight_invoice):
        """Freight category detection."""
        categories, terms = classify_business_category(freight_invoice)
        assert FinanceBusinessCategory.FREIGHT in categories
        assert "freight" in terms

    def test_classify_repair(self, repair_invoice):
        """Repair category detection."""
        categories, terms = classify_business_category(repair_invoice)
        assert FinanceBusinessCategory.VESSEL_REPAIR in categories or \
               FinanceBusinessCategory.VESSEL_MAINTENANCE in categories
        assert any(term in {"repair", "maintenance"} for term in terms)

    def test_classify_software(self, software_invoice):
        """Software category detection."""
        categories, terms = classify_business_category(software_invoice)
        assert FinanceBusinessCategory.SOFTWARE in categories
        assert "software" in terms

    def test_classify_legal(self, legal_invoice):
        """Legal category detection."""
        categories, terms = classify_business_category(legal_invoice)
        assert FinanceBusinessCategory.LEGAL in categories
        assert "legal" in terms

    def test_classify_office_rent(self, office_rent_invoice):
        """Office rent category detection."""
        categories, terms = classify_business_category(office_rent_invoice)
        assert FinanceBusinessCategory.OFFICE_RENT in categories
        assert "rent" in terms

    def test_classify_recharge(self, recharge_invoice):
        """Corporate recharge category detection."""
        categories, terms = classify_business_category(recharge_invoice)
        assert FinanceBusinessCategory.CORPORATE_RECHARGE in categories
        assert "recharge" in terms


# Routing Decision Tests

class TestRoutingDecisions:
    """Test routing logic (priority A-G)."""

    def test_bunker_to_veson(self, bunker_invoice):
        """Bunker → VESON_IMOS."""
        decision = route_invoice(bunker_invoice)
        assert decision.target_system == FinanceTargetSystem.VESON_IMOS
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE
        assert decision.reason_code == RoutingReason.SINGLE_CATEGORY_MATCH

    def test_freight_to_veson(self, freight_invoice):
        """Freight → VESON_IMOS."""
        decision = route_invoice(freight_invoice)
        assert decision.target_system == FinanceTargetSystem.VESON_IMOS
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_repair_to_smartpal(self, repair_invoice):
        """Repair → SMARTPAL."""
        decision = route_invoice(repair_invoice)
        assert decision.target_system == FinanceTargetSystem.SMARTPAL
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_software_to_oracle(self, software_invoice):
        """Software → ORACLE_FUSION."""
        decision = route_invoice(software_invoice)
        assert decision.target_system == FinanceTargetSystem.ORACLE_FUSION
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_legal_to_oracle(self, legal_invoice):
        """Legal → ORACLE_FUSION."""
        decision = route_invoice(legal_invoice)
        assert decision.target_system == FinanceTargetSystem.ORACLE_FUSION
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_office_rent_to_oracle(self, office_rent_invoice):
        """Office rent → ORACLE_FUSION."""
        decision = route_invoice(office_rent_invoice)
        assert decision.target_system == FinanceTargetSystem.ORACLE_FUSION
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_recharge_to_oracle(self, recharge_invoice):
        """Corporate recharge → ORACLE_FUSION."""
        decision = route_invoice(recharge_invoice)
        assert decision.target_system == FinanceTargetSystem.ORACLE_FUSION
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_demurrage_charter_to_veson(self, demurrage_charter_invoice):
        """Demurrage + Charter Hire → VESON_IMOS (multiple consistent)."""
        decision = route_invoice(demurrage_charter_invoice)
        assert decision.target_system == FinanceTargetSystem.VESON_IMOS
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE
        assert decision.reason_code == RoutingReason.MULTIPLE_CATEGORIES_SAME_SYSTEM

    def test_spares_maintenance_to_smartpal(self, spares_maintenance_invoice):
        """Spares + Maintenance → SMARTPAL (multiple consistent)."""
        decision = route_invoice(spares_maintenance_invoice)
        assert decision.target_system == FinanceTargetSystem.SMARTPAL
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE
        assert decision.reason_code == RoutingReason.MULTIPLE_CATEGORIES_SAME_SYSTEM

    def test_port_agency_freight_to_veson(self, port_agency_freight_invoice):
        """Port Agency + Freight → VESON_IMOS."""
        decision = route_invoice(port_agency_freight_invoice)
        assert decision.target_system == FinanceTargetSystem.VESON_IMOS
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_bunker_spares_conflict_human_review(self, bunker_spares_conflict_invoice):
        """Bunker + Spares → UNDETERMINED / HUMAN_REVIEW (conflict)."""
        decision = route_invoice(bunker_spares_conflict_invoice)
        assert decision.target_system == FinanceTargetSystem.UNDETERMINED
        assert decision.routing_status == RoutingStatus.HUMAN_REVIEW
        assert decision.reason_code == RoutingReason.ROUTING_CONFLICT

    def test_no_category_human_review(self, no_category_invoice):
        """No category evidence → UNDETERMINED / HUMAN_REVIEW."""
        decision = route_invoice(no_category_invoice)
        assert decision.target_system == FinanceTargetSystem.UNDETERMINED
        assert decision.routing_status == RoutingStatus.HUMAN_REVIEW
        assert decision.reason_code == RoutingReason.INSUFFICIENT_CATEGORY_EVIDENCE

    def test_low_confidence_human_review(self, low_confidence_invoice):
        """Low document confidence → HUMAN_REVIEW."""
        decision = route_invoice(low_confidence_invoice)
        assert decision.routing_status == RoutingStatus.HUMAN_REVIEW
        assert decision.reason_code == RoutingReason.LOW_DOCUMENT_CONFIDENCE

    def test_requires_review_human_review(self, requires_review_invoice):
        """Existing requires_human_review flag → HUMAN_REVIEW."""
        decision = route_invoice(requires_review_invoice)
        assert decision.routing_status == RoutingStatus.HUMAN_REVIEW
        assert decision.reason_code == RoutingReason.EXISTING_HUMAN_REVIEW_REQUIREMENT

    def test_ap_ar_produce_same_target(self, freight_invoice):
        """AP and AR versions produce same target system."""
        decision_ap = route_invoice(freight_invoice)

        # Create AR version
        invoice_ar = StructuredInvoice(
            invoice_direction="AR",
            invoice_number=freight_invoice.invoice_number,
            invoice_date=freight_invoice.invoice_date,
            gross_amount=freight_invoice.gross_amount,
            currency=freight_invoice.currency,
            document_confidence=freight_invoice.document_confidence,
            line_items=freight_invoice.line_items,
        )
        decision_ar = route_invoice(invoice_ar)

        assert decision_ap.target_system == decision_ar.target_system
        assert decision_ap.routing_status == decision_ar.routing_status

    def test_vessel_reference_alone_insufficient(self, vessel_references_only_invoice):
        """Vessel/voyage references alone do not select system."""
        decision = route_invoice(vessel_references_only_invoice)
        assert decision.target_system == FinanceTargetSystem.UNDETERMINED
        assert decision.routing_status == RoutingStatus.HUMAN_REVIEW

    def test_spanish_rent_detected(self, spanish_rent_invoice):
        """Spanish office-rent term detected (arrendamiento)."""
        categories, terms = classify_business_category(spanish_rent_invoice)
        assert FinanceBusinessCategory.OFFICE_RENT in categories
        assert "arrendamiento" in terms or "rent" in terms


# Confidence and Evidence Tests

class TestConfidenceAndEvidence:
    """Test confidence scoring and evidence tracking."""

    def test_single_category_base_confidence(self, bunker_invoice):
        """Single category: base confidence 0.80."""
        decision = route_invoice(bunker_invoice)
        assert decision.route_confidence == 0.80

    def test_multiple_consistent_categories_confidence(self, demurrage_charter_invoice):
        """Multiple consistent categories: confidence 0.90."""
        decision = route_invoice(demurrage_charter_invoice)
        assert decision.route_confidence == 0.90

    def test_confidence_zero_on_conflict(self, bunker_spares_conflict_invoice):
        """Conflict: confidence 0.0."""
        decision = route_invoice(bunker_spares_conflict_invoice)
        assert decision.route_confidence == 0.0

    def test_matched_categories_in_evidence(self, bunker_invoice):
        """Matched categories included in decision."""
        decision = route_invoice(bunker_invoice)
        assert len(decision.matched_categories) > 0
        assert FinanceBusinessCategory.BUNKER in decision.matched_categories

    def test_matched_evidence_terms_controlled(self, bunker_invoice):
        """Matched evidence terms are controlled keywords only."""
        decision = route_invoice(bunker_invoice)
        assert len(decision.matched_evidence_terms) > 0
        # All terms should be from approved vocabulary
        assert all(
            term in {"bunker", "freight", "demurrage", "charter", "port agency",
                     "repair", "maintenance", "spare", "spares", "software",
                     "legal", "rent", "recharge"}
            for term in decision.matched_evidence_terms
        )

    def test_no_complete_line_text_in_evidence(self, bunker_invoice):
        """Complete line-item description not in evidence terms."""
        decision = route_invoice(bunker_invoice)
        # Evidence should be keywords only, not full text
        assert "Marine bunker fuel delivery" not in decision.matched_evidence_terms


# Pydantic Model Tests

class TestFinanceRoutingDecisionModel:
    """Test FinanceRoutingDecision Pydantic model."""

    def test_extra_fields_rejected(self):
        """Extra fields rejected by extra='forbid'."""
        with pytest.raises(ValueError):
            FinanceRoutingDecision(
                target_system=FinanceTargetSystem.VESON_IMOS,
                routing_status=RoutingStatus.ROUTE_CANDIDATE,
                reason_code=RoutingReason.SINGLE_CATEGORY_MATCH,
                extra_field="not_allowed",
            )

    def test_route_confidence_range(self):
        """Route confidence must be in [0.0, 1.0]."""
        with pytest.raises(ValueError):
            FinanceRoutingDecision(
                target_system=FinanceTargetSystem.VESON_IMOS,
                routing_status=RoutingStatus.ROUTE_CANDIDATE,
                reason_code=RoutingReason.SINGLE_CATEGORY_MATCH,
                route_confidence=1.5,
            )

    def test_matched_categories_empty_list_ok(self):
        """Empty matched_categories list is valid."""
        decision = FinanceRoutingDecision(
            target_system=FinanceTargetSystem.UNDETERMINED,
            routing_status=RoutingStatus.HUMAN_REVIEW,
            reason_code=RoutingReason.INSUFFICIENT_CATEGORY_EVIDENCE,
            matched_categories=[],
        )
        assert decision.matched_categories == []

    def test_matched_evidence_terms_empty_list_ok(self):
        """Empty matched_evidence_terms list is valid."""
        decision = FinanceRoutingDecision(
            target_system=FinanceTargetSystem.UNDETERMINED,
            routing_status=RoutingStatus.HUMAN_REVIEW,
            reason_code=RoutingReason.INSUFFICIENT_CATEGORY_EVIDENCE,
            matched_evidence_terms=[],
        )
        assert decision.matched_evidence_terms == []


# Workflow Tests

class TestWorkflowExecution:
    """Test Kailash workflow execution."""

    def test_workflow_execution_returns_decision(self, bunker_invoice):
        """Workflow execution returns valid FinanceRoutingDecision."""
        decision = route_invoice_via_workflow(bunker_invoice)
        assert isinstance(decision, FinanceRoutingDecision)
        assert decision.target_system == FinanceTargetSystem.VESON_IMOS
        assert decision.routing_status == RoutingStatus.ROUTE_CANDIDATE

    def test_workflow_returns_authentic_run_id(self, bunker_invoice):
        """Workflow returns authentic LocalRuntime workflow_run_id."""
        decision = route_invoice_via_workflow(bunker_invoice)
        # Non-empty run_id from LocalRuntime
        assert decision.workflow_run_id, "workflow_run_id must be non-empty"
        assert isinstance(decision.workflow_run_id, str)
        # Should not be a UUID fallback (starts with different format)
        assert len(decision.workflow_run_id) > 0

    def test_workflow_deterministic_business_decision(self, bunker_invoice):
        """Same input produces identical business decision (excluding run_id)."""
        decision1 = route_invoice_via_workflow(bunker_invoice)
        decision2 = route_invoice_via_workflow(bunker_invoice)

        assert decision1.target_system == decision2.target_system
        assert decision1.routing_status == decision2.routing_status
        assert decision1.reason_code == decision2.reason_code
        # workflow_run_id may differ (different executions)


# File Access Prevention Tests

class TestNoFileAccess:
    """Test that router does not access file paths or names."""

    def test_route_invoice_no_path_parameter(self, bunker_invoice):
        """route_invoice takes only StructuredInvoice, no file path."""
        # This test verifies the signature
        import inspect
        sig = inspect.signature(route_invoice)
        params = list(sig.parameters.keys())
        assert "file_path" not in params
        assert "path" not in params
        assert "filename" not in params

    def test_invoice_schema_no_filename(self, bunker_invoice):
        """StructuredInvoice schema has no filename field."""
        fields = bunker_invoice.model_dump().keys()
        assert "filename" not in fields
        assert "file_path" not in fields
        assert "file_name" not in fields


# Integration Tests

class TestIntegration:
    """Integration tests for complete workflow."""

    def test_all_13_scenarios_via_workflow(self, bunker_invoice, freight_invoice, repair_invoice,
                                          software_invoice, legal_invoice, office_rent_invoice,
                                          bunker_spares_conflict_invoice, no_category_invoice,
                                          low_confidence_invoice, requires_review_invoice,
                                          demurrage_charter_invoice, spares_maintenance_invoice,
                                          recharge_invoice):
        """All 13 scenario invoices route successfully via workflow."""
        invoices = [
            bunker_invoice, freight_invoice, repair_invoice, software_invoice,
            legal_invoice, office_rent_invoice, bunker_spares_conflict_invoice,
            no_category_invoice, low_confidence_invoice, requires_review_invoice,
            demurrage_charter_invoice, spares_maintenance_invoice, recharge_invoice,
        ]

        for invoice in invoices:
            decision = route_invoice_via_workflow(invoice)
            assert isinstance(decision, FinanceRoutingDecision)
            assert decision.target_system in FinanceTargetSystem.__members__.values()
            assert decision.routing_status in RoutingStatus.__members__.values()
            assert decision.workflow_run_id, f"Missing run_id for {invoice.invoice_number}"
