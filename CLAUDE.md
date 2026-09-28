# AI AP Automation Agent

## Claude Code Implementation Plan

## 1. Project Objective

Build a small but polished AI-powered Accounts Payable automation proof of concept that demonstrates how an AI-native AP workflow can automate invoice processing while keeping financial controls and accounting logic deterministic.

This project is primarily a portfolio and interview demonstration for Solutions Architect / AI Accounting / Finance Systems roles.

The application should demonstrate:

```text
Invoice
   ↓
Invoice Extraction
   ↓
Vendor Identification
   ↓
GL / Dimension Classification
   ↓
PO Matching
   ↓
Duplicate Detection
   ↓
Exception Detection
   ↓
Approval Routing
   ↓
Proposed Journal Entry
   ↓
Deterministic Accounting Validation
   ↓
Human Approval
   ↓
Posted / Exported
```

The project should NOT attempt to become a complete ERP or production AP platform.

The emphasis is on:

1. Accounting-domain understanding
2. AP workflow design
3. AI-agent architecture
4. Deterministic financial controls
5. Exception handling
6. Human-in-the-loop workflows
7. Clear solution architecture
8. A polished demo suitable for a job application

---

# 2. Important Architectural Principle

The system must explicitly separate probabilistic AI decisions from deterministic financial controls.

## AI may:

* Extract invoice information
* Identify vendors
* Normalize descriptions
* Suggest GL accounts
* Suggest departments/classes/locations
* Match invoice lines to PO lines
* Explain exceptions
* Propose accounting treatment
* Assign confidence scores

## Deterministic application code must control:

* Arithmetic
* Invoice totals
* PO matching calculations
* Tolerance calculations
* Duplicate detection
* Approval thresholds
* Debit/credit equality
* Account existence
* Accounting-period status
* Required fields
* Workflow permissions
* Posting eligibility
* State transitions

The system should never allow an LLM to directly post accounting entries.

Core principle:

> AI proposes and reasons. Deterministic application logic validates and controls.

This architecture should be prominently documented in the README and UI.

---

# 3. Recommended Technology Stack

Use a simple Python-first stack.

### Backend

* Python 3.12+
* FastAPI
* Pydantic
* SQLAlchemy
* SQLite
* pytest

### Frontend

Use Streamlit.

Do NOT build a separate React frontend for this MVP unless there is a compelling reason.

The goal is to demonstrate the workflow, not frontend engineering.

### AI

Create an abstraction layer around the LLM provider.

The code should support an OpenAI-compatible API interface.

Do not tightly couple the application to a single model.

Example interface:

```python
class LLMProvider(Protocol):
    def extract_invoice(self, document: str) -> InvoiceExtraction:
        ...

    def classify_invoice(self, invoice: Invoice) -> ClassificationProposal:
        ...

    def explain_exception(self, exception: ExceptionRecord) -> str:
        ...
```

The application should be able to run in deterministic/demo mode without an API key.

---

# 4. Repository Structure

Create the following structure:

```text
ai-ap-automation/
│
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── pyproject.toml
├── Makefile
│
├── app/
│   ├── __init__.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py
│   │
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── invoice.py
│   │   ├── vendor.py
│   │   ├── purchase_order.py
│   │   ├── receipt.py
│   │   ├── accounting.py
│   │   ├── approval.py
│   │   ├── exception.py
│   │   └── workflow.py
│   │
│   ├── services/
│   │   ├── extraction.py
│   │   ├── classification.py
│   │   ├── matching.py
│   │   ├── duplicate_detection.py
│   │   ├── exceptions.py
│   │   ├── approvals.py
│   │   ├── journal_entries.py
│   │   └── validation.py
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── provider.py
│   │   ├── mock_provider.py
│   │   └── openai_provider.py
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   ├── models.py
│   │   └── seed.py
│   │
│   └── config.py
│
├── data/
│   ├── vendors.json
│   ├── accounts.json
│   ├── departments.json
│   ├── purchase_orders.json
│   ├── receipts.json
│   └── invoices/
│
├── demo/
│   └── scenarios.py
│
├── streamlit_app.py
│
├── tests/
│   ├── test_invoice.py
│   ├── test_matching.py
│   ├── test_duplicates.py
│   ├── test_approvals.py
│   ├── test_journal_entries.py
│   ├── test_validation.py
│   └── test_workflow.py
│
└── docs/
    ├── architecture.md
    ├── workflow.md
    ├── accounting-controls.md
    └── demo-script.md
```

