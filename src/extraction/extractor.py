"""
extractor.py

Takes a classified document (the to_classifier_input() dict + the
DocumentType from classifier.py) and pulls out structured accounting
fields: supplier, VAT, dates, totals, line items.

Two-stage approach, mirroring classifier.py:
  1. Direct structured lookup — when ingestion.py already parsed the
     document into structured_data (currently: FatturaPA XML), read the
     fields straight out of the dict. Free, exact, no model call.
  2. Local SLM fallback — for PDFs/images/receipts with only free text,
     ask the local model to extract the same field set as JSON.

Usage:
    from src.ingestion.ingestion import IngestionPipeline
    from src.classifier.classifier import DocumentClassifier, DocumentType
    from src.extraction.extractor import FieldExtractor

    doc = IngestionPipeline().ingest_file("invoice.xml")
    ci = doc.to_classifier_input()
    classification = DocumentClassifier().classify(ci)

    fields = FieldExtractor().extract(ci, classification.document_type)
"""

import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/extraction/extractor.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.llm.slm_client import call_llm, parse_json_object, SCHEMAS

logger = logging.getLogger("extractor")
logging.basicConfig(level=logging.INFO)

CONFIDENCE_REVIEW_THRESHOLD = 0.6


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit_price: Optional[str] = None
    total: Optional[str] = None
    vat_rate: Optional[str] = None


class ExtractedFields(BaseModel):
    doc_id: str
    document_type: str
    supplier_name: Optional[str] = None
    supplier_vat: Optional[str] = None
    customer_name: Optional[str] = None
    document_number: Optional[str] = None
    document_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[str] = None
    vat_amount: Optional[str] = None
    total_amount: Optional[str] = None
    iban: Optional[str] = None
    line_items: List[LineItem] = []
    method: str = "unknown"  # "structured" | "model" | "fallback"
    confidence: float = 0.0
    needs_review: bool = False
    notes: Optional[str] = None


