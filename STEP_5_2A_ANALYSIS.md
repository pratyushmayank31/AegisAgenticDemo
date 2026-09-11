# Step 5.2A: Finance Routing Input and Ground-Truth Assessment
**Read-Only Analysis Report**

Date: 2026-09-09 | Analyst: Claude Code | Scope: Invoice schema, extraction pipeline, Aegis adapter, 13 sample invoices

---

## A. Available Routing Fields from StructuredInvoice

### ✓ DIRECTLY EXTRACTED (captured by extraction agent)

**Identification**
- `invoice_number` (string) — invoice identifier
- `invoice_direction` (AP|AR|UNKNOWN) — mandatory for routing decisions
- `invoice_date` (ISO YYYY-MM-DD) — document date
- `due_date` (ISO YYYY-MM-DD) — payment due date

**Parties**
- `supplier_name` (string, optional) — for AP invoices
- `customer_name` (string, optional) — for AR invoices

**Amounts**
- `gross_amount` (float) — total invoice amount
- `subtotal` (float, optional)
- `tax_amount` (float, optional)
- `currency` (ISO 4217, optional) — e.g., USD, EUR, GBP

**References**
- `purchase_order_reference` (string, optional) — PO number
- `contract_reference` (string, optional) — contract number
- `payment_terms` (string, optional) — e.g., "Net 30"

**Shipping/Logistics**
- `vessel_name` (string, optional) — vessel identifier
- `vessel_imo` (string, optional) — IMO number
- `voyage_reference` (string, optional) — voyage identifier

**Banking & Risk Signals**
- `bank_details_present` (bool) — true if invoice contains bank details
- `bank_change_claimed` (bool) — explicitly true only if invoice states bank change

**Line Items**
- `line_items[]` (list of InvoiceLineItem)
  - `item_description` (string, optional) — line detail
  - `quantity` (float, optional)
  - `unit_price` (float, optional)
  - `line_amount` (float, optional)
  - `confidence` (0.0–1.0) — extraction confidence for this line

**Extraction Metadata**
- `document_confidence` (0.0–1.0) — overall extraction confidence
- `extraction_warnings` (list) — extracted warnings from OCR/issues
- `missing_mandatory_fields` (list) — invoice_number, invoice_date, gross_amount if absent
- `requires_human_review` (bool) — extraction agent's preliminary flag

---

### ✓ DETERMINISTICALLY DERIVED (computable from extracted fields)

**Routing Indicators (derivable from extracted fields)**
- **Is AP or AR?** → From `invoice_direction`
- **Is unknown vendor?** → DERIVED: `supplier_name` is null/empty AND `invoice_direction == "AP"`
- **Is price mismatch?** → DERIVED: `|subtotal + tax_amount - gross_amount| > 0.01` (if all three present)
- **Is missing mandatory reference?** → DERIVED: field in `missing_mandatory_fields` list
- **Is duplicate?** → NOT YET DERIVABLE — requires comparison with existing invoices in database (external lookup)
- **Is bank change?** → Direct: `bank_change_claimed == true`
- **Low extraction confidence?** → DERIVED: `document_confidence < 0.5` (or configurable threshold)
- **Amount at/above threshold?** → DERIVED: `gross_amount >= THRESHOLD_CONFIG` (requires business policy)

**Business Category (requires heuristic)**
- DERIVED from combination of:
  - `line_items[].item_description` keywords (bunker, repair, freight, software, rent, etc.)
  - `vessel_*` fields presence (shipping indicator)
  - `supplier_name` or customer domain patterns
  - Invoice context (not yet implemented deterministically)

**Target System Candidate (preliminary)**
- DERIVED from:
  - `invoice_direction` (AP vs AR)
  - Business category signals (voyage/vessel → Veson IMOS; repair → smartPAL; corp → Oracle)
  - `contract_reference` or `voyage_reference` patterns

---

### ⚠ CURRENTLY MISSING (Not in StructuredInvoice schema)

**Critical for Routing**
- **Legal entity** — which company is the invoicee? (MISSING)
- **Business category explicit** — not extracted; only inferred from line items (MISSING)
- **Vendor master status** — is supplier in approved vendor list? (MISSING — requires DB lookup)
- **Duplicate status** — is this invoice a duplicate of an earlier one? (MISSING — requires DB comparison)
- **Extraction method detail** — OCR confidence scores not uniformly available (PARTIAL)
- **Previous invoice history** — for threshold assessment across invoices (MISSING — requires DB context)