Keep the architecture modular, but do not create unnecessary abstractions.

---

# 5. Core Domain Models

Use Pydantic models for application/domain objects.

Use Decimal for all monetary values.

NEVER use float for monetary calculations.

## Vendor

Fields:

```text
id
name
normalized_name
tax_id
default_currency
default_gl_account
default_department
payment_terms
active
```

---

## Invoice

Fields:

```text
id
invoice_number
vendor_id
invoice_date
due_date
po_number
currency
subtotal
tax
total
status
lines
source
created_at
```

Invoice line:

```text
description
quantity
unit_price
amount
gl_account
department
class
location
```

---

## Purchase Order

Fields:

```text
id
po_number
vendor_id
po_date
currency
status
lines
```

PO line:

```text
description
quantity
unit_price
amount
received_quantity
gl_account
department
class
location
```

---

## Receipt

Fields:

```text
id
po_number
received_date
lines
```

Receipt line:

```text
description
quantity_received
```

---

# 6. Accounting Data

Create a small chart of accounts.

Example:

```text
1000 Cash
1100 Accounts Receivable
1200 Prepaid Expenses
1300 Inventory
2000 Accounts Payable
2100 Accrued Expenses
3000 Owner's Equity
4000 Revenue
5000 Cost of Goods Sold
6100 Office Supplies Expense
6200 Software Expense
6300 Professional Services Expense
6400 Travel Expense
6500 Utilities Expense
6600 Cloud Computing Expense
6700 Rent Expense
```

Also create:

```text
Departments:
- Finance
- Operations
- Sales
- Technology
- Human Resources
- Marketing
```

And locations:

```text
- Miami
- New York
- Dallas
```

The exact dimensions are not important.

The goal is to demonstrate dimension-aware AP coding.

---

# 7. Workflow State Machine

Implement explicit workflow states:

```text
RECEIVED
EXTRACTED
CLASSIFIED
MATCHED
EXCEPTION
PENDING_APPROVAL
APPROVED
VALIDATED
POSTED
REJECTED
```

Not every invoice needs every state.

For example:

```text
RECEIVED
→ EXTRACTED
→ CLASSIFIED
→ MATCHED
→ PENDING_APPROVAL
→ APPROVED
→ VALIDATED
→ POSTED
```

An exception might follow:

```text
RECEIVED
→ EXTRACTED
→ CLASSIFIED
→ MATCHED
→ EXCEPTION
→ PENDING_APPROVAL
→ APPROVED
→ VALIDATED
→ POSTED
```

Do not allow invalid state transitions.

---

# 8. Invoice Extraction

For the MVP, support two modes.

## Mode 1 — Demo Mode

Use predefined synthetic invoice data.

This must work without an LLM API key.

## Mode 2 — LLM Mode

Allow an LLM to extract structured invoice information.

Require structured JSON output.

Example:

```json
{
  "vendor_name": "Acme Office Supplies",
  "invoice_number": "INV-1001",
  "invoice_date": "2026-09-25",
  "po_number": "PO-5001",
  "subtotal": 500.00,
  "tax": 0.00,
  "total": 500.00,
  "lines": []
}
```

Validate the LLM response with Pydantic.

Never trust the raw LLM output.

---

# 9. Vendor Identification

Implement vendor matching.

The system should attempt:

1. Exact vendor ID
2. Exact normalized vendor name
3. Fuzzy/AI matching

For ambiguous matches:

```text
STATUS: EXCEPTION

Reason:
Multiple possible vendors identified.

Human review required.
```

The LLM may recommend a vendor, but the application must validate that the proposed vendor exists.

---

# 10. GL and Dimension Classification

Implement an AI classification service.

Input:

```text
Vendor
Invoice description
Historical coding
Available GL accounts
Available dimensions
```

Output:

```json
{
  "gl_account": "6600",
  "department": "Technology",
  "location": "Miami",
  "confidence": 0.94,
  "reason": "Cloud infrastructure expense based on vendor and invoice description."
}
```

The system should display:

```text
AI Recommendation

GL Account:
6600 - Cloud Computing Expense

Department:
Technology

Location:
Miami

Confidence:
94%

Reason:
Cloud infrastructure expense based on vendor history
and invoice description.
```

Do not automatically approve low-confidence classifications.

Suggested threshold:

