"""
validator.py

Validates ExtractedFields (from extractor.py) two ways:
  1. Business rules — required fields present, Italian VAT number
     format, VAT rate legality, and subtotal + VAT = total reconciliation.
  2. Chart of Accounts categorization — maps each line item to a
     category in chart_of_accounts.json (data_set/), so downstream
     (accounting_agent.py) has a real account to book against.

Categorization is two-stage:
  1. Keyword heuristic — free, instant, matches the COA's keyword lists.
  2. Local SLM fallback — for anything no keyword matches, ask Ollama to
     pick from the allowed COA code list ONLY.

Usage:
    from src.validation.validator import Validator

    result = Validator().validate(extracted_fields)
    # result.is_valid, result.issues, result.line_item_categorizations
"""

import json
import logging
import os
import re
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/validation/validator.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.llm.ollama_client import OLLAMA_HOST, OLLAMA_MODEL, call_ollama, parse_json_object

logger = logging.getLogger("validator")
logging.basicConfig(level=logging.INFO)

DEFAULT_COA_PATH = os.path.join(_REPO_ROOT, "data_set", "chart_of_accounts.json")

# Amount reconciliation tolerance (rounding across currencies/line items).
AMOUNT_TOLERANCE = 0.02

# Italian VAT number: optional "IT" + 11 digits.
VAT_NUMBER_RE = re.compile(r"^(IT)?\d{11}$")


class Severity(str, Enum):
    ERROR = "error"      # blocks auto-posting, always needs review
    WARNING = "warning"  # unusual but not necessarily wrong
    INFO = "info"


class ValidationIssue(BaseModel):
    field: str
    severity: Severity
    message: str


class LineItemCategorization(BaseModel):
    description: Optional[str]
    coa_code: Optional[str]
    coa_name: Optional[str]
    method: str  # "keyword" | "model" | "uncategorized"
    confidence: float


class ValidationResult(BaseModel):
    doc_id: str
    is_valid: bool  # no ERROR-severity issues
    issues: List[ValidationIssue] = []
    line_item_categorizations: List[LineItemCategorization] = []
    needs_review: bool = False
    confidence: float = 0.0


