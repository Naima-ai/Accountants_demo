"""
memory.py

The memory/status layer sitting on top of database.py. Orchestrators
and agents should talk to MemoryStore, not to database.py directly --
this is the one place that knows how "learn a supplier pattern" or
"mark a document received" turns into rows in the DB.

Covers:
  - Demo 1: recurring-supplier learning (the auto-learn / accuracy-
    improves-per-client mechanism).
  - Demo 2: client document checklist + status tracking + reminder log
    (this module IS the "memory/status integration" deliverable).
  - Demo 3: financial statement history (for period-over-period
    comparison) + generated reports.
  - Shared: one human-review queue for all three demos.

Usage:
    from src.memory.memory import MemoryStore

    memory = MemoryStore()
    memory.learn_supplier(client_id="c-001", supplier_name="Acme Srl",
                           supplier_vat="IT12345678901", coa_code="B-07-CONS",
                           coa_name="Consulenze professionali")
    hint = memory.get_supplier_hint("c-001", "Acme Srl")
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/memory/memory.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.database.database import (
    Client, Document, JournalEntry, JournalLine, SupplierPattern,
    ExpectedDocument, ReminderLog, FinancialStatement, AnalysisReport,
    ReviewQueueItem, session_scope, init_db,
)

logger = logging.getLogger("memory")
logging.basicConfig(level=logging.INFO)


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Read/write memory + status for all three demos, backed by the shared DB."""

    def __init__(self, auto_init: bool = True):
        if auto_init:
            init_db()

    # ------------------------------------------------------------------
    # Clients (shared)
    # ------------------------------------------------------------------

    def upsert_client(
        self, client_id: str, name: str, vat_number: Optional[str] = None,
        email: Optional[str] = None, phone: Optional[str] = None,
        preferred_tone: Optional[str] = "formal",
    ) -> None:
        with session_scope() as s:
            client = s.get(Client, client_id)
            if client is None:
                client = Client(id=client_id, name=name)
                s.add(client)
            client.name = name
            client.vat_number = vat_number or client.vat_number
            client.email = email or client.email
            client.phone = phone or client.phone
            client.preferred_tone = preferred_tone or client.preferred_tone

    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        with session_scope() as s:
            client = s.get(Client, client_id)
            return self._client_to_dict(client) if client else None

    def list_clients(self) -> List[Dict[str, Any]]:
        with session_scope() as s:
            return [self._client_to_dict(c) for c in s.query(Client).all()]

    @staticmethod
    def _client_to_dict(c: Client) -> Dict[str, Any]:
        return {
            "id": c.id, "name": c.name, "vat_number": c.vat_number,
            "email": c.email, "phone": c.phone, "preferred_tone": c.preferred_tone,
        }

    # ------------------------------------------------------------------
    # Demo 1 -- document/journal status + supplier auto-learning
    # ------------------------------------------------------------------

    def record_document(
        self, doc_id: str, original_filename: str, source_path: str,
        client_id: Optional[str] = None, file_type: Optional[str] = None,
        classification: Optional[str] = None, classification_confidence: Optional[float] = None,
        status: str = "ingested", needs_review: bool = False,
        extracted_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                doc = Document(id=doc_id, original_filename=original_filename, source_path=source_path)
                s.add(doc)
            doc.client_id = client_id or doc.client_id
            doc.file_type = file_type or doc.file_type
            doc.classification = classification or doc.classification
            doc.classification_confidence = (
                classification_confidence if classification_confidence is not None else doc.classification_confidence
            )
            doc.status = status
            doc.needs_review = needs_review
            if extracted_fields is not None:
                doc.extracted_fields_json = json.dumps(extracted_fields, default=str)

    def record_journal_entry(self, doc_id: str, entry: Dict[str, Any]) -> None:
        """entry: dict shaped like accounting_agent.JournalEntry.model_dump()."""
        with session_scope() as s:
            existing = s.query(JournalEntry).filter_by(doc_id=doc_id).one_or_none()
            if existing is not None:
                s.delete(existing)
                s.flush()

            je = JournalEntry(
                doc_id=doc_id,
                entry_date=entry.get("entry_date"),
                description=entry.get("description", ""),
                status=entry.get("status", "pending_review"),
                total_debit=entry.get("total_debit", 0.0),
                total_credit=entry.get("total_credit", 0.0),
                is_balanced=entry.get("is_balanced", False),
            )
            s.add(je)
            s.flush()  # assigns je.id

            for line in entry.get("lines", []):
                s.add(JournalLine(
                    journal_entry_id=je.id,
                    account_code=line.get("account_code"),
                    account_name=line.get("account_name"),
                    debit=line.get("debit"),
                    credit=line.get("credit"),
                    description=line.get("description"),
                ))

    def learn_supplier(
        self, client_id: Optional[str], supplier_name: str,
        supplier_vat: Optional[str] = None, coa_code: Optional[str] = None,
        coa_name: Optional[str] = None,
    ) -> None:
        """
        Record (or reinforce) that this client's supplier's line items
        usually map to this COA category. Called after a document is
        successfully accounted for -- this is what makes the pipeline
        visibly more confident the 2nd+ time it sees the same supplier.
        """
        if not supplier_name:
            return
        with session_scope() as s:
            pattern = (
                s.query(SupplierPattern)
                .filter_by(client_id=client_id, supplier_name=supplier_name)
                .one_or_none()
            )
            if pattern is None:
                s.add(SupplierPattern(
                    client_id=client_id, supplier_name=supplier_name, supplier_vat=supplier_vat,
                    coa_code=coa_code, coa_name=coa_name, seen_count=1,
                ))
            else:
                pattern.seen_count += 1
                pattern.last_seen = _utcnow_str()
                # Only overwrite the remembered category once we have a
                # confident one -- don't let an uncategorized re-visit
                # erase a good prior mapping.
                if coa_code:
                    pattern.coa_code = coa_code
                    pattern.coa_name = coa_name
                pattern.supplier_vat = supplier_vat or pattern.supplier_vat

    def get_supplier_hint(self, client_id: Optional[str], supplier_name: str) -> Optional[Dict[str, Any]]:
        """
        Returns the previously-learned COA mapping for this
        (client, supplier) pair, or None if never seen before.
        Callers (the orchestrator) use this to boost extraction/
        categorization confidence on repeat suppliers.
        """
        if not supplier_name:
            return None
        with session_scope() as s:
            pattern = (
                s.query(SupplierPattern)
                .filter_by(client_id=client_id, supplier_name=supplier_name)
                .one_or_none()
            )
            if pattern is None or not pattern.coa_code:
                return None
            return {
                "coa_code": pattern.coa_code,
                "coa_name": pattern.coa_name,
                "seen_count": pattern.seen_count,
            }

    # ------------------------------------------------------------------
    # Demo 2 -- client document checklist + status + reminders
    # ------------------------------------------------------------------

    def seed_expected_documents(self, client_id: str, period: str, doc_types: List[str]) -> None:
        """Set up the checklist of documents a client owes for a period."""
        with session_scope() as s:
            for doc_type in doc_types:
                exists = (
                    s.query(ExpectedDocument)
                    .filter_by(client_id=client_id, period=period, doc_type=doc_type)
                    .one_or_none()
                )
                if exists is None:
                    s.add(ExpectedDocument(client_id=client_id, period=period, doc_type=doc_type))

    def mark_document_received(
        self, client_id: str, period: str, doc_type: str, received_doc_id: Optional[str] = None,
    ) -> bool:
        """Returns True if a matching expected-document row was found and updated."""
        with session_scope() as s:
            expected = (
                s.query(ExpectedDocument)
                .filter_by(client_id=client_id, period=period, doc_type=doc_type)
                .one_or_none()
            )
            if expected is None:
                return False
            expected.status = "received"
            expected.received_doc_id = received_doc_id
            expected.updated_at = _utcnow_str()
            return True

    def get_missing_documents(self, client_id: str, period: str) -> List[Dict[str, Any]]:
        with session_scope() as s:
            rows = (
                s.query(ExpectedDocument)
                .filter_by(client_id=client_id, period=period)
                .filter(ExpectedDocument.status != "received")
                .all()
            )
            return [self._expected_doc_to_dict(r) for r in rows]

    def get_checklist(self, client_id: str, period: str) -> List[Dict[str, Any]]:
        with session_scope() as s:
            rows = s.query(ExpectedDocument).filter_by(client_id=client_id, period=period).all()
            return [self._expected_doc_to_dict(r) for r in rows]

    @staticmethod
    def _expected_doc_to_dict(r: ExpectedDocument) -> Dict[str, Any]:
        return {
            "id": r.id, "client_id": r.client_id, "period": r.period, "doc_type": r.doc_type,
            "status": r.status, "received_doc_id": r.received_doc_id, "due_date": r.due_date,
        }

    def log_reminder(
        self, client_id: str, expected_document_id: int, channel: str, message: str,
        tone: Optional[str] = None, follow_up_number: int = 1,
    ) -> Dict[str, Any]:
        with session_scope() as s:
            log = ReminderLog(
                client_id=client_id, expected_document_id=expected_document_id,
                channel=channel, tone=tone, message=message, follow_up_number=follow_up_number,
            )
            s.add(log)
            s.flush()
            return {"id": log.id, "sent_at": log.sent_at, "follow_up_number": log.follow_up_number}

    def get_reminder_history(self, client_id: str) -> List[Dict[str, Any]]:
        with session_scope() as s:
            rows = s.query(ReminderLog).filter_by(client_id=client_id).order_by(ReminderLog.sent_at).all()
            return [
                {
                    "id": r.id, "expected_document_id": r.expected_document_id, "channel": r.channel,
                    "tone": r.tone, "message": r.message, "follow_up_number": r.follow_up_number,
                    "status": r.status, "sent_at": r.sent_at,
                }
                for r in rows
            ]

    def dashboard_status(self, period: str) -> Dict[str, Any]:
        """
        Per-client rollup for the Demo 2 dashboard: how many documents
        are still missing, how many reminders have gone out.
        """
        with session_scope() as s:
            clients = s.query(Client).all()
            summary = []
            for client in clients:
                expected = s.query(ExpectedDocument).filter_by(client_id=client.id, period=period).all()
                if not expected:
                    continue
                received = sum(1 for e in expected if e.status == "received")
                missing = len(expected) - received
                reminder_count = (
                    s.query(ReminderLog)
                    .join(ExpectedDocument, ReminderLog.expected_document_id == ExpectedDocument.id)
                    .filter(ExpectedDocument.client_id == client.id, ExpectedDocument.period == period)
                    .count()
                )
                summary.append({
                    "client_id": client.id, "client_name": client.name,
                    "expected": len(expected), "received": received, "missing": missing,
                    "reminders_sent": reminder_count,
                })
            return {"period": period, "clients": summary}

    # ------------------------------------------------------------------
    # Demo 3 -- financial statement history + generated reports
    # ------------------------------------------------------------------

    def store_financial_statement(
        self, client_id: str, period: str, data: Dict[str, Any], statement_type: str = "income_statement",
    ) -> None:
        with session_scope() as s:
            s.add(FinancialStatement(
                client_id=client_id, period=period, statement_type=statement_type,
                data_json=json.dumps(data, default=str),
            ))

    def get_prior_statement(
        self, client_id: str, before_period: str, statement_type: str = "income_statement",
    ) -> Optional[Dict[str, Any]]:
        """
        Most recent statement strictly before `before_period` (string
        comparison, so periods should sort lexicographically, e.g.
        "2026-06" < "2026-07").
        """
        with session_scope() as s:
            row = (
                s.query(FinancialStatement)
                .filter(
                    FinancialStatement.client_id == client_id,
                    FinancialStatement.statement_type == statement_type,
                    FinancialStatement.period < before_period,
                )
                .order_by(FinancialStatement.period.desc())
                .first()
            )
            if row is None:
                return None
            return {"period": row.period, "data": json.loads(row.data_json)}

    def store_report(
        self, client_id: str, period: str, ratios: Dict[str, Any],
        anomalies: List[Dict[str, Any]], letter_text: str, status: str = "draft",
    ) -> int:
        with session_scope() as s:
            report = AnalysisReport(
                client_id=client_id, period=period,
                ratios_json=json.dumps(ratios, default=str),
                anomalies_json=json.dumps(anomalies, default=str),
                letter_text=letter_text, status=status,
            )
            s.add(report)
            s.flush()
            return report.id

    def get_reports(self, client_id: str) -> List[Dict[str, Any]]:
        with session_scope() as s:
            rows = (
                s.query(AnalysisReport)
                .filter_by(client_id=client_id)
                .order_by(AnalysisReport.generated_at.desc())
                .all()
            )
            return [
                {
                    "id": r.id, "period": r.period, "ratios": json.loads(r.ratios_json),
                    "anomalies": json.loads(r.anomalies_json), "letter_text": r.letter_text,
                    "status": r.status, "generated_at": r.generated_at,
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Shared -- human review queue
    # ------------------------------------------------------------------

    def flag_for_review(self, demo: str, ref_type: str, ref_id: str, reason: str) -> None:
        with session_scope() as s:
            s.add(ReviewQueueItem(demo=demo, ref_type=ref_type, ref_id=str(ref_id), reason=reason))
        logger.info(f"[{demo}] Flagged {ref_type} {ref_id} for review: {reason}")

    def list_review_queue(self, demo: Optional[str] = None, status: str = "open") -> List[Dict[str, Any]]:
        with session_scope() as s:
            query = s.query(ReviewQueueItem).filter_by(status=status)
            if demo:
                query = query.filter_by(demo=demo)
            rows = query.order_by(ReviewQueueItem.created_at).all()
            return [
                {
                    "id": r.id, "demo": r.demo, "ref_type": r.ref_type, "ref_id": r.ref_id,
                    "reason": r.reason, "status": r.status, "created_at": r.created_at,
                }
                for r in rows
            ]

    def resolve_review_item(self, item_id: int) -> bool:
        with session_scope() as s:
            item = s.get(ReviewQueueItem, item_id)
            if item is None:
                return False
            item.status = "resolved"
            item.resolved_at = _utcnow_str()
            return True


# ----------------------------------------------------------------------
# Quick manual test: exercises the Demo 1 supplier-learning path and the
# Demo 2 checklist/reminder/dashboard path end to end, in-memory.
# Run: python memory.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    import importlib
    import src.config as _config
    importlib.reload(_config)
    import src.database.database as _database
    importlib.reload(_database)
    from src.database.database import (  # noqa: F811 - reload with the in-memory URL applied
        Client, Document, JournalEntry, JournalLine, SupplierPattern,
        ExpectedDocument, ReminderLog, FinancialStatement, AnalysisReport,
        ReviewQueueItem, session_scope, init_db,
    )

    memory = MemoryStore()
    memory.upsert_client("c-001", "Rossi Impianti Srl", vat_number="IT01234567890", email="rossi@example.it")

    print("=== Demo 1: supplier auto-learning ===")
    print("First sighting:", memory.get_supplier_hint("c-001", "Acme Consulenze Srl"))
    memory.learn_supplier("c-001", "Acme Consulenze Srl", "IT99999999999", "B-07-CONS", "Consulenze professionali")
    memory.learn_supplier("c-001", "Acme Consulenze Srl", "IT99999999999", "B-07-CONS", "Consulenze professionali")
    hint = memory.get_supplier_hint("c-001", "Acme Consulenze Srl")
    print("After 2 invoices from the same supplier:", hint)
    assert hint["seen_count"] == 2

    print("\n=== Demo 2: checklist / missing docs / reminders / dashboard ===")
    memory.seed_expected_documents("c-001", "2026-07", ["bank_statement", "sales_invoices", "payroll"])
    memory.mark_document_received("c-001", "2026-07", "sales_invoices", received_doc_id="doc-001")
    missing = memory.get_missing_documents("c-001", "2026-07")
    print("Missing after 1 of 3 received:", [m["doc_type"] for m in missing])
    assert len(missing) == 2

    expected_id = missing[0]["id"]
    memory.log_reminder("c-001", expected_id, channel="email", message="Please send your bank statement.", tone="formal")
    print("Dashboard:", memory.dashboard_status("2026-07"))

    print("\n=== Shared review queue ===")
    memory.flag_for_review("demo_1", "document", "doc-002", "Low OCR confidence")
    print(memory.list_review_queue())

    print("\nmemory.py self-test passed.")