```text
>= 90% → eligible for straight-through processing
75–89% → human review
< 75% → exception
```

Make these thresholds configurable.

---

# 11. PO Matching Engine

Implement deterministic PO matching.

Support:

## Two-way match

Compare:

```text
Invoice
vs
Purchase Order
```

Check:

* Vendor
* Quantity
* Unit price
* Total amount

## Three-way match

Compare:

```text
Invoice
vs
Purchase Order
vs
Receipt
```

Check:

* Vendor
* Quantity ordered
* Quantity received
* Quantity invoiced
* Unit price

---

# 12. Tolerance Rules

Create configurable tolerances.

Example:

```python
quantity_tolerance = 0
price_tolerance = Decimal("25.00")
percentage_tolerance = Decimal("5.0")
```

For example:

PO:

```text
20 × $25 = $500
```

Invoice:

```text
20 × $27 = $540
```

Variance:

```text
$40
```

Tolerance:

```text
$25
```

Result:

```text
MATCH FAILED
```

Generate a structured exception:

```json
{
  "type": "PRICE_VARIANCE",
  "severity": "MEDIUM",
  "amount": 40.00,
  "tolerance": 25.00,
  "message": "Invoice price variance exceeds configured tolerance."
}
```

---

# 13. Duplicate Detection

Implement deterministic duplicate detection.

Compare:

* Vendor
* Invoice number
* Invoice date
* Total amount
* PO number

Generate:

```text
POSSIBLE_DUPLICATE
```

Use confidence levels:

```text
Exact invoice number + vendor:
HIGH

Same vendor + same amount + similar invoice number:
MEDIUM

Same vendor + same amount:
LOW
```

Do not automatically reject low-confidence duplicates.

---

# 14. Exception Engine

Create structured exceptions.

Types:

```text
PRICE_VARIANCE
QUANTITY_VARIANCE
MISSING_PO
VENDOR_AMBIGUITY
DUPLICATE_INVOICE
LOW_CODING_CONFIDENCE
MISSING_RECEIPT
INVALID_ACCOUNT
ACCOUNTING_VALIDATION_ERROR
```

Each exception should contain:

```text
id
invoice_id
type
severity
message
evidence
status
recommended_action
created_at
```

Example:

```text
EXCEPTION

Type:
PRICE_VARIANCE

Severity:
MEDIUM

Invoice:
INV-1004

PO:
PO-5001

Invoice amount:
$540

PO amount:
$500

Variance:
$40

Allowed tolerance:
$25

Recommended action:
Route to Procurement Manager.
```

---

# 15. Approval Routing

Implement deterministic approval rules.

Initial rules:

```text
Invoice < $1,000
AND no exceptions
→ Auto-approval eligible

$1,000–$9,999
→ Department Manager

>= $10,000
→ Controller

PO mismatch
→ Procurement Manager

Duplicate candidate
→ AP Exception Queue

Low AI confidence
→ AP Review Queue
```

The system should clearly display why an approval route was selected.

Example:

```text
Approval Required

Invoice amount: $4,250
PO match: No
Exception: Missing PO

Routing:
→ Controller
→ AP Review
```

---

# 16. Proposed Journal Entry

Once an invoice is approved, generate a proposed journal entry.

Example:

```text
Invoice:
AWS September 2026

Dr 6600 Cloud Computing Expense      $4,250
    Cr 2000 Accounts Payable                  $4,250
```

Journal entry model:

```text
id
invoice_id
date
description
lines
status
created_by
created_at
```

Each line:

```text
account
department
location
debit
credit
memo
```

---

# 17. Accounting Validation

Before an entry can be posted:

### Rule 1

Total debits must equal total credits.

### Rule 2

All accounts must exist.

### Rule 3

All required dimensions must be valid.

### Rule 4

Invoice total must equal journal-entry amount.

### Rule 5

Invoice must be approved.

### Rule 6

Invoice cannot already be posted.

### Rule 7

Accounting period must be open.

If validation fails:

```text
POSTING BLOCKED
```

Do not allow the UI to bypass validation.

---

# 18. Posting

For this MVP, posting does NOT need to integrate with NetSuite or another ERP.

Create a simple internal ledger.

When posted:

```text
Invoice status:
POSTED

Journal Entry:
JE-000123

Posting date:
2026-09-25
```

Store the posting event.

Later, this component can be replaced with:

```text
NetSuite API
Oracle API
OpenSumma
Other ERP
```