class Validator:
    def __init__(
        self,
        coa_path: str = DEFAULT_COA_PATH,
        ollama_host: str = OLLAMA_HOST,
        ollama_model: str = OLLAMA_MODEL,
    ):
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model
        if not os.path.exists(coa_path):
            raise FileNotFoundError(f"Chart of Accounts not found at: {coa_path}")

        with open(coa_path, "r", encoding="utf-8") as f:
            self.coa = json.load(f)
        self._categories = self._flatten_categories(self.coa)
        self._valid_vat_rates = set(self.coa.get("vat_rates", {}).get("valid_rates_percent", [0, 4, 5, 10, 22]))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def validate(self, fields) -> ValidationResult:
        issues: List[ValidationIssue] = []

        issues.extend(self._check_required_fields(fields))
        issues.extend(self._check_vat_number(fields))
        issues.extend(self._check_amount_reconciliation(fields))
        issues.extend(self._check_line_items_sum(fields))
        issues.extend(self._check_amount_plausibility(fields))
        issues.extend(self._check_vat_rates(fields))
        issues.extend(self._check_date_format(fields))

        categorizations = [self._categorize_line_item(li) for li in fields.line_items]

        has_error = any(i.severity == Severity.ERROR for i in issues)
        any_low_confidence_category = any(c.confidence < 0.6 for c in categorizations)
        any_model_sourced_category = any(c.method == "model" for c in categorizations)
        needs_review = (
            has_error
            or any_low_confidence_category
            or any_model_sourced_category
            or getattr(fields, "needs_review", False)
            or getattr(fields, "confidence", 1.0) < 0.6
        )

        overall_confidence = self._overall_confidence(fields, categorizations, has_error)

        return ValidationResult(
            doc_id=fields.doc_id,
            is_valid=not has_error,
            issues=issues,
            line_item_categorizations=categorizations,
            needs_review=needs_review,
            confidence=overall_confidence,
        )

    # ------------------------------------------------------------------
    # Business rule checks
    # ------------------------------------------------------------------

    PLACEHOLDER_PATTERN = re.compile(r"^<.*>$|^\[.*\]$|^n/?a$|^unknown$|^none$|^null$|^tbd$", re.IGNORECASE)

    def _check_required_fields(self, fields) -> List[ValidationIssue]:
        issues = []
        required = ["supplier_name", "document_number", "document_date", "total_amount"]
        for field_name in required:
            value = getattr(fields, field_name, None)
            if not value:
                issues.append(ValidationIssue(
                    field=field_name,
                    severity=Severity.ERROR,
                    message=f"Required field '{field_name}' is missing.",
                ))
            elif isinstance(value, str) and self.PLACEHOLDER_PATTERN.match(value.strip()):
                issues.append(ValidationIssue(
                    field=field_name,
                    severity=Severity.ERROR,
                    message=f"'{field_name}' looks like a placeholder value ('{value}'), not real extracted data. "
                            f"The model likely failed to read this field from the document.",
                ))
        return issues

    def _check_vat_number(self, fields) -> List[ValidationIssue]:
        issues = []
        vat = fields.supplier_vat
        if not vat:
            issues.append(ValidationIssue(
                field="supplier_vat",
                severity=Severity.WARNING,
                message="Supplier VAT number missing.",
            ))
        elif not VAT_NUMBER_RE.match(vat.replace(" ", "")):
            issues.append(ValidationIssue(
                field="supplier_vat",
                severity=Severity.WARNING,
                message=f"'{vat}' doesn't match the standard Italian VAT format (optional 'IT' + 11 digits). "
                        f"May be a valid foreign VAT number -- flagged for a human to confirm.",
            ))
        return issues

    def _check_amount_reconciliation(self, fields) -> List[ValidationIssue]:
        issues = []
        subtotal = self._to_float(fields.subtotal)
        vat_amount = self._to_float(fields.vat_amount)
        total = self._to_float(fields.total_amount)

        if subtotal is not None and vat_amount is not None and total is not None:
            expected_total = subtotal + vat_amount
            if abs(expected_total - total) > AMOUNT_TOLERANCE:
                issues.append(ValidationIssue(
                    field="total_amount",
                    severity=Severity.ERROR,
                    message=f"subtotal ({subtotal}) + VAT ({vat_amount}) = {expected_total:.2f}, "
                            f"but total_amount is {total}. Mismatch exceeds tolerance.",
                ))
        elif total is None:
            pass
        else:
            issues.append(ValidationIssue(
                field="total_amount",
                severity=Severity.WARNING,
                message="Could not fully reconcile subtotal + VAT against total (one or more values missing/unparseable).",
            ))
        return issues

    def _check_line_items_sum(self, fields) -> List[ValidationIssue]:
        issues = []
        subtotal = self._to_float(fields.subtotal)
        if subtotal is None or not fields.line_items:
            return issues

        line_totals = [self._to_float(li.total) for li in fields.line_items]
        if any(t is None for t in line_totals):
            return issues

        summed = sum(line_totals)
        if abs(summed - subtotal) > AMOUNT_TOLERANCE:
            issues.append(ValidationIssue(
                field="line_items",
                severity=Severity.ERROR,
                message=f"Line item totals sum to {summed:.2f}, but subtotal is {subtotal:.2f}. "
                        f"At least one line item's total was likely mis-extracted.",
            ))
        return issues

    MAX_PLAUSIBLE_AMOUNT = 1_000_000.0

    def _check_amount_plausibility(self, fields) -> List[ValidationIssue]:
        issues = []
        checks = [
            ("subtotal", fields.subtotal),
            ("vat_amount", fields.vat_amount),
            ("total_amount", fields.total_amount),
        ]
        for i, li in enumerate(fields.line_items):
            checks.append((f"line_items[{i}].unit_price", li.unit_price))
            checks.append((f"line_items[{i}].total", li.total))

        for field_name, raw_value in checks:
            value = self._to_float(raw_value)
            if value is not None and abs(value) > self.MAX_PLAUSIBLE_AMOUNT:
                issues.append(ValidationIssue(
                    field=field_name,
                    severity=Severity.ERROR,
                    message=f"'{raw_value}' is an implausible amount for a business document. "
                            f"Likely a non-monetary number misread as a price/total.",
                ))
        return issues

    def _check_vat_rates(self, fields) -> List[ValidationIssue]:
        issues = []
        for i, li in enumerate(fields.line_items):
            rate = self._to_float(li.vat_rate)
            if rate is None:
                continue
            if round(rate) not in self._valid_vat_rates:
                issues.append(ValidationIssue(
                    field=f"line_items[{i}].vat_rate",
                    severity=Severity.WARNING,
                    message=f"VAT rate {rate}% is not one of the standard Italian rates {sorted(self._valid_vat_rates)}. "
                            f"May be legitimate (e.g. reverse charge) but worth a human check.",
                ))
        return issues

    _DATE_FORMATS = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d",
        "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y"
    ]

    def _check_date_format(self, fields) -> List[ValidationIssue]:
        issues = []
        for field_name, value in [("document_date", fields.document_date), ("due_date", fields.due_date)]:
            if value is None:
                continue
            if self._parse_date(value) is None:
                issues.append(ValidationIssue(
                    field=field_name,
                    severity=Severity.ERROR,
                    message=f"'{value}' in '{field_name}' is not a recognized date -- likely a misread, not a real date.",
                ))
        return issues

    @classmethod
    def _parse_date(cls, value) -> Optional[Any]:
        from datetime import datetime
        s = str(value).strip()
        for fmt in cls._DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _to_float(value) -> Optional[float]:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        s = re.sub(r"[€$£\s]", "", s)

        has_comma = "," in s
        has_dot = "." in s

        try:
            if has_comma and has_dot:
                if s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "").replace(",", ".")
                else:
                    s = s.replace(",", "")
                return float(s)
            elif has_comma:
                parts = s.split(",")
                if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
                    return float(s.replace(",", ""))
                return float(s.replace(",", "."))
            else:
                return float(s)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Chart of Accounts categorization
    # ------------------------------------------------------------------

    def _flatten_categories(self, coa: Dict[str, Any]) -> List[Dict[str, Any]]:
        flat = []
        for section_key in ("stato_patrimoniale_attivo", "conto_economico"):
            for top in coa.get(section_key, []):
                for cat in top.get("categories", []):
                    flat.append(cat)
        return flat

    def _categorize_line_item(self, line_item) -> LineItemCategorization:
        description = (line_item.description or "").strip()
        if not description:
            return LineItemCategorization(
                description=None, coa_code=None, coa_name=None, method="uncategorized", confidence=0.0
            )

        keyword_match = self._keyword_categorize(description)
        if keyword_match:
            code, name = keyword_match
            return LineItemCategorization(
                description=description, coa_code=code, coa_name=name, method="keyword", confidence=0.9
            )

        try:
            code, name, confidence = self._model_categorize(description)
            return LineItemCategorization(
                description=description, coa_code=code, coa_name=name, method="model", confidence=confidence
            )
        except Exception as e:
            logger.warning(f"Categorization model call failed for '{description}': {e}")
            return LineItemCategorization(
                description=description, coa_code=None, coa_name=None, method="uncategorized", confidence=0.0
            )

    def _keyword_categorize(self, description: str):
        desc_lower = description.lower()
        for cat in self._categories:
            for kw in cat.get("keywords", []):
                if kw.lower() in desc_lower:
                    return cat["code"], cat["name"]
        return None

    def _model_categorize(self, description: str):
        allowed = [(c["code"], c["name"]) for c in self._categories]
        allowed_lines = "\n".join(f"- {code}: {name}" for code, name in allowed)

        prompt = f"""You are categorizing an Italian accounting line item into a Chart of Accounts.

Line item description: "{description}"

Choose the SINGLE best-fit category from this exact list (use the code exactly as written):
{allowed_lines}

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"code": "<one of the codes above, exactly>", "confidence": <0.0-1.0>}}

If nothing fits well, use {{"code": "NONE", "confidence": 0.0}}.

JSON:"""

        raw = call_ollama(prompt, model=self.ollama_model, host=self.ollama_host, num_predict=100)
        parsed = parse_json_object(raw)

        code = parsed.get("code")
        raw_confidence = parsed.get("confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else 0.0
        confidence = max(0.0, min(1.0, confidence))

        valid_codes = {c["code"] for c in self._categories}
        if code not in valid_codes:
            return None, None, 0.0

        name = next(c["name"] for c in self._categories if c["code"] == code)
        return code, name, confidence

    def _overall_confidence(self, fields, categorizations, has_error: bool) -> float:
        if has_error:
            return 0.0
        extraction_conf = getattr(fields, "confidence", 1.0)
        cat_confs = [c.confidence for c in categorizations] or [1.0]
        avg_cat_conf = sum(cat_confs) / len(cat_confs)
        return round(min(extraction_conf, avg_cat_conf), 2)


# ----------------------------------------------------------------------
# Quick manual test: chains the full pipeline on the real XML sample
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from src.ingestion.ingestion import IngestionPipeline
    from src.classifier.classifier import DocumentClassifier
    from src.extraction.extractor import FieldExtractor, ExtractedFields, LineItem

    pipeline = IngestionPipeline()
    clf = DocumentClassifier()
    extractor = FieldExtractor()
    validator = Validator()

    sample_xml = os.path.join(_REPO_ROOT, "data_set", "IT01234567890_FPR01.xml")

    print("=== Full pipeline on real XML sample ===")
    doc = pipeline.ingest_file(sample_xml)
    ci = doc.to_classifier_input()
    classification = clf.classify(ci)
    fields = extractor.extract(ci, classification.document_type)
    result = validator.validate(fields)
    print(result.model_dump_json(indent=2))

    print("\n=== Keyword categorization test (no model call needed) ===")
    manual_fields = ExtractedFields(
        doc_id="manual-test-1",
        document_type="invoice",
        supplier_name="Immobiliare Rossi Srl",
        supplier_vat="IT01234567890",
        document_number="45",
        document_date="2026-08-01",
        subtotal="1000.00",
        vat_amount="220.00",
        total_amount="1220.00",
        line_items=[
            LineItem(description="Canone di locazione ufficio - Agosto 2026", total="1000.00", vat_rate="22"),
        ],
        method="structured",
        confidence=0.95,
    )
    print(validator.validate(manual_fields).model_dump_json(indent=2))