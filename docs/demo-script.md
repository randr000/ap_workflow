# Demo Script (~3 minutes)

**Setup (before the call):** `streamlit run streamlit_app.py`, then click **Reset demo data** in the sidebar so the state is fresh and deterministic. Keep **Your role** set to *AP Specialist* (Dana Brooks).

---

## 0:00 — Frame it (15 s)

Open **Dashboard**.

> "This is an AI-powered AP workflow. Twenty invoices have just come in. Four went straight through with no human touch; the rest are waiting on the right person, for a specific reason. The design rule is on the screen: **AI proposes and reasons; deterministic application logic validates and controls.**"

Point at the exception breakdown: price and quantity variances, duplicates, missing PO, low-confidence coding.

## 0:15 — Perfect PO match (50 s)

**Invoice Processing** → `AP-0001 · INV-1001 · Acme Office Supplies · $500.00`

Walk top to bottom:

1. **Source document → AI Extraction.** "The AI read the document into a typed, schema-validated structure."
2. **Vendor identification.** "It matched deterministically on tax ID. The AI doesn't get to guess the vendor."
3. **AI Classification.** "6100 Office Supplies, Operations, Miami, 97%. The reason: coding inherited from the PO."
4. **PO Matching.** "Three-way match: ordered 20, received 20, invoiced 20, zero variance."
5. **Approval.** "Under $1,000, no exceptions, confidence above 90%, so it's auto-approved by policy. The policy is code, not the model."
6. **Journal entry.** "Dr 6100 $500 / Cr 2000 AP $500."
7. **Validation.** "Eight checks: debits equal credits, accounts valid, dimensions valid, invoice equals JE, approved, not already posted, period open, no open exceptions."

Click **Post**.

> "Posted to the ledger as JE-000001. Posting re-ran all eight checks immediately before the write."

## 1:05 — Price exception (50 s)

Select `AP-0003 · INV-1004 · $540.00`.

> "Invoice $540, PO $500, tolerance $25 (the lesser of $25 or 5%). Variance is $40."

Show the **PRICE_VARIANCE** card: evidence, the AI's one-line explanation, and the recommended action. Show the route: **Procurement Manager**.

Click **Approve as Procurement Manager** while still signed in as the AP Specialist.

> "Blocked. Dana is an AP Specialist, not Procurement. Permissions are enforced by the service, not by hiding buttons."

Switch **Your role** to *Procurement Manager* (Priya Shah) and open **Approval Inbox**. AP-0003 is waiting there, and its route shows `▶ Procurement Manager → ○ Post`. Add a comment ("Price increase accepted"), then **Approve as Procurement Manager**.

> "Now it's approved, the exception is resolved with Priya's name on it, and the entry is validated and ready to post."

## 1:55 — Duplicate invoice (35 s)

Switch **Your role** back to *AP Specialist*, open **Invoice Processing**, and select `AP-0007 · INV-1001 (resubmitted)`.

> "Same vendor, same invoice number, same amount, same PO as the one we just posted. It's a HIGH-confidence duplicate."

Click **Post**.

> "**POSTING BLOCKED.**"

Click **Approve as AP Exception Queue**.

> "Also blocked. A high-confidence duplicate can't be approved away. It has to be rejected, or dismissed with a documented reason that goes into the audit trail."

Optionally type a reason and click **Reject**.

## 2:30 — Architecture (30 s)

Open **Architecture**.

> "The important architectural decision is that the AI is responsible for probabilistic tasks such as extraction and classification, while deterministic code controls financial integrity, approval rules, and posting."

Point at the red "no direct path" edge:

> "The model returns typed proposals only. It has no handle on the workflow or the ledger. ERP integration is an explicit boundary, so NetSuite, Oracle or an accounting kernel like OpenSumma would plug in there, behind the same controls."

Close on **Audit Trail**, filtered to `AP-0003`: SYSTEM, AI and HUMAN events in order, with structured evidence and no hidden reasoning.

---

## If there's extra time

| Show | Where | Point |
|---|---|---|
| Invalid dimension | `AP-0010` Skyway | The AI read "Chicago" from the ship-to, which isn't a valid location. Approval is blocked. Use **Correct coding** to set New York; a human entering Chicago would be rejected the same way. |
| Retired account | `AP-0018` Redwood | Vendor history points at retired account 6800. `INVALID_ACCOUNT` is raised; the fix is 6900. |
| Ambiguous vendor | `AP-0017` Metro Supply | Two vendor-master records share a name and the invoice has no tax ID. The AI's suggestion is shown but not accepted. |
| Human-review band | `AP-0020` Nimbus | History says cloud, the description says software license. 80% confidence goes to AP Review without raising an exception. |
| High value | `AP-0009` Summit Legal | $18,500 needs the Controller (*Elena Torres*). |
| Travel through roles | Approval Inbox | As *Procurement Manager*, approve `AP-0004`. Switch to *Department Manager*: it's now in that inbox with Procurement's ✅ on the route. |
| Enter an invoice | New Invoice → Enter details | Pick Acme Office Supplies and PO-5003 (lines pre-fill), change the price to 27.00, submit. A price variance goes to Procurement. |
| Local model | Sidebar → AI provider → Ollama | Point it at the Ollama machine and upload `data/samples/sample_invoice.txt`. The model extracts the invoice, and the same controls apply. |
| Benchmark | Dashboard → *Synthetic benchmark* | 100 generated invoices. Clearly labelled as synthetic. |
| CLI | `python -m demo.scenarios` | All 10 scenarios pass deterministically. |
