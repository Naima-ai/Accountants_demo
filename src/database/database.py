"""
database.py

The on-prem database backing all three demos. One schema, three
"heads" (matching the shared-architecture slide): Demo 1 writes
Documents/JournalEntries, Demo 2 writes ExpectedDocuments/ReminderLogs,
Demo 3 writes FinancialStatements/AnalysisReports. memory.py is the
only module that should import from here directly -- everything else
(orchestrators, agents, API) goes through memory.py.

Usage:
    from database import init_db, session_scope

    init_db()  # once, at app startup
    with session_scope() as session:
        session.add(Client(id="c-001", name="Rossi Impianti Srl"))
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    create_engine, ForeignKey, String, Float, Boolean, Text, DateTime, Integer,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session,
)

try:
    from config import DATABASE_URL, ensure_dirs
except ImportError:  # pragma: no cover - allows running this file standalone
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import DATABASE_URL, ensure_dirs

logger = logging.getLogger("database")
logging.basicConfig(level=logging.INFO)


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------------
# Shared: clients (the accounting firm's customers, common to all 3 demos)
# ----------------------------------------------------------------------

class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    vat_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Communication tone the reminder agent should draft in for this
    # client -- e.g. "formal", "friendly". Demo 2's per-client tone.
    preferred_tone: Mapped[Optional[str]] = mapped_column(String, nullable=True, default="formal")
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_str)

    documents: Mapped[list["Document"]] = relationship(back_populates="client")
    expected_documents: Mapped[list["ExpectedDocument"]] = relationship(back_populates="client")


# ----------------------------------------------------------------------
# Demo 1: ingestion -> classification -> extraction -> validation -> accounting
# ----------------------------------------------------------------------

class Document(Base):
    __tablename__ = "documents"

    # Same id as IngestedDocument.doc_id from demo_1/ingestion.py, so
    # rows here can always be traced back to the file that produced them.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"), nullable=True)

    original_filename: Mapped[str] = mapped_column(String)
    source_path: Mapped[str] = mapped_column(String)
    file_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    classification: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    classification_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ingested | classified | extracted | validated | accounted | posted | failed
    status: Mapped[str] = mapped_column(String, default="ingested")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    extracted_fields_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[str] = mapped_column(String, default=_utcnow_str)

    client: Mapped[Optional["Client"]] = relationship(back_populates="documents")
    journal_entry: Mapped[Optional["JournalEntry"]] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)

    entry_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending_review")  # ready_to_post | pending_review
    total_debit: Mapped[float] = mapped_column(Float, default=0.0)
    total_credit: Mapped[float] = mapped_column(Float, default=0.0)
    is_balanced: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_str)

    document: Mapped["Document"] = relationship(back_populates="journal_entry")
    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))

    account_code: Mapped[str] = mapped_column(String)
    account_name: Mapped[str] = mapped_column(String)
    debit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    credit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    entry: Mapped["JournalEntry"] = relationship(back_populates="lines")


class SupplierPattern(Base):
    """
    One row per (client, supplier) pair seen by Demo 1. This is the
    "auto-learn client patterns" memory -- lets the pipeline recognize
    a recurring supplier and pre-fill its usual COA category, which is
    the mechanism behind the brief's "accuracy rises as early as the
    2nd client" demo beat.
    """
    __tablename__ = "supplier_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"), nullable=True)

    supplier_name: Mapped[str] = mapped_column(String)
    supplier_vat: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    coa_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    coa_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[str] = mapped_column(String, default=_utcnow_str)
    last_seen: Mapped[str] = mapped_column(String, default=_utcnow_str)


# ----------------------------------------------------------------------
# Demo 2: document collection / reminder agent
# ----------------------------------------------------------------------

class ExpectedDocument(Base):
    """One row per document a client is expected to supply for a period."""
    __tablename__ = "expected_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))

    period: Mapped[str] = mapped_column(String)  # e.g. "2026-07"
    doc_type: Mapped[str] = mapped_column(String)  # e.g. "bank_statement", "sales_invoices"

    # expected | received | missing
    status: Mapped[str] = mapped_column(String, default="expected")
    received_doc_id: Mapped[Optional[str]] = mapped_column(ForeignKey("documents.id"), nullable=True)
    due_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_str)

    client: Mapped["Client"] = relationship(back_populates="expected_documents")
    reminders: Mapped[list["ReminderLog"]] = relationship(back_populates="expected_document")


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    expected_document_id: Mapped[int] = mapped_column(ForeignKey("expected_documents.id"))

    channel: Mapped[str] = mapped_column(String)  # email | pec | whatsapp
    tone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    follow_up_number: Mapped[int] = mapped_column(Integer, default=1)

    # sent | responded | escalated
    status: Mapped[str] = mapped_column(String, default="sent")
    sent_at: Mapped[str] = mapped_column(String, default=_utcnow_str)

    expected_document: Mapped["ExpectedDocument"] = relationship(back_populates="reminders")


# ----------------------------------------------------------------------
# Demo 3: financial analysis / advisory report agent
# ----------------------------------------------------------------------

class FinancialStatement(Base):
    __tablename__ = "financial_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))

    period: Mapped[str] = mapped_column(String)
    statement_type: Mapped[str] = mapped_column(String, default="income_statement")
    data_json: Mapped[str] = mapped_column(Text)  # raw statement figures, JSON-encoded
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_str)


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))

    period: Mapped[str] = mapped_column(String)
    ratios_json: Mapped[str] = mapped_column(Text)
    anomalies_json: Mapped[str] = mapped_column(Text)
    letter_text: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String, default="draft")  # draft | sent
    generated_at: Mapped[str] = mapped_column(String, default=_utcnow_str)


# ----------------------------------------------------------------------
# Shared: human-review queue (all 3 demos feed into this one queue)
# ----------------------------------------------------------------------

class ReviewQueueItem(Base):
    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    demo: Mapped[str] = mapped_column(String)  # "demo_1" | "demo_2" | "demo_3"
    ref_type: Mapped[str] = mapped_column(String)  # "document" | "expected_document" | "analysis_report"
    ref_id: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String, default="open")  # open | resolved
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_str)
    resolved_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


# ----------------------------------------------------------------------
# Engine / session plumbing
# ----------------------------------------------------------------------

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        ensure_dirs()
        connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
        _engine = create_engine(DATABASE_URL, connect_args=connect_args)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    """Create all tables if they don't exist yet. Safe to call repeatedly."""
    Base.metadata.create_all(bind=get_engine())
    logger.info(f"Database ready at {DATABASE_URL}")


@contextmanager
def session_scope():
    """Provide a transactional scope: commits on success, rolls back on error."""
    session: Session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ----------------------------------------------------------------------
# Quick manual test: create the schema against a throwaway SQLite file
# and round-trip one row through each table family.
# Run: python database.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import os
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    # Re-read config with the override in place.
    import importlib
    import config as _config
    importlib.reload(_config)

    DATABASE_URL = _config.DATABASE_URL  # noqa: F811 - test-only override

    init_db()

    with session_scope() as s:
        s.add(Client(id="c-001", name="Rossi Impianti Srl", vat_number="IT01234567890"))
        s.add(Document(
            id="doc-001", client_id="c-001", original_filename="invoice.xml",
            source_path="/tmp/invoice.xml", file_type="xml", status="ingested",
        ))

    with session_scope() as s:
        client = s.get(Client, "c-001")
        doc = s.get(Document, "doc-001")
        print(f"Client: {client.name} ({client.vat_number})")
        print(f"Document: {doc.original_filename} status={doc.status}")

    print("database.py self-test passed.")
