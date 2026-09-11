# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**LdcDemo** is a Python-based finance invoice processing and orchestration system. It demonstrates:
- Document intake and tracking (invoice PDFs)
- LLM-driven document extraction and classification
- Multi-agent routing and decision-making (finance decisions, approvals, exceptions)
- Audit logging and event-sourcing patterns
- Workflow orchestration with human-in-the-loop approval gates

The system tracks invoices through multiple states: receipt → extraction → routing → approval → posting, with exception handling and business audit trails at each stage.

## Architecture

### Data Models (src/database.py)

Uses **Kailash DataFlow ORM** (sqlite-backed) with these primary models:

- **InvoiceCase**: Consolidated state of an invoice from receipt through posting. Tracks supplier, amounts, extraction confidence, status, and current owner.
- **FinanceDecision**: LLM-generated routing, coding, and governance decisions with confidence scores and rationale.
- **ApprovalRequest**: Human approval workflows — who requested, from whom, and current approval status.
- **ExceptionCase**: Safety net for invoices that don't fit normal flows (duplicates, missing references, validation failures). Tracks reason and owner.
- **PostingRecord**: Result of posting to finance systems (SAP, Oracle, etc.) with posting references and approver trail.
- **EventReceipt**: Deduplication record — prevents the same incoming event from creating duplicate cases.
- **BusinessAuditEvent**: Immutable business evidence (separate from agent memory) with actor, action, outcome, and cryptographic hashes.

All models share correlation_id/invoice_id for tracing across events.

### Directory Layout

- **src/**: Application source code
  - `database.py`: All data models and database configuration (SQLite at `data/finance_demo.db`)
  - `intake_service.py`: Placeholder for invoice intake/extraction service
- **scripts/**: Utilities
  - `init_database.py`: Creates database tables synchronously
- **sample_invoices/**: 15 PDF samples covering edge cases (clean documents, poor scans, handwritten, duplicates, cross-border, price mismatches, threshold violations)
- **data/**: SQLite database and WAL files
- **migrations/**: Empty; schema is managed via DataFlow models

## Common Commands

### Setup
```bash
source .venv/bin/activate          # Activate virtual environment (Python 3.12)
```

### Database
```bash
python -m scripts.init_database    # Initialize SQLite tables for all models
```

### Testing
```bash
# Tests directory is currently empty, but:
python -m pytest tests/            # Run all tests (when added)
python -m pytest tests/ -v         # Verbose output
python -m pytest tests/test_*.py   # Run specific test file
```

### Linting/Type Checking
```bash
# Not configured yet, but recommended:
python -m mypy src/                # Type checking
python -m black src/ scripts/       # Code formatting
python -m flake8 src/ scripts/      # Linting
```

## Dependencies

Key packages (in `.venv`):
- **kailash** / **kailash-dataflow**: ORM and database abstraction
- **kailash-kaizen**: Likely used for LLM orchestration/agents
- **kailash-mcp**: MCP (Model Context Protocol) support
- **kailash-pact**: Workflow/orchestration primitives
- **aiosqlite**: Async SQLite driver
- **asyncpg**: Async PostgreSQL driver (for future scaling)
- **httpx**: Async HTTP client (for external service calls)

## Key Development Notes

### Adding New Models
1. Define model in `src/database.py` with `@db.model` decorator
2. Include `id: str`, `correlation_id: str`, and relevant fields
3. Run `python scripts/init_database.py` to create tables

### Invoking Database Operations
All DataFlow models support async operations; methods are accessed via the `db` singleton:
```python
from src.database import db, InvoiceCase
# db.create_tables_sync()  # Already called by init_database.py
```

### Sample Invoices
The 13 PDFs in `sample_invoices/` represent real-world edge cases:
- Clean extraction vs. poor scans / handwriting
- Known vendors vs. unknown vendors
- Duplicate detection
- Threshold-based routing (AR Freight below/at/above thresholds)
- Cross-border / currency issues
- Bank change notifications
- Price mismatches requiring human intervention

Use these for integration testing document extraction and routing logic.

### Audit and Compliance
The **BusinessAuditEvent** model is immutable and cryptographically hashed (`previous_hash` → `event_hash`). Do not mutate these records — always append new events. This pattern supports regulatory compliance (GDPR, SOX, etc.) and forensic auditing.

## Future Expansion

- **intake_service.py** will likely implement document parsing and LLM-driven extraction
- Scale to async/concurrent processing for high-volume invoices
- Add external system integrations (SAP, Oracle, banking APIs)
- Implement MCP-based agent orchestration (kailash-mcp)
