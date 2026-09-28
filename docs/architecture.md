# Architecture

> **AI proposes and reasons. Deterministic application logic validates and controls.**

This document explains how the system is layered, where the AI sits, and why it is kept away from anything that changes the books.

## Layers

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Presentation          streamlit_app.py · app/api/routes.py               │
│                       Thin: every action calls the workflow service      │
├──────────────────────────────────────────────────────────────────────────┤
│ Workflow              app/services/workflow_service.py                   │
│                       State machine · orchestration · permissions ·      │
│                       human actions · audit trail                        │
├──────────────────────────────────────────────────────────────────────────┤
│ AI Services           app/ai/  (LLMProvider protocol)                    │
│                       extract_invoice · suggest_vendor ·                 │
│                       classify_invoice · explain_exception               │
│                       → returns typed, validated proposals only          │
├──────────────────────────────────────────────────────────────────────────┤
│ Deterministic         app/services/                                      │
│ Services              vendor_matching · matching (2/3-way, tolerances) ·  │
│                       duplicate_detection · exceptions (policy table) ·  │
│                       approvals · classification (master-data checks) ·  │
│                       journal_entries · validation                       │
├──────────────────────────────────────────────────────────────────────────┤
│ Accounting Domain     app/domain/  (pure Pydantic, no I/O)                │
│                       Invoice · PO · Receipt · Account · Period ·        │
│                       JournalEntry · ExceptionRecord · ApprovalRoute     │
├──────────────────────────────────────────────────────────────────────────┤
│ Persistence           app/db/  SQLite via SQLAlchemy (money as TEXT)      │
│ ERP boundary          app/services/ledger.py  LedgerGateway protocol     │
│                       → InternalLedger (MVP) | NetSuite | Oracle | …     │
└──────────────────────────────────────────────────────────────────────────┘
```

Dependencies point downward. `app/domain` imports nothing from the other layers. `app/ai` imports only domain models. The deterministic services import domain models and configuration. The workflow service is the only component that composes them all, and the only one that writes state.

## Where the AI sits and what it can touch

The `LLMProvider` protocol (`app/ai/provider.py`) has exactly four methods:

| Method | Returns | Validated by |
|---|---|---|
| `extract_invoice(document)` | `InvoiceExtraction` | Pydantic schema, then deterministic arithmetic checks (`Invoice.arithmetic_issues`) |
| `suggest_vendor(name, candidates)` | `VendorSuggestion` | Must be one of the deterministic candidates; never auto-accepted |
| `classify_invoice(invoice, context)` | `ClassificationProposal` | Pydantic schema (confidence 0..1, reason ≤ 500 chars), then `validate_coding` against the chart of accounts and dimensions |
| `explain_exception(exception)` | `str` (≤ 400 chars) | Display only; never used for decisions |

Two implementations exist:

- **`MockLLMProvider`** is the default demo mode. It is rule-based and fully deterministic: it parses the synthetic invoice layout, and it derives coding confidence from how strongly the evidence agrees (PO coding > vendor history > vendor default > keywords). This makes the demo reproducible and requires no API key.
- **`OpenAICompatibleProvider`** works with any `/chat/completions` endpoint that supports JSON mode. It uses `temperature=0` and embeds the Pydantic JSON schema in the prompt. Any non-JSON or schema-invalid response raises `AIProviderError`.

Swapping providers is configuration (`AP_LLM_PROVIDER`). Nothing downstream changes.

## The LLM is not the system of record

The system of record is the SQLite database: invoice cases, the audit log and the ledger. The LLM's contribution is stored as a *proposal*:

- `InvoiceCase.ai_classification` keeps the original AI coding, even after a human corrects it. `InvoiceCase.classification` holds the coding actually used.
- Each exception stores `evidence` (deterministic facts) separately from `explanation` (AI prose).
- Audit events record what the AI proposed (`GL account proposed`, with account, confidence and reason). They never record prompts, raw completions or chain-of-thought.

## The LLM cannot bypass accounting validation

This is enforced structurally, not by convention:

1. **No reference, no path.** The provider object is given documents and read-only context. It holds no repository, workflow service or ledger. `tests/test_workflow_service.py::test_ai_provider_has_no_path_to_ledger` fails if the provider's public surface grows.
2. **Single writer.** Only `APWorkflowService` changes invoice state. Every change goes through `assert_transition`, which enforces a whitelist.
3. **One edge into POSTED.** `VALIDATED → POSTED` is the only transition that reaches `POSTED`. `VALIDATED` is reachable only when `validate_for_posting` passes all eight rules.
4. **Validation runs twice.** It runs when approvals complete, and again inside `post()` immediately before `LedgerGateway.post`. A stale or tampered case cannot post.
5. **Humans are validated too.** `correct_coding` runs the same `validate_coding` checks as an AI proposal, so an invalid human correction is rejected.

## Human-in-the-loop design

Confidence thresholds, amount thresholds and exception types decide *who* must look at an invoice. The model does not decide this.

| Signal | Route |
|---|---|
| < $1,000, no exceptions, confidence ≥ 90% | Auto-approve, then validate (straight-through) |
| Confidence 75–89% | AP Review Queue (no exception, just review) |
| Confidence < 75% | `LOW_CODING_CONFIDENCE` exception, then AP Review Queue |
| $1,000 – $9,999.99 | Department Manager |
| ≥ $10,000 | Controller (supersedes Department Manager) |
| Price / quantity / receipt issues | Procurement Manager |
| Duplicate, ambiguous vendor, invoice does not foot | AP Exception Queue |
| Missing PO on a PO-required vendor | AP Review Queue, then Controller |

Steps are worked in a fixed order (AP triage first, financial authority last). Each step can only be approved by a user holding that role. Approving a step resolves the non-blocking exceptions that step owns. **Blocking** exceptions (HIGH-confidence duplicate, invalid account or dimension, ambiguous vendor, vendor mismatch, arithmetic error) cannot be approved away. They require a specific corrective action (correct the coding, dismiss the duplicate with a documented reason) or a rejection.

## ERP integration is a boundary, not an implementation

```python
class LedgerGateway(Protocol):
    name: str
    def post(self, je: JournalEntry, posted_by: str) -> PostingReceipt: ...
    def has_posting_for(self, invoice_id: str) -> bool: ...