**Nice-to-Have for Routing**
- **Expense category** — SAP/Oracle GL account hints (MISSING)
- **Cost center** — for GL posting (MISSING)
- **Department/project** — internal allocation (MISSING)

---

### ❌ UNSAFE TO USE FOR ROUTING

**Reason: Never transmitted to Aegis adapter; contains sensitive PII/bank details**
- `supplier_name` (free text, may contain PII) — **NOT FORWARDED TO AEGIS**
- `customer_name` (free text, may contain PII) — **NOT FORWARDED TO AEGIS**
- `line_items[].item_description` (free text, may contain sensitive details) — **NOT FORWARDED TO AEGIS**
- `bank_details_present`, `bank_change_claimed` → FLAGS ONLY (actual bank details never extracted or forwarded)
- `payment_terms` (free text, may be sensitive) — **NOT FORWARDED TO AEGIS**
- `invoice_date` (only extracted; not forwarded to Aegis) — use for workflow context only
- `due_date` (only extracted; not forwarded to Aegis)

**Impact**: Deterministic routing rules can **reference** these fields (e.g., detect "unknown vendor" flag) but must **not expose them to Aegis**. Aegis receives only:
- Controlled enums (invoice_id, target_system, risk_flags)
- Safe numeric/scalar fields (gross_amount, currency, extraction_confidence)

---

## B. Missing-Field Gaps for Deterministic Routing

| Gap | Impact | Current Workaround | Required Before Implementation |
|-----|--------|-------------------|--------------------------------|
| **Legal entity** | Cannot route to correct subsidiary | Manual review flag | DB schema: add `legal_entity` to StructuredInvoice extraction |
| **Business category** | Impossible to deterministically select target system | Keyword heuristic on line items | Manual business policy mapping or vector similarity lookup |
| **Vendor master lookup** | Unknown-vendor detection requires DB | `supplier_name == null` as proxy | DB query for supplier in master file |
| **Duplicate detection** | Cannot identify same invoice received twice | Compare document hash only | Full-text or invoice-number + date matching in DB |
| **Extraction method (OCR vs. embedded)** | Low OCR confidence should trigger HUMAN_REVIEW | Partially available; confidence varies | Normalize OCR confidence scoring in document extractor |
| **Configurable amount threshold** | Threshold-based routing hardcoded as risk flag | Need external config service | Configuration store or environment-driven threshold |

---

## C. Exact Aegis Outbound-Data Assessment

### Outgoing Objective Title Format
```
"Finance governance request {invoice_id}"
```
- **Example**: `"Finance governance request INV-001"`
- **Generated internally** by `AegisGovernanceAdapter.submit()` (line 190)
- **Caller cannot control** — validated in tests

### Outgoing Objective Description Format
```
"Evaluate proposed route {target_system.value} for invoice {invoice_id}."
```
- **Example**: `"Evaluate proposed route VESON_IMOS for invoice INV-001."`
- **Generated internally** by `AegisGovernanceAdapter.submit()` (line 192)
- **Target system enum-controlled** — only VESON_IMOS|SMARTPAL|ORACLE_FUSION|UNDETERMINED

### Exact Metadata Keys Transmitted
```python
GovernanceProposal fields transmitted to Aegis:
- invoice_id (str, 1-64 chars, alphanumeric + _ - .)
- correlation_id (str, 1-64 chars, alphanumeric + _ - .)
- agent_id (str, 1-64 chars, alphanumeric + _ - .)
- proposed_target_system (ProposedTargetSystem enum)
- proposed_accounting_code (str, optional, GL format)
- gross_amount (Decimal, optional, ≥0)
- currency (str, optional, ISO 3-letter uppercase)
- extraction_confidence (float, 0.0–1.0)
- risk_flags (list of GovernanceRiskFlag enum)

NOT transmitted:
- title, description (generated internally)
- supplier_name, customer_name (never accepted as input)
- line_item descriptions (never accepted as input)
- bank details (only flag is transmitted)
- invoice_date (only for local decision logic)
```

### Risk Flag Enums (Controlled List)
```python
GovernanceRiskFlag values:
- DUPLICATE_INVOICE
- BANK_DETAILS_CHANGED
- UNKNOWN_VENDOR
- MISSING_REFERENCE
- LOW_CONFIDENCE
- AMOUNT_THRESHOLD
- ROUTING_CONFLICT
```

