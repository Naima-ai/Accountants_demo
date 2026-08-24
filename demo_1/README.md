# Demo 1 — Sovereign Doc-to-Data (Bookkeeping Pipeline)

Converts a real accounting document (invoice, receipt, e-invoice) into a
validated, bookable double-entry journal entry — entirely on-prem, no
data ever leaves the machine. Uses a local LLM via Ollama only where
deterministic logic can't do the job (reading unstructured text/photos);
everything else (math checks, XML parsing, VAT rules, double-entry
bookkeeping) is plain code.

## Pipeline

```
ingestion.py (Naima)
      |
      v
classifier.py  --> what kind of document is this?
      |
      v
extractor.py   --> pull out supplier, VAT, dates, totals, line items
      |
      v
validator.py   --> check math, VAT format, dates, categorize expenses
      |
      v
accounting_agent.py --> build a real Dare/Avere journal entry
```

Each step decides for itself whether it needs the local model:
- **XML/CSV documents never touch the model at all** — the format
  already tells you what you need to know (fast, free, 100% reliable).
- **Photos/PDFs with no fixed structure** go through the local model,
  since something has to actually *read* them first.

## Files in this folder

| File | Owner | What it does |
|---|---|---|
| `ingestion.py`, `schemas.py` | Naima | Converts any file format into a normalized document |
| `classifier.py` | Meet | Figures out the document type |
| `extractor.py` | Meet | Pulls out structured fields |
| `validator.py` | Meet | Checks correctness, categorizes expenses |
| `accounting_agent.py` | Meet | Builds the final journal entry |
| `ollama_client.py` | Meet | Shared helper for talking to the local model |
| `chart_of_accounts.json` | Meet | Synthetic Italian Chart of Accounts (built on the real Civil Code structure) |
| `test_pipeline_on_file.py` | Meet | Run the full chain on any one file |
| `accuracy_scorer.py` | Meet | Measures real accuracy against a labeled dataset (SROIE) |


## Setup

```bash
pip install -r requirements.txt
```

You also need, separately (not pip-installable):
- **[Ollama](https://ollama.com)**, running locally (`ollama serve`), with a model pulled
  (`ollama pull qwen2.5:3b-instruct` — 3B recommended for CPU-only machines;
  7B works but is noticeably slower without a GPU).
- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)** installed as a system binary
  (not just the `pytesseract` Python package). On Windows, if it's not on
  your PATH, the scripts here point directly at
  `C:\Program Files\Tesseract-OCR\tesseract.exe` — adjust if you installed
  it elsewhere.

Configuration (all optional, sensible defaults apply):
```bash
OLLAMA_MODEL=qwen2.5:3b-instruct   # which local model to use
OLLAMA_HOST=http://localhost:11434
OLLAMA_TIMEOUT_S=420               # 7B on CPU needs real headroom -- don't lower without testing
SROIE_PATH=/path/to/sroie          # only needed for accuracy_scorer.py
```

## Running it

```bash
# Test the full chain on one document
python test_pipeline_on_file.py path/to/invoice.xml
python test_pipeline_on_file.py path/to/receipt.jpg

# Measure real accuracy against labeled data
python accuracy_scorer.py
```

## Current status, honestly

**Solid:** the deterministic layer — math reconciliation, VAT format
checks, XML parsing, double-entry bookkeeping — is reliable. Every
deliberately-broken test case thrown at it so far has been caught
correctly.

**Genuinely imperfect, by nature of the task:** the AI layer (reading
photos/receipts) makes real mistakes on messy documents, especially
multi-column tables and low-quality scans — measured around 60-75%
field-level accuracy on a hard, non-Italian benchmark dataset (SROIE).
This isn't a bug to "fix" so much as an inherent limit of a small local
model — which is *why* the validation layer exists: every extraction
gets checked, and low-confidence or inconsistent results are flagged
`needs_review` rather than silently trusted.

**Known open items:**
- Not yet tested against: credit notes, non-EUR currency, withholding
  tax (ritenuta d'acconto) — common in real Italian professional-services
  invoices to Public Administration clients.
- No integration yet with `memory.py`, `api.py`, `orchestrator.py`, or
  `ui.py` (owned by other team members).

## A note for whoever builds on top of this

- **Never trust extracted data without checking `needs_review`.** A
  `False` here means the deterministic checks passed AND nothing came
  from a low-confidence model call — not that the data is guaranteed
  perfect, but that it's the system's honest best assessment.
- **`accounting_agent.py`'s output is always safe to inspect even when
  `status == "pending_review"`** — uncategorized or uncertain line items
  still get booked to a clearly-labeled placeholder account rather than
  silently dropped, so nothing about a document ever goes missing.