class FieldExtractor:
    """Extracts ExtractedFields from a classifier_input dict + document type."""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def extract(self, classifier_input: Dict[str, Any], document_type: Any) -> ExtractedFields:
        doc_id = classifier_input.get("doc_id", "unknown")
        doc_type_str = document_type.value if hasattr(document_type, "value") else str(document_type)
        structured_data = classifier_input.get("structured_data")

        # Path 1: FatturaPA XML invoice — structured_data is present and
        # shaped like a FatturaElettronica document. Direct lookup, no model.
        if structured_data and "FatturaElettronica" in structured_data:
            try:
                fields = self._extract_from_fatturapa(doc_id, doc_type_str, structured_data)
                fields.needs_review = fields.needs_review or classifier_input.get("needs_review", False)
                return fields
            except Exception as e:
                logger.warning(f"[{doc_id}] Structured FatturaPA extraction failed, falling back to model: {e}")
                # fall through to model path below

        # Path 2: no usable structured_data (PDF/image/receipt/other) -> SLM.
        # Same guard as classifier.py: never let the model extract fields
        # from empty/near-empty text -- it will invent plausible-looking
        # fake data (fake names, fake amounts) rather than say "no data".
        text = (classifier_input.get("text") or "").strip()
        if len(text) < 10:
            return ExtractedFields(
                doc_id=doc_id,
                document_type=doc_type_str,
                method="fallback",
                confidence=0.0,
                needs_review=True,
                notes="Extracted text is empty or near-empty -- likely an ingestion failure upstream. "
                      "Skipped model extraction rather than fabricate fields.",
            )

        try:
            fields = self._extract_with_model(doc_id, doc_type_str, classifier_input)
            fields.needs_review = (
                fields.confidence < CONFIDENCE_REVIEW_THRESHOLD or classifier_input.get("needs_review", False)
            )
            return fields
        except Exception as e:
            logger.warning(f"[{doc_id}] Model extraction failed: {e}")
            return ExtractedFields(
                doc_id=doc_id,
                document_type=doc_type_str,
                method="fallback",
                confidence=0.0,
                needs_review=True,
                notes=f"Extraction failed: {e}",
            )

    # ------------------------------------------------------------------
    # Path 1: direct FatturaPA XML lookup
    # ------------------------------------------------------------------

    def _extract_from_fatturapa(self, doc_id: str, doc_type_str: str, structured: Dict[str, Any]) -> ExtractedFields:
        fe = structured["FatturaElettronica"]
        header = fe.get("FatturaElettronicaHeader", {})

        bodies = self._as_list(fe.get("FatturaElettronicaBody"))
        multi_invoice_note = None
        if len(bodies) > 1:
            multi_invoice_note = (
                f"This XML bundles {len(bodies)} invoices in one file. Only the FIRST was "
                f"extracted here -- the other {len(bodies) - 1} were NOT processed and need "
                f"separate handling."
            )
            logger.warning(f"[{doc_id}] {multi_invoice_note}")
        body = bodies[0] if bodies else {}

        cedente = header.get("CedentePrestatore", {}).get("DatiAnagrafici", {})
        cessionario = header.get("CessionarioCommittente", {}).get("DatiAnagrafici", {})

        supplier_name = cedente.get("Anagrafica", {}).get("Denominazione")
        iva = cedente.get("IdFiscaleIVA", {})
        supplier_vat = f"{iva.get('IdPaese', '')}{iva.get('IdCodice', '')}" if iva else None
        customer_name = cessionario.get("Anagrafica", {}).get("Denominazione")

        doc_general = body.get("DatiGenerali", {}).get("DatiGeneraliDocumento", {})
        document_number = doc_general.get("Numero")
        document_date = doc_general.get("Data")
        currency = doc_general.get("Divisa")

        beni_servizi = body.get("DatiBeniServizi", {})
        line_items = [
            LineItem(
                description=li.get("Descrizione"),
                quantity=li.get("Quantita"),
                unit_price=li.get("PrezzoUnitario"),
                total=li.get("PrezzoTotale"),
                vat_rate=li.get("AliquotaIVA"),
            )
            for li in self._as_list(beni_servizi.get("DettaglioLinee"))
        ]

        riepilogo = self._as_list(beni_servizi.get("DatiRiepilogo"))
        subtotal = self._sum_field(riepilogo, "ImponibileImporto")
        vat_amount = self._sum_field(riepilogo, "Imposta")

        pagamento = body.get("DatiPagamento", {})
        dettaglio_pag = self._as_list(pagamento.get("DettaglioPagamento"))
        total_amount = self._sum_field(dettaglio_pag, "ImportoPagamento")
        due_date = dettaglio_pag[0].get("DataScadenzaPagamento") if dettaglio_pag else None
        iban = dettaglio_pag[0].get("IBAN") if dettaglio_pag else None

        return ExtractedFields(
            doc_id=doc_id,
            document_type=doc_type_str,
            supplier_name=supplier_name,
            supplier_vat=supplier_vat or None,
            customer_name=customer_name,
            document_number=document_number,
            document_date=document_date,
            due_date=due_date,
            currency=currency,
            subtotal=subtotal,
            vat_amount=vat_amount,
            total_amount=total_amount or subtotal,
            iban=iban,
            line_items=line_items,
            method="structured",
            confidence=0.6 if multi_invoice_note else 0.98,
            needs_review=bool(multi_invoice_note),
            notes=multi_invoice_note,
        )

    @staticmethod
    def _as_list(value: Union[Dict, List, None]) -> List[Dict]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    @staticmethod
    def _sum_field(items: List[Dict], key: str) -> Optional[str]:
        total = 0.0
        found = False
        for item in items:
            raw = item.get(key)
            if raw is None:
                continue
            try:
                total += float(raw)
                found = True
            except (TypeError, ValueError):
                continue
        return f"{total:.2f}" if found else None

    _PLACEHOLDER_RE = re.compile(r"^<.*>$|^\[.*\]$|^n/?a$|^unknown$|^none$|^null$|^tbd$", re.IGNORECASE)

    def _sanitize_value(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str) and self._PLACEHOLDER_RE.match(value.strip()):
            logger.warning(f"Discarding placeholder-shaped extracted value: {value!r}")
            return None
        return value

    # OCR (via Tesseract, on scanned/photographed documents) commonly
    # confuses a printed "IT" VAT-prefix with visually similar digits --
    # "1T00834710156" instead of "IT00834710156" is the single most
    # common case (capital I misread as digit 1). Only correct the
    # 2-character country-code prefix, and only when doing so yields
    # exactly "IT" against an otherwise well-formed 11-digit number --
    # never touch the digits themselves, since a real misread there
    # isn't something a heuristic can safely undo (that stays a job for
    # validator.py's review-queue flag).
    _VAT_PREFIX_OCR_CONFUSABLES = {"1": "I", "L": "I", "0": "O", "5": "S", "8": "B", "2": "Z"}
    _VAT_SHAPE_RE = re.compile(r"^(.{2})(\d{11})$")

    def _fix_vat_ocr_confusion(self, vat: Optional[str]) -> Optional[str]:
        if not vat:
            return vat
        s = vat.strip().upper().replace(" ", "")
        match = self._VAT_SHAPE_RE.match(s)
        if not match:
            return vat
        prefix, digits = match.groups()
        if prefix == "IT":
            return f"IT{digits}"
        fixed_prefix = "".join(self._VAT_PREFIX_OCR_CONFUSABLES.get(ch, ch) for ch in prefix)
        if fixed_prefix == "IT":
            logger.info(f"Corrected likely OCR misread in VAT prefix: {vat!r} -> IT{digits}")
            return f"IT{digits}"
        return vat

    # ------------------------------------------------------------------
    # Path 2: local SLM extraction (PDFs, images, receipts, other)
    # ------------------------------------------------------------------

    def _extract_with_model(self, doc_id: str, doc_type_str: str, ci: Dict[str, Any]) -> ExtractedFields:
        text = (ci.get("text") or "").strip()
        text_excerpt = text[:3000]

        prompt = self._build_prompt(doc_type_str, text_excerpt)
        raw = call_llm(prompt, num_predict=800, schema=SCHEMAS["extraction"])
        parsed = parse_json_object(raw)

        line_items = [
            LineItem(
                description=li.get("description"),
                quantity=li.get("quantity"),
                unit_price=li.get("unit_price"),
                total=li.get("total"),
                vat_rate=li.get("vat_rate"),
            )
            for li in parsed.get("line_items", []) or []
        ]

        raw_confidence = parsed.get("confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else 0.5
        confidence = max(0.0, min(1.0, confidence))

        return ExtractedFields(
            doc_id=doc_id,
            document_type=doc_type_str,
            supplier_name=self._sanitize_value(parsed.get("supplier_name")),
            supplier_vat=self._sanitize_value(self._fix_vat_ocr_confusion(parsed.get("supplier_vat"))),
            customer_name=self._sanitize_value(parsed.get("customer_name")),
            document_number=parsed.get("document_number"),
            document_date=parsed.get("document_date"),
            due_date=parsed.get("due_date"),
            currency=parsed.get("currency"),
            subtotal=parsed.get("subtotal"),
            vat_amount=parsed.get("vat_amount"),
            total_amount=parsed.get("total_amount"),
            iban=parsed.get("iban"),
            line_items=line_items,
            method="model",
            confidence=confidence,
        )

    def _build_prompt(self, doc_type_str: str, text_excerpt: str) -> str:
        return f"""You are an accounting field-extraction assistant for an Italian accounting firm.

The document below has already been classified as: {doc_type_str}

Read the ACTUAL TEXT and pull the real values out of it. Never write a
placeholder, a field name, or a type description as a value -- if you
cannot find a value in the text, use null.

Here is a worked example showing the exact format, with real extracted
values (not placeholders):

Example document text:
---
FORNITURA UFFICIO SRL
Via Torino 8, Torino
P.IVA: IT98765432109
Fattura n. 512
Data: 03/07/2026
Carta A4 500 fogli - 15.00
Subtotale 15.00
IVA 22% 3.30
Totale 18.30
---

Example correct JSON output for that text:
{{
  "supplier_name": "FORNITURA UFFICIO SRL",
  "supplier_vat": "IT98765432109",
  "customer_name": null,
  "document_number": "512",
  "document_date": "2026-07-03",
  "due_date": null,
  "currency": "EUR",
  "subtotal": "15.00",
  "vat_amount": "3.30",
  "total_amount": "18.30",
  "iban": null,
  "line_items": [{{"description": "Carta A4 500 fogli", "quantity": null, "unit_price": "15.00", "total": "15.00", "vat_rate": "22"}}],
  "confidence": 0.95
}}

Notice "supplier_name" became the ACTUAL company name found in the text
("FORNITURA UFFICIO SRL"), not a placeholder like "<NAME>" or the word
"supplier_name" itself. Do the same for the real document below.

Now extract from this ACTUAL document text:
---
{text_excerpt}
---

Respond with ONLY a JSON object in the exact same shape as the example above, no other text, no markdown fences.

JSON:"""


# ----------------------------------------------------------------------
# Quick manual test: structured XML path needs no model call.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from src.ingestion.ingestion import IngestionPipeline
    from src.classifier.classifier import DocumentClassifier

    pipeline = IngestionPipeline()
    clf = DocumentClassifier()
    extractor = FieldExtractor()

    sample_xml = os.path.join(_REPO_ROOT, "data_set", "IT01234567890_FPR01.xml")

    doc = pipeline.ingest_file(sample_xml)
    ci = doc.to_classifier_input()
    classification = clf.classify(ci)
    print("Classification:", classification.document_type, classification.confidence)

    fields = extractor.extract(ci, classification.document_type)
    print("\nExtracted fields:")
    print(fields.model_dump_json(indent=2))