### Target System Enums (Controlled List)
```python
ProposedTargetSystem values:
- VESON_IMOS (shipping/voyage invoices)
- SMARTPAL (vessel maintenance/repair)
- ORACLE_FUSION (corporate overhead, software, legal, rent)
- UNDETERMINED (insufficient evidence)
```

### Does Proposed Target System Reach Aegis?
✓ **YES** — transmitted as `proposed_target_system` enum in `GovernanceProposal`  
✓ **YES** — Aegis uses this in its decomposition logic (expected to map to approval/compliance gates)

### Does Controlled Risk-Flag Enum Reach Aegis?
✓ **YES** — transmitted as `risk_flags` (list of GovernanceRiskFlag enum)  
✓ **Validated** — Pydantic enforces only enum members; invalid values rejected

### Are Only Low-Risk Identifiers Transmitted?
✓ **YES**  
- `invoice_id`: alphanumeric + underscore/hyphen/period only (regex validated)
- `correlation_id`: same validation
- `agent_id`: same validation
- No free-text supplier/customer names
- No free-text line descriptions
- No bank account details (only flag)

### Does Aegis Receive Enough Information to Govern the Proposed Action?
✓ **SUFFICIENT FOR GOVERNANCE GATING**  
- Aegis receives invoice_id (traceability)
- Aegis receives target system (routing decision)
- Aegis receives extraction_confidence (quality indicator)
- Aegis receives risk flags (escalation signals)
- Aegis receives amount + currency (financial context)
- Aegis does NOT receive supplier name or line details (privacy-by-design)

**Limitation**: Aegis cannot perform business-rule checks that require supplier name, business category, or line-item description. Those must be handled in the deterministic routing layer (Step 5.2B) *before* Aegis submission.

---

## D. Thirteen-Invoice Ground-Truth Matrix

### Sample Invoice Definitions (by Filename)

| # | Filename | Direction | Business Category | Scenario | Expected Target System | Risk Indicators | Evidence Fields |
|----|----------|-----------|-------------------|----------|----------------------|-----------------|-----------------|
| **01** | `01_Veson_Bunker_Clean_STP` | AP | Shipping/Fuel | Clean extraction, bunker fuel | VESON_IMOS | Low (clean doc, complete fields) | `voyage_reference` present; "bunker" in line items |
| **02** | `02_smartPAL_Spares_Scan_Handwritten` | AP | Vessel Maintenance | Handwritten/scanned, spares | smartPAL | Medium (OCR needed; low confidence) | Low `document_confidence`; OCR extraction required |
| **03** | `03_Oracle_Software_Clean_UnknownVendor` | AP | Corporate Software | Unknown vendor, clean text | ORACLE_FUSION | High (unknown vendor) | `supplier_name` absent or unrecognized in master |
| **04** | `04_Veson_Demurrage_AR_Duplicate` | AR | Shipping/Port Services | Duplicate AR invoice | VESON_IMOS | High (duplicate) | Hash/number match with earlier invoice; `invoice_direction == AR` |
| **05** | `05_Oracle_Legal_Photo_BankChange` | AP | Corporate Legal | Bank change notification | ORACLE_FUSION | Critical (bank change) | `bank_change_claimed == true`; OCR extraction needed |
| **06** | `06_smartPAL_Repair_PoorScan_PriceMismatch` | AP | Vessel Maintenance | Price mismatch, poor scan | smartPAL | High (price mismatch + low confidence) | `abs(subtotal + tax - gross) > 0.01`; low `document_confidence` |
| **07** | `07_Oracle_OfficeRent_Spanish_Clean` | AP | Corporate Overhead | Multi-language, clean | ORACLE_FUSION | Low (language processing ok) | Line items indicate "rent"; clean text |
| **08** | `08_Veson_PortAgency_Photo_MissingReference` | AP | Shipping/Port Services | Missing mandatory field | VESON_IMOS | High (missing reference) | `missing_mandatory_fields` contains reference field; OCR needed |
| **09** | `09_AR_Freight_BelowThreshold_Clean` | AR | Shipping/Freight | Below threshold, clean | UNDETERMINED* | Low (clean; amount below threshold) | `invoice_direction == AR`; `gross_amount < THRESHOLD` |
| **10** | `10_AR_CharterHire_AboveThreshold_Clean` | AR | Shipping/Charter | Above threshold, clean | UNDETERMINED* | Medium (amount at threshold) | `invoice_direction == AR`; `gross_amount > THRESHOLD` |
| **11** | `11_AR_Demurrage_AtThreshold_Clean` | AR | Shipping/Port Services | At threshold, clean | UNDETERMINED* | Medium (amount = threshold) | `invoice_direction == AR`; `gross_amount == THRESHOLD` |
| **12** | `12_AR_Corporate_Recharge_Threshold` | AR | Corporate/Recharge | Threshold boundary, clean | UNDETERMINED* | Medium (amount = threshold) | `invoice_direction == AR`; "recharge" in line items |
| **13** | `13_AR_Freight_ConfigurableThreshold_PoorScan` | AR | Shipping/Freight | Poor scan, configurable threshold | UNDETERMINED* | High (low confidence; threshold policy) | `document_confidence < 0.5`; OCR required; amount = threshold |

