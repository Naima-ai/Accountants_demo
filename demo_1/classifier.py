"""
classifier.py

Classifies a normalized document (the dict produced by
IngestedDocument.to_classifier_input() in ingestion.py) into an
accounting document type, with a confidence score.

Two-stage approach:
  1. Heuristic fast-path — free, instant, 100% reliable for structural
     cases (XML e-invoices, CSV ledgers/bank exports). No model call.
  2. Local SLM fallback — for PDFs/images/TXT where the type isn't
     structurally obvious, ask a local Ollama model for a JSON verdict.

Usage:
    from classifier import DocumentClassifier
    from ollama_client import warm_up

    warm_up()  # once, before real work — see ollama_client.py
    clf = DocumentClassifier()
    result = clf.classify(doc.to_classifier_input())
    # result.document_type, result.confidence, result.needs_review
"""

import logging
import re
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel

from ollama_client import OLLAMA_HOST, OLLAMA_MODEL, call_ollama, parse_json_object

logger = logging.getLogger("classifier")
logging.basicConfig(level=logging.INFO)

# Below this confidence, flag for human review regardless of source.
CONFIDENCE_REVIEW_THRESHOLD = 0.6


class DocumentType(str, Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    BANK_STATEMENT = "bank_statement"
    FINANCIAL_STATEMENT = "financial_statement"
    PAYROLL = "payroll"
    CHART_OF_ACCOUNTS = "chart_of_accounts"
    OTHER = "other"
    UNKNOWN = "unknown"


class ClassificationResult(BaseModel):
    doc_id: str
    document_type: DocumentType
    confidence: float  # 0.0-1.0
    method: str  # "heuristic" | "model" | "fallback"
    reasoning: Optional[str] = None
    needs_review: bool = False


class DocumentClassifier:
    """Classifies to_classifier_input() payloads into a DocumentType."""

    def __init__(self, ollama_host: str = OLLAMA_HOST, ollama_model: str = OLLAMA_MODEL):
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def classify(self, classifier_input: Dict[str, Any]) -> ClassificationResult:
        doc_id = classifier_input.get("doc_id", "unknown")

        heuristic_result = self._try_heuristic(classifier_input)
        if heuristic_result is not None:
            doc_type, confidence, reasoning = heuristic_result
            return ClassificationResult(
                doc_id=doc_id,
                document_type=doc_type,
                confidence=confidence,
                method="heuristic",
                reasoning=reasoning,
                needs_review=confidence < CONFIDENCE_REVIEW_THRESHOLD
                or classifier_input.get("needs_review", False),
            )

        text = (classifier_input.get("text") or "").strip()
        if len(text) < 10:
            return ClassificationResult(
                doc_id=doc_id,
                document_type=DocumentType.UNKNOWN,
                confidence=0.0,
                method="fallback",
                reasoning="Extracted text is empty or near-empty -- likely an ingestion failure upstream. "
                          "Skipped model classification rather than guess from nothing.",
                needs_review=True,
            )

        # No confident heuristic — fall back to the local SLM.
        try:
            doc_type, confidence, reasoning = self._classify_with_model(classifier_input)
            return ClassificationResult(
                doc_id=doc_id,
                document_type=doc_type,
                confidence=confidence,
                method="model",
                reasoning=reasoning,
                needs_review=confidence < CONFIDENCE_REVIEW_THRESHOLD
                or classifier_input.get("needs_review", False),
            )
        except Exception as e:
            logger.warning(f"[{doc_id}] Model classification failed: {e}")
            return ClassificationResult(
                doc_id=doc_id,
                document_type=DocumentType.UNKNOWN,
                confidence=0.0,
                method="fallback",
                reasoning=f"Model call failed: {e}",
                needs_review=True,
            )

    # ------------------------------------------------------------------
    # Stage 1: heuristics (structural, free, no model call)
    # ------------------------------------------------------------------

    def _try_heuristic(self, ci: Dict[str, Any]):
        """
        Returns (DocumentType, confidence, reasoning) if a heuristic can
        decide confidently, else None (falls through to the SLM).
        """
        file_type = ci.get("file_type", "")
        metadata = ci.get("metadata") or {}
        text = (ci.get("text") or "").lower()

        # XML: ingestion.py's XMLIngestor is FatturaPA-flavored in this
        # treat XML as invoice with high confidence.
        if file_type == "xml":
            return (
                DocumentType.INVOICE,
                0.95,
                "XML source — treated as e-invoice (FatturaPA-style) by construction.",
            )

        # CSV: distinguish bank statement vs. generic ledger/other by column names.
        if file_type == "csv":
            columns = [str(c).lower() for c in metadata.get("columns", [])]
            bank_signals = {"iban", "balance", "saldo", "transaction", "debit", "credit"}
            if any(any(sig in col for sig in bank_signals) for col in columns):
                return (
                    DocumentType.BANK_STATEMENT,
                    0.85,
                    f"CSV columns matched bank-statement signals: {columns}",
                )
        
            return None

        # Chart-of-accounts text often self-identifies structurally
        # (short, dense, category-code-like rows) 
        if file_type in ("txt", "csv") and re.search(r"\bpiano dei conti\b|\bchart of accounts\b", text):
            return (DocumentType.CHART_OF_ACCOUNTS, 0.9, "Text explicitly references chart of accounts.")

        # PDF, image, or anything else structurally ambiguous -> model.
        return None

    # ------------------------------------------------------------------
    # Stage 2: local SLM via Ollama (shared client in ollama_client.py)
    # ------------------------------------------------------------------

    def _classify_with_model(self, ci: Dict[str, Any]):
        text = (ci.get("text") or "").strip()
        text_excerpt = text[:3000]  # keep prompt small/fast on a local model

        prompt = self._build_prompt(text_excerpt)
        raw = call_ollama(prompt, model=self.ollama_model, host=self.ollama_host, num_predict=150)
        parsed = parse_json_object(raw)

        doc_type_str = parsed.get("document_type", "unknown")
        try:
            doc_type = DocumentType(doc_type_str)
        except ValueError:
            doc_type = DocumentType.UNKNOWN

        raw_confidence = parsed.get("confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else 0.0
        confidence = max(0.0, min(1.0, confidence))
        reasoning = parsed.get("reasoning")

        return doc_type, confidence, reasoning

    def _build_prompt(self, text_excerpt: str) -> str:
        valid_types = ", ".join(t.value for t in DocumentType if t != DocumentType.UNKNOWN)
        return f"""You are an accounting document classifier for an Italian accounting firm.

Classify the document below into exactly one of these types:
{valid_types}

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"document_type": "<one of the types above>", "confidence": <0.0-1.0>, "reasoning": "<one short sentence>"}}

Examples:
- A supplier bill with VAT, invoice number, line items -> "invoice"
- A shop/restaurant till slip or small purchase receipt -> "receipt"
- A list of bank transactions with dates, amounts, balances -> "bank_statement"
- A balance sheet / income statement / P&L -> "financial_statement"
- A payslip with employee, gross/net pay, deductions -> "payroll"
- A categorized list of account codes -> "chart_of_accounts"
- Anything else accounting-related but not matching the above -> "other"

Document text:
---
{text_excerpt}
---

JSON:"""


# ----------------------------------------------------------------------
# Quick manual test — exercises the heuristic path with no model call,
# and (if Ollama is running locally) the model path on a fake PDF-derived
# text. Run: python classifier.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from ollama_client import warm_up

    clf = DocumentClassifier()

    # Heuristic path: XML
    xml_input = {"doc_id": "test-xml-1", "file_type": "xml", "text": "...", "metadata": {}}
    print(clf.classify(xml_input))

    # Heuristic path: CSV with bank columns
    csv_input = {
        "doc_id": "test-csv-1",
        "file_type": "csv",
        "text": "...",
        "metadata": {"columns": ["Date", "Description", "IBAN", "Debit", "Credit", "Balance"]},
    }
    print(clf.classify(csv_input))

    # Model path: a PDF-derived text excerpt (requires Ollama running locally)
    warm_up()
    pdf_input = {
        "doc_id": "test-pdf-1",
        "file_type": "pdf_text",
        "text": (
            "RISTORANTE DA MARIO\nVia Roma 12, Milano\n"
            "Scontrino n. 00234\nData: 12/08/2026\n"
            "2x Pizza Margherita   16.00\n1x Acqua  2.00\nTotale: 18.00 EUR"
        ),
        "metadata": {},
    }
    print(clf.classify(pdf_input))