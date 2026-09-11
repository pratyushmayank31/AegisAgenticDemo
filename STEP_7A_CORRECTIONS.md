# Step 7A Corrections: Canonical Event Hashing and Tamper Detection

## Contract Failures Fixed

### 1. Canonical Event Hashing (Failure 1)

**Issue:** Hash computation used only `event_id:timestamp` and truncated to 16 chars.

**Fixed in:** `src/audit_chain_service.py`
- Updated `compute_event_hash()` to include ALL required fields:
  - `event_id`
  - `correlation_id` ✓ (was missing)
  - `action_type` ✓ (was missing)
  - `event_payload` (canonical JSON) ✓ (was missing)
  - `timestamp`
  - `previous_hash`

**Implementation:**
```python
def compute_event_hash(
    self,
    event_id: str,
    correlation_id: str,
    action_type: str,
    event_payload: str,
    timestamp: str,
    previous_hash: str = ""
) -> str:
    """Compute deterministic SHA256 hash for audit event."""
    self._validate_no_sensitive_data(event_payload)
    canonical_payload = self._canonicalize_payload(event_payload)
    
    canonical_event = {
        "action_type": action_type,
        "correlation_id": correlation_id,
        "event_id": event_id,
        "event_payload": canonical_payload,
        "previous_hash": previous_hash,
        "timestamp": timestamp,
    }
    
    canonical_json = json.dumps(canonical_event, sort_keys=True, separators=(",", ":"))
    canonical_bytes = canonical_json.encode("utf-8")
    full_hash = hashlib.sha256(canonical_bytes).hexdigest()
    
    assert len(full_hash) == 64, f"SHA256 hash must be 64 chars"
    return full_hash
```

### 2. Full 64-Character Hash Storage (Failure 2)

**Issue:** Hash was truncated with `[:16]` instead of using full 64-char SHA-256.

**Fixed in:**
- `src/audit_chain_service.py:compute_event_hash()` — Returns full 64-char hex
- `src/approval_workflow.py:record_approval_decision()` — Removed local `_compute_event_hash()` method that truncated

**Changed:** All hash fields now store exactly 64 hexadecimal characters.

### 3. Chain Verification with Canonical Reconstruction (Failure 3)

**Issue:** Verification only checked `previous_hash` links, not actual hash validity.

**Fixed in:** `src/audit_chain_service.py:verify_audit_chain()`
- Now reconstructs canonical event content from stored records
- Recomputes hash using all fields
- Detects tampering when payload/action_type changed (hash mismatch)

**Detection Logic:**
```python
# Recompute hash from all event content
recomputed_hash = self.compute_event_hash(
    event_id=event_id,
    correlation_id=correlation_id,
    action_type=action_type,
    event_payload=event_payload,
    timestamp=timestamp,
    previous_hash=stored_previous_hash,
)

# Detect payload/action tampering by hash mismatch
if recomputed_hash != stored_hash:
    issues.append(f"Event {event_id} hash mismatch (possible tampering)")
```

### 4. Negative Tests for Tampering Detection (Failure 4)

**Added in:** `tests/test_audit_chain_service.py`

New test cases proving detection when:
1. `event_payload` is modified → BROKEN
2. `action_type` is modified → BROKEN
3. `correlation_id` is modified (via query) → BROKEN
4. `previous_hash` is modified → BROKEN (broken link)
5. `stored event_hash` is modified → BROKEN (hash mismatch)

Example test:
```python
def test_tampering_detection_payload_modified(audit_chain_service, test_db):
    """Test detection when event_payload is modified (BROKEN)."""
    # Create valid event with hash
    # Simulate tampering: modify stored payload in database
    # Verify chain returns BROKEN with hash mismatch issue
    assert is_valid is False
    assert any("hash mismatch" in issue.lower() for issue in issues)
```

### 5. Sensitive Data Validation (Failure 5)

**Added in:** `src/audit_chain_service.py`

Prohibited fields (case-insensitive):
- `raw_text`, `extracted_text`, `bank_details`, `account_number`
- `api_key`, `token`, `secret`, `password`

**Implementation:**
```python
PROHIBITED_PAYLOAD_FIELDS = {
    "raw_text", "extracted_text", "bank_details", "account_number",
    "api_key", "token", "secret", "password",
}

def _validate_no_sensitive_data(self, event_payload: str) -> None:
    """Validate that event_payload does not contain prohibited fields."""
    payload_dict = json.loads(event_payload)
    for key in payload_dict.keys():
        if key.lower() in PROHIBITED_PAYLOAD_FIELDS:
            raise ValueError(f"Prohibited sensitive field '{key}'")
```

**Test:** `test_sensitive_data_validation()` raises ValueError when api_key present.

### 6. Canonical JSON Ordering (Failure - Implicit)

**Added in:** `src/audit_chain_service.py:_canonicalize_payload()`

Ensures consistent hashing regardless of JSON key order:
```python
def _canonicalize_payload(self, event_payload: str) -> str:
    """Parse and re-serialize event_payload as canonical JSON."""
    payload_dict = json.loads(event_payload)
    return json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
```

**Test:** `test_canonical_json_ordering()` verifies same payload with different key order produces identical hash.