**\*AR Invoices (09–13)**: Target system is UNDETERMINED because AR invoices represent customer receivables (freight income, charters, recharges) — not payables to be routed to SAP/Oracle/Veson. These require separate AR business logic (not in current Aegis routing scope).

---

### Preliminary Outcome Assessment

| # | Expected Preliminary Outcome | Reason | Assumptions |
|----|------------------------------|--------|-------------|
| **01** | ROUTE_CANDIDATE (VESON_IMOS) | High confidence, complete mandatory fields, voyage context | Vessel + bunker keywords → Veson; no risk flags |
| **02** | HUMAN_REVIEW (Low Confidence) | Handwritten/scanned, needs OCR; no target system clarity | Medium extraction confidence; smartPAL likely but not certain |
| **03** | HUMAN_REVIEW (Unknown Vendor) | Supplier not recognized; cannot post without approval | `supplier_name` absent or external validation fails |
| **04** | BLOCK (Duplicate) | Same invoice_number + date previously received | DB lookup shows duplicate; `invoice_direction == AR` |
| **05** | HUMAN_REVIEW (Bank Change) | Governance gate required for bank detail changes | `bank_change_claimed == true` is explicit signal |
| **06** | HUMAN_REVIEW (Price Mismatch + Low Confidence) | Arithmetic error + OCR uncertainty | Both factors present; smartPAL assumed but not certain |
| **07** | ROUTE_CANDIDATE (ORACLE_FUSION) | Corporate overhead (rent), clean extraction, complete fields | "Rent" in line items; Oracle category; no risk flags |
| **08** | HUMAN_REVIEW (Missing Reference) | Mandatory field absent; cannot post without reference | `missing_mandatory_fields` contains required reference |
| **09** | ROUTE_CANDIDATE or HOLD (depends on policy) | Below threshold; AR direction unclear on routing | If AR = customer invoice, may not route to SAP; policy TBD |
| **10** | HUMAN_REVIEW (Amount Threshold) | Amount ≥ threshold triggers approval gate | `gross_amount >= THRESHOLD_CONFIG`; above approval limit |
| **11** | HUMAN_REVIEW (Amount Threshold) | Amount = threshold boundary; may require approval | `gross_amount == THRESHOLD_CONFIG`; at boundary |
| **12** | ROUTE_CANDIDATE or HUMAN_REVIEW | Corporate recharge, clean; depends on AR policy | If AR = approved internal recharge, ROUTE; else HOLD |
| **13** | HUMAN_REVIEW (Low Confidence + Threshold) | Poor scan + threshold amount | `document_confidence < 0.5`; OCR needed; threshold policy |

---

## E. Proposed Deterministic Routing Rules

### Preliminary Business Policy (as stated in prompt)

**Target System Selection**
```
IF invoice_direction == "AR":
  → UNDETERMINED (AR is customer receivable; separate handling required)
  
ELSE IF voyage_reference OR vessel_name OR vessel_imo:
  → VESON_IMOS (shipping/commercial activity)
  
ELSE IF line_items contain keywords ("repair", "spares", "maintenance", "overhaul"):
  → smartPAL (vessel maintenance)
  
ELSE IF line_items contain keywords ("software", "legal", "rent", "recharge", "subscription"):
  → ORACLE_FUSION (corporate overhead)
  
ELSE:
  → UNDETERMINED (insufficient evidence)
```

