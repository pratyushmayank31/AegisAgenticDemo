---
name: build-demo-step
description: Implement or correct one approved LdcDemo step using an evidence-backed workflow with native Kailash SDK integration.
disable-model-invocation: true
---

# build-demo-step: Finance Workflow Step Implementation

Use `/build-demo-step <step objective>` to implement or correct one focused step of the LdcDemo invoice processing workflow.

## Purpose

Deliver one self-contained workflow component with:
- Clear acceptance criteria and scope boundaries
- SDK-native implementation (Kailash DataFlow, Kaizen, PACT)
- Focused test coverage and live validation
- Evidence-based completion reporting

See `CLAUDE.md` for full project context (invoice processing pipeline: receipt → extraction → routing → approval → posting).

## Invocation

```bash
/build-demo-step <step objective>
```

The objective (captured in `$ARGUMENTS`) specifies what to implement or fix. Examples:
- `step 2: implement document extraction with confidence scoring`
- `step 3a: fix invoice routing decision logic`
- `step 4: add approval workflow`

## Delivery Process (14 Steps)

### 1. Read CLAUDE.md
Understand the project overview, data models, architecture, and existing dependencies. Identify the workflow stage relevant to the objective.

### 2. Inspect Implementation
Review current implementation in `src/`, `scripts/`, and `tests/` directories. Understand what already exists and what must remain unchanged.

### 3. Identify Scope
Document:
- **Requested outcome**: What should work when complete
- **Acceptance criteria**: Observable, testable conditions
- **Explicit exclusions**: What is out of scope (e.g., "Do not integrate with SAP yet")
- **Unchanged behavior**: What existing functionality must not break

### 4. Inspect SDK API
Before writing unfamiliar code, consult:
- **Kailash Core**: Database and model basics (DataFlow ORM, correlation_id tracking)
- **Kailash Kaizen**: LLM orchestration and multi-agent patterns
- **Kailash PACT**: Workflow/orchestration primitives and state machines
- **Project dependencies** in `requirements.txt` and `.venv/`

### 5. State Implementation Plan
Concise plan covering:
- Which files to create or modify
- Key SDK calls and APIs
- Test strategy (unit tests, integration tests, live execution)
- Expected workflow node IDs or runtime locations

### 6. Implement Requested Scope
- Prefer Kailash SDK methods over custom implementations
- Keep changes minimal and focused
- Do not add features beyond the step objective
- Do not refactor unrelated code

### 7. Add Focused Tests
Create or update tests in `tests/` directory:
- Use temporary databases (`test.db` or in-memory SQLite)
- Use mock LLM providers (do not call live APIs unless explicitly requested)
- Test the happy path and key edge cases
- Tests must be runnable and pass before reporting completion

### 8. Run Focused Tests
Execute only the test file(s) relevant to this step:
```bash
python -m pytest tests/test_<step_name>.py -v
```

### 9. Run Complete Test Suite
Verify no regressions:
```bash
python -m pytest tests/ -v
```

### 10. Perform Realistic Execution
Execute the workflow step in a realistic but safe scenario:
- Use sample invoices from `sample_invoices/` directory (15 PDF test cases available)
- Use the temporary database (`test.db`)
- Capture workflow run ID, node IDs, and output
- Do not assume live execution succeeded if it was not actually performed

### 11. Compare Against Acceptance Criteria
Verify that:
- Requested outcome is achieved
- All acceptance criteria are met
- No excluded functionality was implemented
- Unchanged behavior remains intact

### 12. Correct Failures
If acceptance criteria are not met:
- Investigate the root cause
- Fix within the requested scope
- Return to Step 8 (focused tests)
- Do not proceed to reporting if the step failed

### 13. Gather Completion Evidence
Collect and report:
- **Files changed** (relative paths)
- **SDK APIs executed** (actual method calls with parameters)
- **Workflow node IDs** (e.g., `decision_router_v1`, `approval_gate_v1`)
- **Workflow runtime location** (e.g., `data/finance_demo.db`)
- **Workflow run-ID source** (e.g., from `EventReceipt.correlation_id`, `FinanceDecision.id`)
- **Focused test result** (pass/fail, actual output)
- **Complete test-suite result** (all tests passing or list of failures)
- **Realistic/live execution result** (actual invoice processed, decision generated, etc.)
- **Warnings and limitations** (e.g., "Approval routing only supports known vendors")
- **Explicit exclusions confirmed** (list what was intentionally not implemented)