This should be documented as an intentional architectural boundary.

---

# 19. Audit Trail

Every major workflow action should create an audit event.

Example:

```text
2026-09-25 10:32
SYSTEM
Invoice received

2026-09-25 10:33
AI
Invoice extracted

2026-09-25 10:33
AI
GL account proposed

2026-09-25 10:34
SYSTEM
PO matching completed

2026-09-25 10:34
SYSTEM
Price variance detected

2026-09-25 10:35
SYSTEM
Approval routed to Procurement

2026-09-25 10:38
HUMAN
Invoice approved

2026-09-25 10:38
SYSTEM
Journal entry validated

2026-09-25 10:38
SYSTEM
Journal entry posted
```

Do not store private LLM chain-of-thought.

Store only concise structured explanations/evidence.

---

# 20. Demo Dataset

Create at least 20 synthetic invoices.

Include:

### Scenario 1

Perfect PO match

### Scenario 2

Price variance within tolerance

### Scenario 3

Price variance above tolerance

### Scenario 4

Quantity variance

### Scenario 5

Three-way match failure

### Scenario 6

Non-PO invoice

### Scenario 7

Duplicate invoice

### Scenario 8

Low-confidence GL classification

### Scenario 9

High-value invoice requiring Controller approval

### Scenario 10

Invalid/missing accounting dimension

Each scenario must have deterministic expected results.

Create a scenario runner:

```bash
python -m demo.scenarios
```

Output:

```text
AP AUTOMATION DEMO

Scenario 1  Perfect Match             PASS
Scenario 2  Price Within Tolerance    PASS
Scenario 3  Price Exception            PASS
Scenario 4  Quantity Exception         PASS
Scenario 5  Three-Way Match Failure    PASS
Scenario 6  Non-PO                     PASS
Scenario 7  Duplicate                 PASS
Scenario 8  Low Confidence             PASS
Scenario 9  High Value Approval        PASS
Scenario 10 Invalid Dimension          PASS
```

---

# 21. Synthetic Benchmark

Generate 100 invoices.

Include a controlled distribution of:

```text
Straight-through invoices
PO exceptions
Quantity exceptions
Price exceptions
Non-PO invoices
Duplicates
Low-confidence classifications
Approval-required invoices
```

Calculate:

```text
Total invoices
Straight-through invoices
Exception rate
Human-review rate
Duplicate detection rate
PO-match rate
Classification accuracy
```

Do not claim these metrics represent real-world AP performance.

Label them clearly:

> Synthetic benchmark for demonstration purposes.

---

# 22. Streamlit Application

Create a professional but simple UI.

## Page 1 — Dashboard

Show:

```text
Invoices Processed
Straight-Through Rate
Exceptions
Pending Approvals
Posted Amount
```

Then:

```text
Exception Breakdown

Price Variance       4
Quantity Variance    3
Duplicate            2
Missing PO           3
Low Confidence       2
```

---

## Page 2 — Invoice Processing

Allow selecting a demo invoice.

Display:

### Invoice

Vendor
Invoice Number
Amount
PO

### AI Extraction

Fields extracted

### AI Classification

GL
Department
Location
Confidence
Reason

### PO Matching

Ordered
Received
Invoiced
Variance
Tolerance

### Exceptions

Clearly display exceptions.

### Approval

Show approval route.

### Accounting

Show proposed journal entry.

### Validation

Show:

```text
✓ Debits = Credits
✓ Accounts valid
✓ Dimensions valid
✓ Invoice approved
✓ Period open
```

Then:

```text
[Approve]
[Reject]
[Post]
```

Buttons must enforce the workflow rules.

---

# 23. Page 3 — Exception Queue

Show all exceptions.

Columns:

```text
Invoice
Vendor
Exception
Severity
Amount
Status
Recommended Action
```

Allow selecting an exception to inspect its evidence.

---

# 24. Page 4 — Audit Trail

Display chronological workflow events.

Allow filtering by invoice.

---

# 25. Page 5 — Architecture

Create a visual architecture diagram and explain:

```text
AI Layer
↓
Workflow Layer
↓
Deterministic Control Layer
↓
Accounting Layer
↓
ERP Boundary
```

Explain why AI and deterministic controls are separated.

---

# 26. Tests

Write comprehensive unit tests.

At minimum:

## Matching

* exact PO match
* price variance
* quantity variance
* tolerance
* three-way match