**Risk Flag Assignment**
```
IF invoice_number + invoice_date found in prior InvoiceCase:
  → risk_flags.append(DUPLICATE_INVOICE)
  → OUTCOME = BLOCK

IF bank_change_claimed == true:
  → risk_flags.append(BANK_DETAILS_CHANGED)
  → OUTCOME = HUMAN_REVIEW

IF (invoice_direction == "AP") AND (supplier_name == null OR supplier_name not in vendor_master):
  → risk_flags.append(UNKNOWN_VENDOR)
  → OUTCOME = HUMAN_REVIEW

IF (subtotal + tax) - gross > 0.01:
  → risk_flags.append(ROUTING_CONFLICT) [or PRICE_MISMATCH if added to enum]
  → OUTCOME = HUMAN_REVIEW

IF invoice_number OR invoice_date in missing_mandatory_fields:
  → risk_flags.append(MISSING_REFERENCE)
  → OUTCOME = HUMAN_REVIEW

IF document_confidence < 0.5:
  → risk_flags.append(LOW_CONFIDENCE)
  → OUTCOME = HUMAN_REVIEW

IF gross_amount >= AMOUNT_THRESHOLD_CONFIG:
  → risk_flags.append(AMOUNT_THRESHOLD)
  → OUTCOME = HUMAN_REVIEW
  
ELSE IF target_system == UNDETERMINED:
  → risk_flags.append(ROUTING_CONFLICT)
  → OUTCOME = HUMAN_REVIEW

ELSE IF all risk_flags.length == 0 AND target_system != UNDETERMINED:
  → OUTCOME = ROUTE_CANDIDATE
```

**Risk Precedence**
```
1. Confirmed duplicate → BLOCK (explicit rejection)
2. Bank change, unknown vendor, price mismatch, missing reference, low confidence, amount threshold, routing conflict → HUMAN_REVIEW (any one present)
3. Clear low-risk evidence + complete mandatory fields → ROUTE_CANDIDATE
4. No matching evidence → UNDETERMINED → HUMAN_REVIEW (fail-closed)
```

---

## F. Step 5.2B Implementation Boundary

### Smallest Viable Implementation Scope

**Files to Create or Modify**

```
src/
  routing_engine.py (NEW)
    - DeterministicRouter class
    - route_invoice(structured_invoice) → RoutingDecision
    - classify_business_category(structured_invoice) → str
    - detect_risk_flags(structured_invoice, db) → list[GovernanceRiskFlag]
    - propose_target_system(structured_invoice, category) → ProposedTargetSystem
    
  routing_decision.py (NEW)
    - RoutingDecision dataclass
    - fields: target_system, risk_flags, confidence, reasoning, needs_aegis
    
  routing_config.py (NEW)
    - AMOUNT_THRESHOLD_USD (configurable)
    - TARGET_SYSTEM_KEYWORDS (mapping)
    - BUSINESS_CATEGORY_PATTERNS (keywords → category)
    
tests/
  test_routing_engine.py (NEW)
    - test_routing_decision_dataclass()
    - test_detect_duplicate_invoice()
    - test_detect_unknown_vendor()
    - test_detect_price_mismatch()
    - test_detect_low_confidence()
    - test_amount_threshold_assignment()
    - test_target_system_selection()
    - test_risk_flag_precedence()
    - test_13_sample_invoices() [parametrized]
    - test_undetermined_outcome()
```

### Routing Inputs (StructuredInvoice + Context)

```python
@dataclass
class RoutingContext:
    """Context for routing decision."""
    structured_invoice: StructuredInvoice
    db: DataFlow  # For duplicate detection, vendor lookup
    config: RoutingConfig  # Thresholds, keywords, patterns
```

### Controlled Enums (Reuse Existing)

```python
# From src/aegis_governance_adapter.py
ProposedTargetSystem  # VESON_IMOS, SMARTPAL, ORACLE_FUSION, UNDETERMINED
GovernanceRiskFlag    # DUPLICATE, BANK, UNKNOWN_VENDOR, MISSING_REF, LOW_CONF, AMOUNT, CONFLICT
```

### Reason Codes (New, Optional)

```python
@enum.Enum
class RoutingReason(str):
    ROUTE_CANDIDATE = "ROUTE_CANDIDATE"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCK = "BLOCK"
    UNDETERMINED = "UNDETERMINED"
```

### Tests

