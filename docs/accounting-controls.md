# Accounting Controls

Every control in this document is deterministic application code with unit tests. None of them depends on model output being correct.

## 1. Monetary precision

| Control | Implementation |
|---|---|
| No floats for money | `money()` raises `TypeError` on floats; all amounts are `Decimal` |
| Data files | `json.load(..., parse_float=Decimal)` |
| LLM output | JSON numbers are converted `float → str → Decimal` (`LLMDecimal`), so `0.1 + 0.2 == 0.3` holds |
| Storage | `Money` column type stores TEXT in SQLite |
| Rounding | `ROUND_HALF_UP` to cents |

## 2. Invoice integrity (extraction check)

After AI extraction, the system recomputes:

- each line: `quantity × unit_price = amount`
- `Σ line amounts = subtotal`
- `subtotal + tax = total`

Any mismatch raises `INVOICE_ARITHMETIC_ERROR`, which is blocking. The request goes back to the vendor for a corrected invoice.

## 3. Vendor controls

1. Exact vendor ID (the tax ID printed on the invoice)
2. Exact normalized name (lowercase, punctuation and legal suffixes removed)
3. Fuzzy match. Accepted only if ≥ 88% similar **and** at least 10 points ahead of the runner-up.

If there are multiple candidates, `VENDOR_AMBIGUITY` is raised (blocking). The AI may recommend one, but the recommendation must be one of the deterministic candidates and is never auto-accepted. The demo includes duplicate vendor-master records ("Metro Supply Co." / "Metro Supply Company") to show why.

## 4. PO matching

| | Two-way | Three-way |
|---|---|---|
| Used for | services (`match_type = TWO_WAY`) | goods (`THREE_WAY`) |
| Vendor | invoice vendor = PO vendor | same |
| Quantity | invoiced ≤ ordered + tolerance | also invoiced ≤ **received** + tolerance |
| Price | Σ \|invoice price − PO price\| × qty ≤ tolerance | same |
| Receipt | — | must exist, else `MISSING_RECEIPT` |

Line pairing uses description similarity (character-sequence or token overlap, order-insensitive). Every figure is Decimal arithmetic.

### Tolerance rule

```text
effective tolerance = min( $25.00 , 5% × PO-priced amount )
```

| Case | PO | Invoice | Variance | Tolerance | Result |
|---|---|---|---|---|---|
| Scenario 2 | 40 × $12.50 = $500 | 40 × $12.75 = $510 | $10 | $25 | ✅ match |
| Scenario 3 | 20 × $25.00 = $500 | 20 × $27.00 = $540 | $40 | $25 | ❌ `PRICE_VARIANCE` |
| Small order | 8 × $25.00 = $200 | 8 × $26.50 = $212 | $12 | $10 (5%) | ❌ `PRICE_VARIANCE` |

Quantity tolerance defaults to 0 units. All tolerances are configurable (`AP_PRICE_TOLERANCE_AMOUNT`, `AP_PRICE_TOLERANCE_PERCENT`, `AP_QUANTITY_TOLERANCE`).

## 5. Duplicate detection

Each new invoice is compared against every non-rejected invoice from the same vendor.

| Confidence | Rule | Severity | Blocks approval? |
|---|---|---|---|
| HIGH | Same vendor + same invoice number (normalized) | HIGH | Yes: reject, or dismiss with a documented reason |
| MEDIUM | Same vendor + same amount + similar number (contains / ≥ 90% similar) or same date | MEDIUM | No: AP Exception Queue reviews |
| LOW | Same vendor + same amount within 60 days | LOW | No: AP Exception Queue reviews |

Nothing is auto-rejected. Posting also requires that no duplicate flag is open (Rule 8 below).

## 6. Coding controls

AI coding proposals (and human corrections) are validated against master data before they are applied:

- Account exists → else `INVALID_ACCOUNT`
- Account is active → else `INVALID_ACCOUNT` (demo: retired account 6800 still in vendor history)
- Account is codable for AP (active expense or asset, excluding cash)
- Department is in the department list → else `INVALID_DIMENSION`
- Location is in the location list → else `INVALID_DIMENSION` (demo: ship-to "Chicago")

Confidence bands (configurable): **≥ 90%** is eligible for straight-through processing; **75–89%** goes to human review; **< 75%** raises an exception. A low-confidence proposal is never auto-approved.

## 7. Approval authority

| Rule | Route |
|---|---|
| total < $1,000 and no exceptions and confidence ≥ 90% | auto-approval eligible |
| $1,000 ≤ total < $10,000 | Department Manager |
| total ≥ $10,000 | Controller |
| PO mismatch (price / quantity / receipt / vendor) | Procurement Manager |
| duplicate / ambiguous vendor / arithmetic | AP Exception Queue |
| low confidence / invalid coding | AP Review Queue |
| missing PO on PO-required vendor | AP Review Queue + Controller |

Permissions are role-based. A user can only approve the step for a role they hold, and only in route order. Only users with `can_post` may post.

## 8. Journal-entry construction

```text
Dr  <line GL account>         line amount     (department, location)
Dr  <largest line's account>  sales tax       (if any; non-recoverable tax is expensed)
    Cr  2000 Accounts Payable                  invoice total
```

## 9. Posting validation: 8 rules

`validate_for_posting` (`app/services/validation.py`) must pass in full, or **POSTING BLOCKED**.

| # | Rule | Fails when |
|---|---|---|
| 1 | Debits = Credits | totals differ, zero entry, negative amounts, or a line with both / neither debit and credit |
| 2 | Accounts valid | account missing or inactive |
| 3 | Dimensions valid | expense / asset line missing department or location, or any invalid value |
| 4 | Invoice total = JE amount | AP credit ≠ invoice total, or debits ≠ invoice total |
| 5 | Invoice approved | status not APPROVED / VALIDATED, or approval route incomplete |
| 6 | Not already posted | status POSTED, or ledger already has entries for this invoice |
| 7 | Period open | JE date falls in a CLOSED or undefined accounting period |
| 8 | Exceptions resolved (no duplicate) | any exception still open (including duplicate flags) |

The rules run when the final approval completes (a pass moves the invoice to `VALIDATED`; a failure raises `ACCOUNTING_VALIDATION_ERROR` and posting stays blocked). They run again inside `post()` immediately before the ledger write.

## 10. Test coverage map

| Area | Tests |
|---|---|
| Matching: exact, price variance, within tolerance, tolerance boundaries, quantity (ordered and received), missing receipt, vendor mismatch, multi-line pairing | `tests/test_matching.py` |
| Duplicates: exact, normalized, similar, same-amount, different vendor, window, rejected ignored | `tests/test_duplicates.py` |
| Approvals: low value, mid, high, boundaries, price → procurement, duplicate, low confidence, missing PO, ordering | `tests/test_approvals.py` |
| JE: balanced, multi-line + tax, Decimal | `tests/test_journal_entries.py` |
| Validation: each of the 8 rules failing in isolation | `tests/test_validation.py` |
| State machine: valid paths, invalid transitions, posting before approval, posting twice, rejected cannot post | `tests/test_workflow.py` |
| End-to-end controls with the service | `tests/test_workflow_service.py` |