## Duplicate Detection

* exact duplicate
* different invoice number
* different vendor
* similar invoice

## Approval

* low-value invoice
* high-value invoice
* exception routing
* missing PO routing

## Accounting

* balanced journal entry
* unbalanced journal entry
* invalid account
* invalid dimension
* invoice/JE mismatch

## Workflow

* valid transitions
* invalid transitions
* posting before approval
* posting twice
* rejected invoice cannot post

Run:

```bash
pytest
```

All tests must pass.

---

# 27. README

The README should be written for a technical hiring manager / Solutions Architect.

Start with:

# AI AP Automation Agent

> A proof-of-concept intelligent Accounts Payable workflow demonstrating how AI can automate invoice processing while deterministic accounting controls govern matching, approvals, exceptions, and journal-entry validation.

Then show:

```text
Invoice
 ↓
AI Extraction
 ↓
AI Classification
 ↓
PO Matching
 ↓
Exception Detection
 ↓
Approval Routing
 ↓
Accounting Proposal
 ↓
Deterministic Validation
 ↓
Human Approval
 ↓
Posting
```

Explain:

### Problem

Traditional AP workflows involve repetitive invoice processing, coding, matching, exception handling, and approvals.

### Solution

An AI-enabled workflow can automate high-volume decisions while retaining deterministic controls around financial integrity and posting.

### Architectural Principle

> AI proposes and reasons. Deterministic application logic validates and controls.

### Key Features

* Invoice extraction
* Vendor identification
* AI GL classification
* Dimension classification
* Two-way PO matching
* Three-way PO matching
* Duplicate detection
* Exception management
* Approval routing
* Journal-entry generation
* Accounting validation
* Human-in-the-loop approval
* Audit trail
* Synthetic benchmark

---

# 28. Architecture Documentation

Create:

```text
docs/architecture.md
```

Explain the system using these layers:

```text
Presentation
    ↓
Workflow
    ↓
AI Services
    ↓
Deterministic Services
    ↓
Accounting Domain
    ↓
Persistence
```

Explicitly explain:

> The LLM is not the system of record.

And:

> The LLM cannot bypass accounting validation.

Also explain that the ERP integration is intentionally represented as a boundary rather than implemented.

---

# 29. Demo Script

Create:

```text
docs/demo-script.md
```

The demo should take approximately 3 minutes.

## Demo

Start with a perfect PO match.

Show:

```text
Invoice
→ extraction
→ classification
→ PO match
→ approval
→ JE
→ validation
→ posting
```

Then demonstrate a price exception.

Show:

```text
Invoice = $540
PO = $500
Tolerance = $25

Exception detected
→ Procurement approval
```

Then demonstrate a duplicate invoice.

Show:

```text
Duplicate detected
→ posting blocked
```

Finally show the architecture page.

Explain:

> The important architectural decision is that the AI is responsible for probabilistic tasks such as extraction and classification, while deterministic code controls financial integrity, approval rules, and posting.

---

# 30. Portfolio Positioning

The project should communicate these competencies:

```text
Accounting domain expertise
        +
AP process understanding
        +
ERP workflow understanding
        +
AI
        +
Software engineering
        +
Solution architecture
```

Describe it as:

> AI AP Automation Agent — Proof of Concept

The README may say:

> Inspired by modern AI-native AP automation architectures.

---

# 31. Future OpenSumma Integration

Do not implement this now.

Create a section in the README called:

## Future Architecture

Explain that the deterministic accounting components could eventually be extracted into OpenSumma:

```text
AI AP Agent
      ↓
Workflow Engine
      ↓
OpenSumma Accounting Kernel
      ↓
Ledger
```

Potential future OpenSumma integration:

```text
Invoice
→ Accounting Object
→ Proposed Accounting
→ Validation
→ Approval
→ Journal Entry
→ Immutable Ledger
```

This should remain documentation only for the MVP.

Do not add unnecessary OpenSumma dependencies.

---

# 32. Development Sequence

Implement in this order.

## Phase 1 — Foundation

Create:

* project structure
* pyproject.toml
* configuration
* database
* domain models
* seed data

Verify:

```bash
pytest
```

---

## Phase 2 — Deterministic AP Engine

Implement:

* PO matching
* tolerance calculations
* duplicate detection
* exception engine
* approval routing
* journal-entry generation
* accounting validation
* workflow state machine

Write tests immediately.