### 13a. Mandatory Independent Review Gate (ldc-sdk-reviewer)
**Required before reporting completion.**

Invoke `ldc-sdk-reviewer` with:
- **Step scope** (approved objective, acceptance criteria)
- **Modified files** (relative paths only)
- **Test evidence** (focused test output, full suite results)
- **Claimed SDK call chain** (actual method calls, parameters, and relevant Kailash Core, Kaizen, PACT, or DataFlow APIs)

**Reviewer behavior:**
- Read-only access only (no file modifications)
- Verifies genuine Kailash SDK usage (not mock implementations or imports without execution)
- Checks that execution evidence matches the approved acceptance criteria. Clearly distinguishes unit/mock testing, realistic local execution, and live external-provider execution. Mock execution must never be described as live.
- Returns: **PASS** or **FAIL** with findings

**If reviewer returns FAIL:**
1. Do not declare Step complete
2. Review reported findings and root causes
3. Remediate issues within requested Step scope only
4. Re-run focused tests (Step 8) and full suite (Step 9)
5. Re-run the applicable realistic local execution or live external-provider execution required by the Step acceptance criteria
6. Return to Step 13a: Re-invoke reviewer with updated evidence

**If reviewer returns PASS:**
1. Report the Step as **Accepted**
2. Record any non-blocking findings as follow-up items (not blocking this Step)
3. Proceed to Step 14

### 14. Return Evidence-Based Completion Report (PASS path only)
Provide a structured summary with:
- **Step status**: Accepted
- **Evidence** for each criterion (files, APIs, test results, live execution)
- **Reviewer approval**: ldc-sdk-reviewer PASS
- **Any SDK limitations or workarounds** discovered
- **Non-blocking follow-up findings** (if any)

## Mandatory Completion Rules

✅ **DO:**
- Invoke ldc-sdk-reviewer after gathering completion evidence (Step 13a)
- Claim completion only if ldc-sdk-reviewer returns PASS
- Show actual SDK API calls executed (not just imports or object construction)
- Report real workflow IDs and run IDs (not generated UUIDs)
- List all files changed with relative paths
- Describe warnings and limitations transparently
- Use temporary databases and mock providers in tests
- Remediate and re-review if reviewer returns FAIL (loop Steps 8-9-13a until PASS)

❌ **DO NOT:**
- Claim completion without ldc-sdk-reviewer PASS approval
- Claim completion if live execution failed or was only mock-simulated
- Describe mock execution as live/realistic execution
- Treat imports/object construction as evidence of SDK execution
- Present generated UUIDs as real Kailash runtime IDs
- Switch silently from live provider to mock without notification
- Hide SDK warnings or errors
- Call unvalidated code "production-ready"
- Claim zero technical debt
- Begin next Step automatically (await user instruction or /build-demo-step for next step)
- Create hooks or begin Step 5 automatically
- Print API keys or complete invoice text
- Read or display `.env` file
- Call live LLMs in tests unless explicitly requested

## Integration with Project Structure

**Source code**: `src/` (database models, services, agents, schema)
**Scripts**: `scripts/` (database init, intake, extraction examples)
**Tests**: `tests/` (test_*.py files, temporary databases)
**Sample data**: `sample_invoices/` (15 PDF test cases)
**Database**: `data/finance_demo.db` (SQLite, initialized via `scripts/init_database.py`)

**Key models** (in `src/database.py`):
- `InvoiceCase`: Consolidated invoice state
- `FinanceDecision`: LLM routing/coding decisions
- `ApprovalRequest`: Human approval workflows
- `ExceptionCase`: Exceptions and manual handling
- `PostingRecord`: Finance system posting results
- `BusinessAuditEvent`: Immutable audit trail (append-only, hashed)

## Reporting Language

Use "Step" not "Chapter." Example: "Step 2: Document Extraction completed with confidence scoring."

## Reference

- **Project overview**: See `CLAUDE.md` in project root
- **Data models**: `src/database.py`
- **Database schema**: Managed via Kailash DataFlow; initialize with `python -m scripts.init_database`
- **Intake flow**: `src/intake_service.py`
- **Document extraction**: `src/document_extractor.py`
- **Extraction agent**: `src/invoice_extraction_agent.py`
- **Test suite**: `tests/test_*.py` files
