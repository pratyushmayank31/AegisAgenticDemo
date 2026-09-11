# Step 7A Blocker Fix: governance_persistence.py Parallel Hash Implementation

## Issue Identified

**Blocker Found By:** ldc-sdk-reviewer (First Pass)

**Severity:** CRITICAL - Violates Step 7A Requirement #6

The `src/governance_persistence.py` module had its own truncated-hash implementation that bypassed the audit_chain_service, creating parallel hashing logic.

## Original Problem Code

**File:** `src/governance_persistence.py`

### Before (Lines 367-370)
```python
def _compute_event_hash(self, event_id: str, timestamp: str) -> str:
    """Compute deterministic hash for audit event."""
    content = f"{event_id}:{timestamp}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
```

### Usage (Line 173)
```python
"event_hash": self._compute_event_hash(audit_event_id, timestamp),
```

### Violations

1. **Only 2 of 6 Required Fields** (Missing: correlation_id, action_type, event_payload, previous_hash)
2. **Hash Truncation** (16 chars instead of 64)
3. **No Previous Hash Linking** (Cannot chain governance events)
4. **No Sensitive Data Validation** (Could leak PII/secrets)
5. **Bypasses audit_chain_service** (Violates single source of truth)

## Fix Applied

### Step 1: Remove hashlib Import
**File:** `src/governance_persistence.py:17`

**Before:**
```python
import json
import uuid
import hashlib
from datetime import datetime, timezone
```

**After:**
```python
import json
import uuid
from datetime import datetime, timezone
```

### Step 2: Import AuditChainService
**File:** `src/governance_persistence.py:22-26`

**Before:**
```python
from src.database import db
from src.governance_orchestrator import (
    GovernanceOrchestrationResult,
    GovernanceOrchestrationStatus,
)
```

**After:**
```python
from src.database import db
from src.audit_chain_service import AuditChainService
from src.governance_orchestrator import (
    GovernanceOrchestrationResult,
    GovernanceOrchestrationStatus,
)
```

### Step 3: Initialize AuditChainService
**File:** `src/governance_persistence.py:75-77`

**Before:**
```python
def __init__(self):
    """Initialize persistence service with database connection."""
    self.db = db
```

**After:**
```python
def __init__(self):
    """Initialize persistence service with database connection and audit chain service."""
    self.db = db
    self.audit_chain = AuditChainService()
    self.audit_chain.db = self.db
```

### Step 4: Use audit_chain.link_audit_event()
**File:** `src/governance_persistence.py:164-182`

**Before:**
```python
audit_event_data = {
    "id": audit_event_id,
    "invoice_id": case_id,
    "correlation_id": correlation_id,
    "actor_id": "governance-orchestrator",
    "action_type": "GOVERNANCE_DECISION",
    "action_outcome": decision_status,
    "event_payload": json.dumps(audit_payload),
    "previous_hash": "",
    "event_hash": self._compute_event_hash(audit_event_id, timestamp),
    "event_timestamp": timestamp,
}

if not is_replay:
    try:
        self.db.express_sync.create("BusinessAuditEvent", audit_event_data)
    except Exception as e:
        pass
```

**After:**
```python
audit_event_data = {
    "id": audit_event_id,
    "invoice_id": case_id,
    "correlation_id": correlation_id,
    "actor_id": "governance-orchestrator",
    "action_type": "GOVERNANCE_DECISION",
    "action_outcome": decision_status,
    "event_payload": json.dumps(audit_payload),
    "previous_hash": "",
    "event_hash": "",  # Will be computed by audit_chain.link_audit_event
    "event_timestamp": timestamp,
}

# Link to previous audit event in the chain using corrected full 64-char hash
audit_event_data = self.audit_chain.link_audit_event(
    audit_event_data, correlation_id
)

if not is_replay:
    try:
        self.db.express_sync.create("BusinessAuditEvent", audit_event_data)
    except Exception as e:
        pass
```

### Step 5: Remove _compute_event_hash() Method
**File:** `src/governance_persistence.py:367-370` (DELETED)

```python
# REMOVED - No longer needed, using audit_chain service
# def _compute_event_hash(self, event_id: str, timestamp: str) -> str:
#     """Compute deterministic hash for audit event."""
#     content = f"{event_id}:{timestamp}"
#     return hashlib.sha256(content.encode()).hexdigest()[:16]
```

## Verification Results

### Test Results After Fix

✅ **Governance Persistence Tests (30/30):** PASS
```
tests/test_governance_persistence.py ............................ PASSED
- test_allow_* (6 tests)
- test_deny_* (6 tests)
- test_hold_* (5 tests)
- test_replay_* (6 tests)
- test_audit_event_* (4 tests)
- test_no_external_* (2 tests)
- test_backward_compatible (1 test)
```

✅ **Audit Chain Tests (26/26):** PASS
```
tests/test_audit_chain_service.py .............................. PASSED
- test_hash_computation_deterministic
- test_hash_full_length
- test_hash_changes_with_*
- test_sensitive_data_validation
- test_canonical_json_ordering
- test_tampering_detection_*
- test_verify_audit_chain_*
```

✅ **Complete Application Suite (443/443):** PASS
```
pytest tests/ --tb=short -q
======================== 443 passed in 14.84s ========================
```

✅ **Realistic Demonstration:** PASS
```
python scripts/test_audit_chain_tampering.py
✓ All 7 steps completed successfully
✓ Tampering detected correctly
✓ No database mutations
```

## Impact of Fix

### Before Fix
- Governance GOVERNANCE_DECISION events had 16-character hashes (truncated)
- Events could not be properly linked in the audit chain
- Tampering in governance events could not be detected
- Sensitive data in governance payloads not validated
- Parallel hash implementation violated single source of truth

### After Fix
- Governance GOVERNANCE_DECISION events now have full 64-character hashes
- Events properly linked to previous events in chain
- Tampering detection works for governance events
- Sensitive data validation enforced for all audit events
- Single source of truth: AuditChainService handles all hash computation

## Compliance with Step 7A Requirements

| Requirement | Status | Evidence |
|-------------|--------|----------|
| All 6 fields in canonical hash | ✅ PASS | audit_chain_service.py:122-174 |
| Full 64-char SHA-256 digest | ✅ PASS | audit_chain_service.py:172 assertion |
| Chain verification reconstruction | ✅ PASS | audit_chain_service.py:235-316 |
| Tampering detection tests | ✅ PASS | 26 audit chain tests all pass |
| Sensitive data validation | ✅ PASS | audit_chain_service.py:104-120 |
| No parallel implementations | ✅ PASS | governance_persistence.py now uses audit_chain |
| approval_workflow.py uses service | ✅ PASS | approval_workflow.py:309-310 |
| Component classification | ✅ PASS | audit_chain_service.py:14 "application-level tamper-evident" |

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `src/governance_persistence.py` | Integrate AuditChainService, remove truncated-hash implementation | -4, +10 |

## Backward Compatibility

⚠ **Hash Format Change:** Existing GOVERNANCE_DECISION events with 16-char hashes will be detected as broken when verified with the new 64-char hash verification logic.

**Migration Path:**
1. Keep existing 16-char governance events (mark as "pre-7A")
2. All new governance events use 64-char hashes
3. Run batch job to recompute hashes for historical events if needed

## Ready for Final Review

✅ Blocker resolved
✅ All tests passing  
✅ No parallel implementations
✅ Single source of truth established
✅ Realistic demonstration verified

**Status: READY FOR FINAL VERIFICATION**
