# Workflow

How an invoice moves from document to ledger, and what can stop it.

## State machine

```text
                ┌────────────────────────────────────────────────────────────┐
                │                                                            ▼
RECEIVED ─► EXTRACTED ─► CLASSIFIED ─► MATCHED ─► EXCEPTION ─► PENDING_APPROVAL ─► APPROVED ─► VALIDATED ─► POSTED
                │            │   │        │  │                        │               │            │
                │            │   └────────┼──┴─► (auto-approve) ──────┼──────────────►│            │
                └─(vendor    └─(non-PO)───┘                           │               │            │
                  ambiguous)                                          ▼               ▼            ▼
                                                                   REJECTED ◄─────────┴────────────┘
```

Allowed transitions (`app/domain/workflow.py`):

| From | To |
|---|---|
| RECEIVED | EXTRACTED, REJECTED |
| EXTRACTED | CLASSIFIED, EXCEPTION (vendor not identified, processing halts), REJECTED |
| CLASSIFIED | MATCHED, EXCEPTION, PENDING_APPROVAL (non-PO), APPROVED (non-PO auto-approval), REJECTED |
| MATCHED | EXCEPTION, PENDING_APPROVAL, APPROVED (auto-approval), REJECTED |
| EXCEPTION | PENDING_APPROVAL, REJECTED |
| PENDING_APPROVAL | APPROVED, REJECTED |
| APPROVED | VALIDATED, REJECTED |
| VALIDATED | POSTED, REJECTED |
| POSTED | — (terminal) |
| REJECTED | — (terminal) |

Anything else raises `InvalidTransitionError`. Invoices don't have to visit every state. Non-PO invoices skip `MATCHED`, and clean invoices skip `EXCEPTION` and `PENDING_APPROVAL`.

## Processing pipeline (`APWorkflowService.process_document`)

| # | Step | Who | Output | Can raise |
|---|---|---|---|---|
| 1 | Receive | SYSTEM | invoice id, audit event | — |
| 2 | Extract | **AI** | `InvoiceExtraction` (schema-validated) | extraction failure (no case created) |
| 3 | Identify vendor | SYSTEM (+ AI suggestion) | `VendorMatch` | `VENDOR_AMBIGUITY` (halts) |
| 4 | Arithmetic check | SYSTEM | lines × price, Σ lines = subtotal, subtotal + tax = total | `INVOICE_ARITHMETIC_ERROR` |
| 5 | Classify | **AI** | `ClassificationProposal` (GL, dept, location, confidence, reason) | — |
| 6 | Validate coding | SYSTEM | account exists / active / codable; dimensions valid | `INVALID_ACCOUNT`, `INVALID_DIMENSION` |
| 7 | Confidence band | SYSTEM | straight-through / review / exception | `LOW_CODING_CONFIDENCE` |
| 8 | PO match | SYSTEM | `MatchResult` (per-line ordered / received / invoiced / variance) | `PRICE_VARIANCE`, `QUANTITY_VARIANCE`, `MISSING_RECEIPT`, `VENDOR_MISMATCH`, `MISSING_PO` |
| 9 | Duplicate check | SYSTEM | `DuplicateCandidate[]` | `DUPLICATE_INVOICE` |
| 10 | Explain exceptions | **AI** | ≤ 2 sentences per exception (display only) | — |
| 11 | Route approvals | SYSTEM | `ApprovalRoute` (steps + rationale) | — |
| 12 | Propose JE | SYSTEM | `JournalEntry` (PROPOSED) | — |
| 13a | Auto-approve + validate | SYSTEM | APPROVED → VALIDATED | validation failure (posting blocked) |
| 13b | …or wait for humans | — | PENDING_APPROVAL | — |

## Human actions

| Action | Who may do it | Rules |
|---|---|---|
| **Approve** | Holder of the *next* step's role | Invoice must be PENDING_APPROVAL. No open blocking exceptions. Resolves the non-blocking exceptions that step owns. When the last step is approved: APPROVED, JE regenerated, validated. |
| **Reject** | Next step's role, or AP / Controller | Reason required. Any non-terminal state. Closes all open exceptions. |
| **Correct coding** | AP Review Queue or Controller | Only in PENDING_APPROVAL or APPROVED. New coding must pass the same master-data checks as AI output. Resolves coding exceptions. Keeps the original AI proposal. |
| **Dismiss duplicate** | AP Exception Queue | Duplicate exceptions only. Documented reason required. |
| **Post** | AP Specialist or Controller (`can_post`) | Invoice must be VALIDATED. All 8 validation rules re-run immediately before the ledger write. |

## Example: price exception

```text
RECEIVED            SYSTEM  Invoice received
EXTRACTED           AI      Invoice extracted (INV-1004, $540.00, 98%)
                    SYSTEM  Vendor identified (EXACT_VENDOR_ID → V001)
CLASSIFIED          AI      GL account proposed (6100 / Finance / New York, 97%)
MATCHED             SYSTEM  PO matching completed (PO-5003, variance $40.00, tolerance $25.00)
                    SYSTEM  Price Variance detected → Procurement Manager
                    SYSTEM  Duplicate check completed (0)
EXCEPTION           SYSTEM  Journal entry proposed (JE-000003)
PENDING_APPROVAL    SYSTEM  Approval routed to Procurement Manager
                    HUMAN   Approved as Procurement Manager (Priya Shah)
APPROVED            SYSTEM  Invoice approved — all approvals complete
VALIDATED           SYSTEM  Journal entry validated
                    HUMAN   Posting requested (Dana Brooks)
POSTED              SYSTEM  Journal entry posted (JE-000003)
```

## Audit trail

Every step above writes an `AuditEvent(invoice_id, timestamp, actor_type, actor, action, details)`:

- `actor_type` is `SYSTEM`, `AI` or `HUMAN`.
- `details` holds concise structured evidence (amounts, accounts, confidence, reasons, failed checks).
- The log is append-only, and it never contains prompts, raw LLM responses or chain-of-thought.