- **Unit tests**: Each risk detection function (duplicate, unknown vendor, price mismatch, etc.) tested independently
- **Integration tests**: All 13 sample invoices routed end-to-end (mock DB, no Aegis call)
- **Parametrized tests**: One test function, 13 fixtures (one per sample invoice)
- **Policy tests**: Risk precedence enforced (duplicate always → BLOCK; any other risk → HUMAN_REVIEW, etc.)

### No Persistence Yet

- Routing decision created in memory only
- Not stored in InvoiceCase.target_system yet
- Returned as RoutingDecision dict for caller to inspect
- Step 5.3 will add submission to Aegis and persistence

---

## G. Assumptions Requiring Business Confirmation

| # | Assumption | Impact | Resolution Required |
|----|-----------|--------|----------------------|
| **1** | AR invoices (customer receivable) do NOT route to SAP/Oracle/Veson | Samples 09–13 expected to yield UNDETERMINED | Confirm: Are AR invoices in-scope for Step 5.2B routing? If yes, define AR business logic (separate systems?). If no, filter them out pre-routing. |
| **2** | "Unknown vendor" = supplier_name is null OR supplier not in vendor_master table | Sample 03 expected to trigger HUMAN_REVIEW | Confirm: How is vendor master populated? Via CSV import? Manual entry? Real-time lookup against SAP/Oracle? |
| **3** | Price mismatch threshold is `abs(subtotal + tax - gross) > 0.01` | Sample 06 expected to trigger HUMAN_REVIEW | Confirm: Should threshold be configurable? Should it account for currency rounding (e.g., JPY has no decimals)? |
| **4** | Amount threshold is a single hard number (e.g., $10,000 USD) | Samples 10–13 expected to trigger HUMAN_REVIEW if ≥ threshold | Confirm: Threshold amount? Is it per-currency or converted to base? Does it vary by business category (freight vs. software)? |
| **5** | Duplicate detection is based on (invoice_number + invoice_date) match in InvoiceCase table | Sample 04 expected to BLOCK | Confirm: Should we also check document hash? What if invoice_number is duplicated but date differs (different fiscal period)? |
| **6** | Low confidence threshold is 0.5 (50%) | Samples 02, 05, 06, 13 expected to trigger HUMAN_REVIEW | Confirm: Should this be configurable? Different thresholds per extraction method (OCR vs. embedded text)? |
| **7** | Bank change is ALWAYS a governance gate (not auto-routed) | Sample 05 expected to HUMAN_REVIEW | Confirm: No exception for known, pre-approved banks? Always escalate? |
| **8** | Business category detection is heuristic (keyword matching in line items) | Samples 01–08 rely on keywords | Confirm: Is keyword matching sufficient, or should we use ML/embedding similarity? Should we require explicit category from extraction agent? |
| **9** | Routing conflict (UNDETERMINED target system) → HUMAN_REVIEW, never BLOCK | Applies to AR invoices and unclassifiable AP | Confirm: Is this correct? Should UNDETERMINED ever auto-route to a default (e.g., ORACLE_FUSION as catch-all)? |
| **10** | All risk flags are independent; any risk flag → HUMAN_REVIEW (except duplicate which → BLOCK) | Fundamental rule | Confirm: Any case where multiple risk flags should change outcome (e.g., AMOUNT_THRESHOLD + UNKNOWN_VENDOR = escalation to CFO instead of standard approval)? |

---

## Summary: Readiness Assessment

### ✓ Schema Is Ready for Step 5.2B
- All 16 StructuredInvoice fields needed for routing are captured
- Aegis adapter correctly enforces privacy (no PII transmission)
- Risk flag enums and target system enums are controlled and validated
- Sample invoices are diverse and cover major scenarios

### ⚠ Gaps to Address Before Step 5.2B
- **Legal entity**: Not extracted; required for multi-entity routing
- **Vendor master lookup**: Requires external data source
- **Duplicate detection**: Requires database state (InvoiceCase history)
- **Business category**: Requires heuristic or ML model
- **Configuration**: Amount threshold, keywords, patterns need externalization

### Next Steps
1. **Confirm assumptions G1–G10** with business stakeholders
2. **Design vendor master integration** (if not using supplier_name)
3. **Define amount thresholds** per currency/category
4. **Create routing_config.py** with business policy constants
5. **Implement Step 5.2B** (DeterministicRouter class + tests)

---

**End of Report**