### 7. approval_workflow.py Integration (Failure 6)

**Fixed in:** `src/approval_workflow.py`

**Changes:**
1. Removed local `_compute_event_hash()` method (line 442-445)
2. Removed `hashlib` import (no longer needed)
3. Set `event_hash=""` initially, letting `audit_chain.link_audit_event()` compute correct full hash
4. Updated comment to clarify full 64-character hash is computed

**Before:**
```python
"event_hash": self._compute_event_hash(audit_event_id, timestamp),  # 16 chars
```

**After:**
```python
"event_hash": "",  # Will be computed by audit_chain.link_audit_event
# Link to previous audit event in the chain (Step 7A)
audit_event_data = self.audit_chain.link_audit_event(
    audit_event_data, correlation_id
)
```

### 8. Realistic Temporary Database Demonstration (Failure 7)

**Created:** `scripts/test_audit_chain_tampering.py`

Demonstrates in temporary SQLite:
1. ✓ Creates 3 linked events (INVOICE_RECEIVED → APPROVAL_REQUESTED → APPROVAL_DECISION)
2. ✓ Verifies VALID chain with correct hashes
3. ✓ Modifies Event 2 payload (requested_from L2_SUPERVISOR → L3_CONTROLLER)
4. ✓ Verifies BROKEN chain (hash mismatch detected on Event 2)
5. ✓ Restores original payload
6. ✓ Verifies VALID chain again
7. ✓ No production database mutation (temporary SQLite cleanup)

**Run:** `python scripts/test_audit_chain_tampering.py`

**Output:**
```
✓ Event 1 (INVOICE_RECEIVED): aud-001
✓ Event 2 (APPROVAL_REQUESTED): aud-002
✓ Event 3 (APPROVAL_DECISION): aud-003
Chain status: ✓ VALID
...
✗ Event 2 payload modified
Chain status: ✗ BROKEN
Issues: 1 - Event aud-002 hash mismatch (possible tampering)
...
✓ Event 2 payload restored
Chain status: ✓ VALID
```

## Test Suite Updates

Updated `tests/test_audit_chain_service.py`:

**Deterministic Hash Tests:**
- `test_hash_computation_deterministic()` — Same inputs → same hash (64 chars)
- `test_hash_full_length()` — Asserts exactly 64 hex characters
- `test_canonical_json_ordering()` — Different key order → same hash

**Tampering Detection Tests (NEW):**
- `test_hash_changes_with_payload()` — Payload modification detected
- `test_hash_changes_with_action_type()` — Action type modification detected
- `test_hash_changes_with_correlation_id()` — Correlation ID change detected
- `test_hash_changes_with_previous_hash()` — Link modification detected
- `test_tampering_detection_payload_modified()` — Stored payload tampering → BROKEN
- `test_tampering_detection_action_type_modified()` — Stored action type tampering → BROKEN
- `test_tampering_detection_previous_hash_modified()` — Stored link tampering → BROKEN
- `test_tampering_detection_event_hash_modified()` — Stored hash tampering → BROKEN

**Sensitive Data Tests:**
- `test_sensitive_data_validation()` — Prohibits api_key in payload

**Chain Verification Tests (UPDATED):**
- Updated all tests to use full 64-char hashes
- Updated hash computation calls with new signature (6 parameters)
- Updated link and chain tests with proper canonical JSON

## Component Classification

**Status: APPLICATION-LEVEL TAMPER-EVIDENT**

- ✓ Modified event_payload changes computed hash
- ✓ Chain verification detects hash mismatches
- ✓ Broken links detected by previous_hash mismatch
- ✓ First event in chain has empty previous_hash

**Not Tamper-Proof (requires additional measures):**
- No cryptographic signing (attacker could recompute all hashes)
- No HSM/PKI integration
- No timestamp authority validation
- No external audit log immutability

For tamper-proof: Implement digital signatures (HMAC/RSA) or store hash commitments in immutable log.

## Files Changed

1. `src/audit_chain_service.py` — Core hash logic + validation
2. `src/approval_workflow.py` — Removed duplicate hash logic
3. `tests/test_audit_chain_service.py` — Added tampering tests + updated existing
4. `scripts/test_audit_chain_tampering.py` — NEW demonstration script

## Backwards Compatibility

⚠ **Breaking Change:** Hash computation signature changed
- Old: `compute_event_hash(event_id, timestamp, previous_hash)`
- New: `compute_event_hash(event_id, correlation_id, action_type, event_payload, timestamp, previous_hash)`

All existing BusinessAuditEvent records have 16-character hashes (outdated).
For production migration, recompute hashes for all events using new method.

## Step 7A Status

✓ Contract 1: Canonical event hashing (includes all fields)
✓ Contract 2: Full 64-char SHA-256 hex digest
✓ Contract 3: Chain verification reconstructs canonical content
✓ Contract 4: Negative tests prove tampering detection
✓ Contract 5: Sensitive data validation enforced
✓ Contract 6: approval_workflow.py uses corrected hash (no truncation)
✓ Contract 7: Realistic demonstration with 3+ events, tampering, restoration
✓ Contract 9: Proof that payload tampering changes/invalidates hash

**Ready for Step 7B Review**