```

The MVP ships `InternalLedger`, an append-only `ledger_entries` table. A real deployment would add a `NetSuiteLedger` or `OracleLedger` adapter, or an OpenSumma kernel client, that implements the same protocol. For example, a NetSuite adapter would create a Vendor Bill and return its internal ID as `PostingReceipt.reference`. Because the workflow service depends only on the protocol, the controls in front of the boundary stay the same.

This boundary is intentional. Building a real ERP connector would add credentials, sandbox accounts and API quirks without demonstrating anything new about the architecture.

## Persistence

- Master data (vendors, accounts, dimensions, periods, POs, receipts, coding history) lives in normalized tables seeded from `data/*.json`.
- Each invoice is an `InvoiceCase` aggregate. Key fields (id, number, vendor, status, total) are indexed columns, and the full aggregate (extraction, proposals, match result, exceptions, approval route, JE, validation report, state history) is a JSON payload. This keeps the PoC schema small while staying queryable.
- The audit log and ledger are separate append-only tables.
- Amounts use a `Money` TypeDecorator that stores TEXT, so SQLite never round-trips money through a binary float.

## Why these technology choices

| Choice | Reason |
|---|---|
| Python, Pydantic | Typed contracts at the AI boundary; the same models serve validation, persistence and the API |
| SQLite + SQLAlchemy | Zero-setup, reproducible demo; swapping to Postgres is a connection string |
| Streamlit | Demonstrates the workflow without building a frontend |
| FastAPI | Shows the same service exposed as an integration surface |
| No agent framework | The "agent" is a deterministic pipeline that consults an LLM at specific points. That is easier to test and audit, and it is the point of the design. |
