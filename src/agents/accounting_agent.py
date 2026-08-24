"""
accounting_agent.py

Takes the output of extractor.py (ExtractedFields) and validator.py (ValidationResult) and produces an
actual double-entry journal entry (Italian: scrittura in Dare/Avere)

Line items that couldn't be categorized (validator.py returned no COA
code) still get booked -- to a clearly-named placeholder account --
rather than silently dropped, so the entry still balances and nothing
about the document goes missing. Anything uncategorized, or already
flagged by validator.py, forces the entry's status to "pending_review"
regardless of how confident extraction/validation looked.

Usage:
    from accounting_agent import AccountingAgent

    agent = AccountingAgent()
    entry = agent.build_journal_entry(fields, validation_result)
    # entry.lines, entry.status, entry.is_balanced
"""

import logging
import os
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger("accounting_agent")
logging.basicConfig(level=logging.INFO)

# Fixed control accounts used on every purchase invoice/receipt, not
# derived from line-item keyword matching (unlike the expense categories
# in chart_of_accounts.json, which vary per line item).
PAYABLE_ACCOUNT = {"code": "D-07", "name": "Debiti verso fornitori"}
VAT_CREDIT_ACCOUNT = {"code": "C-II-05", "name": "IVA a credito (verso Erario)"}
UNCATEGORIZED_ACCOUNT = {"code": "B-99-TBD", "name": "Costi da classificare (in attesa di revisione)"}

AMOUNT_TOLERANCE = 0.02


class JournalLine(BaseModel):
    account_code: str
    account_name: str
    debit: Optional[float] = None   # Dare
    credit: Optional[float] = None  # Avere
    description: Optional[str] = None


class JournalEntry(BaseModel):
    doc_id: str
    entry_date: Optional[str] = None
    description: str
    lines: List[JournalLine] = []
    status: str = "pending_review"  # "ready_to_post" | "pending_review"
    total_debit: float = 0.0
    total_credit: float = 0.0
    is_balanced: bool = False


class AccountingAgent:
    def build_journal_entry(self, fields, validation) -> JournalEntry:
        """
        fields: an ExtractedFields (from extractor.py)
        validation: a ValidationResult (from validator.py), whose
                    line_item_categorizations are in the SAME ORDER as
                    fields.line_items -- both are built by iterating the
                    same list, so zip() is safe here.
        """
        doc_id = fields.doc_id
        lines: List[JournalLine] = []
        forced_review = False

        # --- Debit side: one line per unique COA category actually used ---
        category_totals = {}  # coa_code -> (coa_name, summed_amount)
        for line_item, categorization in zip(fields.line_items, validation.line_item_categorizations):
            amount = self._to_float(line_item.total)
            if amount is None:
                forced_review = True
                continue

            if categorization.coa_code:
                code, name = categorization.coa_code, categorization.coa_name
            else:
                # Uncategorized -- still book it, don't drop it, but this
                # ALWAYS forces human review regardless of anything else.
                code, name = UNCATEGORIZED_ACCOUNT["code"], UNCATEGORIZED_ACCOUNT["name"]
                forced_review = True

            existing_name, existing_amount = category_totals.get(code, (name, 0.0))
            category_totals[code] = (existing_name, existing_amount + amount)

        for code, (name, amount) in category_totals.items():
            lines.append(JournalLine(
                account_code=code, account_name=name, debit=round(amount, 2),
                description=fields.supplier_name,
            ))

        # --- Debit side: recoverable VAT, if present ---
        vat_amount = self._to_float(fields.vat_amount)
        if vat_amount is not None and vat_amount > 0:
            lines.append(JournalLine(
                account_code=VAT_CREDIT_ACCOUNT["code"], account_name=VAT_CREDIT_ACCOUNT["name"],
                debit=round(vat_amount, 2), description=f"IVA su fattura {fields.document_number or ''}".strip(),
            ))

        # --- Credit side: what's owed to the supplier ---
        total_amount = self._to_float(fields.total_amount)
        if total_amount is not None:
            lines.append(JournalLine(
                account_code=PAYABLE_ACCOUNT["code"], account_name=PAYABLE_ACCOUNT["name"],
                credit=round(total_amount, 2), description=fields.supplier_name,
            ))
        else:
            forced_review = True

        total_debit = round(sum(l.debit or 0.0 for l in lines), 2)
        total_credit = round(sum(l.credit or 0.0 for l in lines), 2)
        is_balanced = abs(total_debit - total_credit) <= AMOUNT_TOLERANCE

        # Never mark an entry ready to post if: validator flagged it,
        # extraction flagged it, a line item was uncategorized/unparseable,
        # or the entry doesn't even balance -- any one of these means a
        # human needs to look at it before it touches real books.
        needs_review = (
            forced_review
            or not is_balanced
            or getattr(validation, "needs_review", True)
            or not getattr(validation, "is_valid", False)
        )

        supplier = fields.supplier_name or "Unknown supplier"
        doc_ref = fields.document_number or "no document number"
        description = f"{supplier} - {doc_ref}"

        entry = JournalEntry(
            doc_id=doc_id,
            entry_date=fields.document_date,
            description=description,
            lines=lines,
            status="pending_review" if needs_review else "ready_to_post",
            total_debit=total_debit,
            total_credit=total_credit,
            is_balanced=is_balanced,
        )
        if needs_review:
            logger.info(f"[{doc_id}] Journal entry built, status=pending_review "
                        f"(forced_review={forced_review}, is_balanced={is_balanced})")
        return entry

    @staticmethod
    def _to_float(value) -> Optional[float]:
        # Reuses the same tolerant parsing validator.py needed -- amounts
        # arriving here have already passed through extractor.py, but stay
        # defensive since this module has no control over what upstream sends.
        if value is None:
            return None
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None


# ----------------------------------------------------------------------
# Quick manual test: full pipeline (ingestion -> classify -> extract ->
# validate -> journal entry) on the real XML sample -- should produce a
# clean, balanced, ready_to_post entry with no model call needed.
# Run: python accounting_agent.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from ingestion import IngestionPipeline
    from classifier import DocumentClassifier
    from extractor import FieldExtractor
    from validator import Validator

    pipeline = IngestionPipeline()
    clf = DocumentClassifier()
    extractor = FieldExtractor()
    validator = Validator()
    agent = AccountingAgent()

    curr_dir = os.path.dirname(os.path.abspath(__file__))
    sample_xml = os.path.join(curr_dir, "..", "data_set", "IT01234567890_FPR01.xml")
    if not os.path.exists(sample_xml):
        sample_xml = os.path.join(curr_dir, "data_set", "IT01234567890_FPR01.xml")

    doc = pipeline.ingest_file(sample_xml)
    ci = doc.to_classifier_input()
    classification = clf.classify(ci)
    fields = extractor.extract(ci, classification.document_type)
    validation = validator.validate(fields)

    entry = agent.build_journal_entry(fields, validation)
    print(entry.model_dump_json(indent=2))
