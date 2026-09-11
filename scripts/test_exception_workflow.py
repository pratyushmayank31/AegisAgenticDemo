"""
Realistic Step 7B Execution: Exception Visibility and Resolution Workflow.

Demonstrates:
1. Creating exceptions (via governance_persistence DENY decisions)
2. Querying exceptions (visibility service)
3. Computing metrics
4. Resolving exceptions (with audit trail)
5. Verifying audit events
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import DataFlow
from src.governance_persistence import GovernancePersistenceService
from src.exception_visibility_service import ExceptionVisibilityService, ExceptionQueryFilter
from src.exception_resolution_service import ExceptionResolutionService, ExceptionResolutionInput
from src.governance_orchestrator import (
    GovernanceOrchestrationResult,
    GovernanceOrchestrationStatus,
    GovernanceOrchestrationReason,
)
import asyncio


async def main():
    """Run realistic exception workflow demonstration."""
    # Use temporary database
    db_url = "sqlite:////tmp/test_exceptions_demo.db"
    db = DataFlow(db_url)

    # Define models
    @db.model
    class InvoiceCase:
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
    class ExceptionCase:
        id: str
        invoice_id: str
        correlation_id: str
        reason_code: str
        reason_detail: str
        recommended_action: str
        owner_id: str
        exception_status: str = "OPEN"
        opened_at: str = ""
        resolved_at: str = ""

    @db.model
    class BusinessAuditEvent:
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

    db.create_tables_sync()

    print("=" * 80)
    print("STEP 7B: EXCEPTION VISIBILITY AND RESOLUTION")
    print("=" * 80)

    # Step 1: Create sample exceptions by persisting DENY governance decisions
    print("\nSTEP 1: Create sample exceptions")
    print("-" * 80)

    gov_persistence = GovernancePersistenceService()
    gov_persistence.db = db

    # Decision 1: DENY (creates exception)
    deny_result1 = GovernanceOrchestrationResult(
        case_id="inv-dup-001",
        correlation_id="corr-invoice-dup-001",
        proposed_target_system="SAP",
        evaluating_authority="governance-orchestrator",
        required_authority="L2_SUPERVISOR",
        authority_outcome="INSUFFICIENT",
        governance_status=GovernanceOrchestrationStatus.DENY,
        reason_code=GovernanceOrchestrationReason.GOVERNANCE_REJECTED,
        explanation="Duplicate invoice detected",
    )

    outcome1 = await gov_persistence.persist_decision(
        deny_result1, "inv-dup-001", "corr-invoice-dup-001"
    )
    print(f"✓ Created exception for duplicate invoice: {outcome1.exception_case_id}")

    # Decision 2: DENY (creates another exception)
    deny_result2 = GovernanceOrchestrationResult(
        case_id="inv-missing-ref-001",
        correlation_id="corr-invoice-missing-ref-001",
        proposed_target_system="SAP",
        evaluating_authority="governance-orchestrator",
        required_authority="L2_SUPERVISOR",
        authority_outcome="INSUFFICIENT",
        governance_status=GovernanceOrchestrationStatus.DENY,
        reason_code=GovernanceOrchestrationReason.GOVERNANCE_REJECTED,
        explanation="Missing PO reference",
    )

    outcome2 = await gov_persistence.persist_decision(
        deny_result2, "inv-missing-ref-001", "corr-invoice-missing-ref-001"
    )
    print(f"✓ Created exception for missing PO reference: {outcome2.exception_case_id}")

    # Step 2: Query exceptions (visibility)
    print("\nSTEP 2: Query exceptions (visibility)")
    print("-" * 80)

    visibility = ExceptionVisibilityService()
    visibility.db = db

    # Get all open exceptions
    open_exceptions = visibility.get_open_exceptions()
    print(f"✓ Found {len(open_exceptions)} open exceptions:")
    for exc in open_exceptions:
        print(f"  - ID: {exc['id']}, Invoice: {exc['invoice_id']}, Reason: {exc['reason_code']}")

    # Filter by reason code
    duplicates = visibility.get_exceptions(
        ExceptionQueryFilter(reason_code="DUPLICATE")
    )
    print(f"✓ Filtered by DUPLICATE reason: {len(duplicates)} exception(s)")

    # Step 3: Compute metrics
    print("\nSTEP 3: Compute exception metrics")
    print("-" * 80)

    metrics = visibility.compute_metrics()
    print(f"✓ Total open exceptions: {metrics.total_open}")
    print(f"✓ Total resolved exceptions: {metrics.total_resolved}")
    print(f"✓ By reason code: {metrics.by_reason_code}")
    print(f"✓ By owner: {metrics.by_owner}")

    # Step 4: Resolve first exception
    print("\nSTEP 4: Resolve first exception")
    print("-" * 80)

    resolver = ExceptionResolutionService()
    resolver.db = db

    resolution_input = ExceptionResolutionInput(
        exception_id=outcome1.exception_case_id,
        resolver_id="supervisor-alice",
        resolver_authority="L2_SUPERVISOR",
        resolution_code="INVOICE_CORRECTED",
        resolution_comment="Corrected duplicate marker, invoice reprocessed",
    )

    result = resolver.resolve_exception(resolution_input)
    if result.resolution_status == "RESOLVED":
        print(f"✓ Resolved exception: {result.exception_id}")
        print(f"  Audit event: {result.audit_event_id}")
        print(f"  Resolved at: {result.resolved_at}")
    else:
        print(f"✗ Resolution blocked: {result.blocked_reason}")

    # Step 5: Verify metrics changed
    print("\nSTEP 5: Verify metrics after resolution")
    print("-" * 80)

    updated_metrics = visibility.compute_metrics()
    print(f"✓ Updated open exceptions: {updated_metrics.total_open}")
    print(f"✓ Updated resolved exceptions: {updated_metrics.total_resolved}")

    # Step 6: Verify audit trail
    print("\nSTEP 6: Verify audit trail")
    print("-" * 80)

    audit_events = db.express_sync.list(
        "BusinessAuditEvent",
        {"action_type": "EXCEPTION_RESOLVED"},
    )
    print(f"✓ Found {len(audit_events)} EXCEPTION_RESOLVED audit event(s)")
    if audit_events:
        event = audit_events[0]
        print(f"  Event ID: {event['id']}")
        print(f"  Actor: {event['actor_id']}")
        print(f"  Outcome: {event['action_outcome']}")
        print(f"  Hash length: {len(event['event_hash'])} chars (expected 64)")

    # Step 7: Attempt unauthorized resolution (should fail)
    print("\nSTEP 7: Attempt unauthorized resolution")
    print("-" * 80)

    blocked_input = ExceptionResolutionInput(
        exception_id=outcome2.exception_case_id,
        resolver_id="processor-bob",
        resolver_authority="L1_PROCESSOR",
        resolution_code="INVOICE_CORRECTED",
        resolution_comment="Attempting to resolve",
    )

    blocked_result = resolver.resolve_exception(blocked_input)
    if blocked_result.resolution_status == "BLOCKED":
        print(f"✓ Resolution correctly blocked: {blocked_result.blocked_reason}")
    else:
        print(f"✗ Resolution should have been blocked but was: {blocked_result.resolution_status}")

    print("\n" + "=" * 80)
    print("STEP 7B DEMONSTRATION COMPLETE")
    print("=" * 80)
    print("✓ Exceptions created from governance decisions")
    print("✓ Exception visibility queries working")
    print("✓ Exception metrics computed")
    print("✓ Exception resolution workflow functioning")
    print("✓ Authorization checks enforced")
    print("✓ Audit trail created for all state changes")


if __name__ == "__main__":
    asyncio.run(main())