---

## Phase 3 — AI Layer

Implement:

* LLM provider abstraction
* mock provider
* invoice extraction
* GL classification
* dimension classification
* exception explanation

Ensure the application works completely without an API key.

---

## Phase 4 — Demo Data

Create:

* 20 synthetic invoices
* PO data
* receipts
* vendors
* expected results

Implement scenario runner.

---

## Phase 5 — Streamlit UI

Implement:

* dashboard
* invoice detail
* exception queue
* audit trail
* architecture page

Make the UI clean and professional.

---

## Phase 6 — Testing

Run:

```bash
pytest
```

Fix all failures.

Run:

```bash
python -m demo.scenarios
```

All scenarios must pass.

---

## Phase 7 — Benchmark

Generate 100 synthetic invoices.

Calculate demonstration metrics.

---

## Phase 8 — Documentation

Create:

```text
README.md
docs/architecture.md
docs/workflow.md
docs/accounting-controls.md
docs/demo-script.md
```

---

## Phase 9 — Final Polish

Verify:

* clean startup
* no hard-coded secrets
* .env.example exists
* deterministic demo mode works
* tests pass
* scenario runner passes
* README is polished
* screenshots can be added
* application can be demonstrated in under 5 minutes

---

# 33. Definition of Done

The project is complete when all of the following are true:

### Functional

* [ ] Invoice can move through AP workflow
* [ ] Vendor can be identified
* [ ] Invoice can be classified
* [ ] PO can be matched
* [ ] Exceptions are detected
* [ ] Duplicates are detected
* [ ] Approval route is determined
* [ ] Journal entry is generated
* [ ] Journal entry is validated
* [ ] Human approval is required where appropriate
* [ ] Posting is blocked when controls fail
* [ ] Audit trail is created

### Accounting

* [ ] Monetary values use Decimal
* [ ] Debits equal credits
* [ ] Invalid accounts are rejected
* [ ] Invalid dimensions are rejected
* [ ] Invoice amount agrees with proposed JE
* [ ] Unapproved invoices cannot post
* [ ] Duplicate invoices cannot post

### AI

* [ ] LLM abstraction exists
* [ ] Mock/demo provider exists
* [ ] Structured outputs are validated
* [ ] AI confidence is displayed
* [ ] Low-confidence decisions can be routed to humans
* [ ] LLM cannot directly post accounting entries

### Testing

* [ ] Unit tests pass
* [ ] Integration tests pass
* [ ] All 10 demo scenarios pass
* [ ] Synthetic benchmark runs

### Documentation

* [ ] README explains problem
* [ ] README explains architecture
* [ ] README explains AI vs deterministic responsibilities
* [ ] Architecture diagram exists
* [ ] Demo script exists
* [ ] Future OpenSumma integration is documented

---

# 34. Claude Code Operating Instructions

Before writing code:

1. Inspect the repository.
2. If an existing project exists, do not overwrite unrelated work.
3. Create a short implementation checklist.
4. Implement one phase at a time.
5. Run tests after each major phase.
6. Fix errors before proceeding.
7. Keep the implementation simple enough for a portfolio demo.
8. Avoid speculative infrastructure.
9. Do not introduce unnecessary microservices.
10. Do not implement authentication unless needed for the demo.
11. Do not implement real ERP integrations.
12. Do not implement OCR unless it can be done simply.
13. Do not require an LLM API key for the core demo.
14. Use deterministic mock data so the entire demonstration is reproducible.
15. Do not use floats for money.
16. Never allow the LLM to bypass deterministic accounting validation.
17. Do not expose private chain-of-thought in logs or the UI.
18. Store concise structured AI explanations instead.
19. Write tests alongside the implementation.
20. Keep the code readable enough that another engineer can understand the architecture quickly.

At the end of each phase, report:

```text
Phase completed:
Files created/modified:
Tests:
Scenario results:
Known limitations:
Next phase:
```

Do not move to the next phase if tests are failing.

---

# 35. Final Deliverable

The final repository should allow a reviewer to run:

```bash
git clone <repository>
cd ai-ap-automation

python -m venv .venv

# activate environment

pip install -e .

pytest

python -m demo.scenarios

streamlit run streamlit_app.py
```

The reviewer should be able to understand the architecture and demonstrate a complete AP workflow within five minutes.

The final project should feel like a **small, thoughtfully designed AI accounting system**, not a toy chatbot.
