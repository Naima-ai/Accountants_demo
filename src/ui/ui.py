"""
Streamlit dashboard for all three demos, sharing the same on-prem stack
(memory.py / database.py) that the orchestrators already write to.

    Demo 1 - Doc-to-Data:      before/after document review + journal entry
    Demo 2 - Client Reminders: missing-document gap detection + follow-ups
    Demo 3 - Advisory Reports: financial ratios, anomalies, advisory letter
"""

import glob
import os
import sys
import time
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from pathlib import Path

# Makes `from src...` imports work when Streamlit executes this file directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.memory.memory import MemoryStore
from src.orchestration.demo1_orchestrator import Demo1Orchestrator
from src.orchestration.demo2_orchestrator import ReminderOrchestrator
from src.orchestration.demo3_orchestrator import ReportOrchestrator
from src.extraction.extractor import ExtractedFields
from src.config import REMINDER_FOLLOWUP_INTERVAL_DAYS, REMINDER_MANUAL_MINUTES_PER_DOC

_SAMPLES_DIR = os.path.join(_REPO_ROOT, "data_set", "samples")
_UPLOAD_DIR = os.path.join(_REPO_ROOT, "var", "ui_uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)

DEMO_CLIENTS = [
    ("c-001", "Rossi Impianti Srl", "friendly"),
    ("c-002", "Bianchi Consulting Srl", "formal"),
    ("c-003", "Verdi Logistica Srl", "formal"),
]

DOC_TYPE_LABELS = {
    "bank_statement": "Bank statement",
    "sales_invoices": "Sales invoices",
    "purchase_invoices": "Purchase invoices",
    "payroll": "Payroll documentation",
}

# ----------------------------------------------------------------------
# Page config + theme
# ----------------------------------------------------------------------
PANEL = "#0F2545"
TEXT = "#E7EEF9"
SUBTEXT = "#8FA6C9"
OK = "#33C481"
WARN = "#E8A93B"
ERR = "#E5566B"

def load_css():
    css_path = Path(__file__).parent / "styles.css"

    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(
            f"<style>{f.read()}</style>",
            unsafe_allow_html=True,
        )
        
st.set_page_config(
    page_title="Private Edge Agents for Accountants",
    layout="wide",
    initial_sidebar_state="expanded",
)
load_css()


# ----------------------------------------------------------------------
# Cached resources -- one shared MemoryStore/orchestrator set per session
# ----------------------------------------------------------------------

@st.cache_resource
def get_memory() -> MemoryStore:
    return MemoryStore()


@st.cache_resource
def get_demo1_orchestrator() -> Demo1Orchestrator:
    return Demo1Orchestrator(memory=get_memory())


@st.cache_resource
def get_demo2_orchestrator() -> ReminderOrchestrator:
    return ReminderOrchestrator(memory=get_memory())


@st.cache_resource
def get_demo3_orchestrator() -> ReportOrchestrator:
    return ReportOrchestrator(memory=get_memory())


memory = get_memory()
demo1_orch = get_demo1_orchestrator()
demo2_orch = get_demo2_orchestrator()
demo3_orch = get_demo3_orchestrator()


@st.cache_resource
def get_latency_store() -> Dict[str, List[float]]:
    """Mirrors api.py's in-process latency tracker -- this UI calls the
    orchestrators directly, so it keeps its own copy instead."""
    return {"demo-1-process": [], "demo-2-run": [], "demo-3-generate": []}


def _record_latency(key: str, started_at: float) -> None:
    elapsed_ms = (time.time() - started_at) * 1000
    store = get_latency_store()
    bucket = store.setdefault(key, [])
    bucket.append(elapsed_ms)
    if len(bucket) > 200:
        del bucket[: len(bucket) - 200]


def _avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


# ----------------------------------------------------------------------
# Small render helpers
# ----------------------------------------------------------------------

def pill(text: str, kind: str = "neutral") -> str:
    return f'<span class="pill pill-{kind}">{text}</span>'


def panel_open(title: str) -> None:
    st.markdown(f'<div class="panel"><div class="panel-title">{title}</div>', unsafe_allow_html=True)


def panel_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def render_banner() -> None:
    clients = memory.list_clients()
    open_review = memory.list_review_queue()
    metrics = [
        ("EDGE INFERENCE", "Active", True),
        ("DATA EGRESS", "0 Bytes", True),
        ("CLIENTS ONBOARDED", str(len(clients)), False),
        ("AWAITING REVIEW", str(len(open_review)), False),
    ]
    cells = []
    for label, value, live in metrics:
        cls = "metric live" if live else "metric"
        dot = '<span class="dot"></span>' if live else ""
        cells.append(
            f'<div class="{cls}"><div class="label">{label}</div>'
            f'<div class="value">{dot}{value}</div></div>'
        )
    st.markdown(f'<div class="console-banner">{"".join(cells)}</div>', unsafe_allow_html=True)


def client_options() -> List[Dict[str, Any]]:
    clients = memory.list_clients()
    if not clients:
        for cid, name, tone in DEMO_CLIENTS:
            memory.upsert_client(cid, name, preferred_tone=tone)
        clients = memory.list_clients()
    return clients


def client_label(c: Dict[str, Any]) -> str:
    return f"{c['name']} ({c['id']})"


# ----------------------------------------------------------------------
# Sidebar -- navigation + shared client management
# ----------------------------------------------------------------------

def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("Private Edge Agents for Accountants")
        st.page_link(
            dashboard_page,
            label="Dashboard",
            icon=":material/dashboard:",
            width="stretch",
        )

        st.page_link(
            demo1_page,
            label="Doc-to-Data",
            icon=":material/description:",
            width="stretch",
        )

        st.page_link(
            demo2_page,
            label="Client Reminders",
            icon=":material/notifications:",
            width="stretch",
        )

        st.page_link(
            demo3_page,
            label="Advisory Reports",
            icon=":material/analytics:",
            width="stretch",
        )


        st.markdown("---")

        with st.expander("Clients"):
            clients = client_options()

            # ---------------------------------------------------------------
            # Scrollable client list
            # ---------------------------------------------------------------

            client_html = '<div class="client-list">'

            for c in clients:
                client_html += (
                    f'<div class="client-item">'
                    f'<div class="client-name">{c["name"]}</div>'
                    f'<div class="client-meta">'
                    f'{c["id"]} · tone: {c.get("preferred_tone") or "formal"}'
                    f'</div>'
                    f'</div>'
                )

            client_html += "</div>"

            st.markdown(
                client_html,
                unsafe_allow_html=True,
            )

            # ---------------------------------------------------------------
            # Add / update client
            # ---------------------------------------------------------------

            with st.form("add_client_form", clear_on_submit=True):

                st.caption("Add / update a client")

                new_id = st.text_input(
                    "Client ID",
                    placeholder="c-004",
                )

                new_name = st.text_input("Name")

                new_tone = st.selectbox(
                    "Preferred tone",
                    ["formal", "friendly"],
                )

                new_email = st.text_input(
                    "Email (optional)",
                )

                submitted = st.form_submit_button(
                    "Save client"
                )

                if submitted and new_id and new_name:
                    memory.upsert_client(
                        new_id,
                        new_name,
                        email=new_email or None,
                        preferred_tone=new_tone,
                    )                  
                            
                    
                    
                    st.success(f"Saved {new_name}.")



# ========================================================================
# DEMO 1 -- Doc-to-Data
# ========================================================================

DOC_TYPE_BADGES = {
    "invoice": "Invoice", "receipt": "Receipt", "bank_statement": "Bank Statement",
    "financial_statement": "Financial Statement", "payroll": "Payroll",
    "chart_of_accounts": "Chart of Accounts", "other": "Other", "unknown": "Unknown",
}


def _sample_picks() -> Dict[str, str]:
    picks = {}
    candidates = [
        ("XML e-invoice", os.path.join(_SAMPLES_DIR, "xml", "synthetic_invoice_1.xml")),
        ("Plain-text invoice", os.path.join(_SAMPLES_DIR, "text", "synthetic_invoice_2.txt")),
        ("Native PDF invoice", os.path.join(_SAMPLES_DIR, "pdf", "synthetic_invoice_3.pdf")),
        ("Scanned PDF (OCR path)", os.path.join(_SAMPLES_DIR, "pdf_scanned", "synthetic_invoice_4.pdf")),
    ]
    for label, path in candidates:
        if os.path.exists(path):
            picks[label] = path
    image_dir = os.path.join(_SAMPLES_DIR, "images")
    if os.path.isdir(image_dir):
        images = sorted(glob.glob(os.path.join(image_dir, "*.png")))
        if images:
            picks["Real photographed receipt"] = images[0]
    return picks


def _save_upload(uploaded_file) -> str:
    suffix = os.path.splitext(uploaded_file.name)[1]
    path = os.path.join(_UPLOAD_DIR, f"{uuid.uuid4().hex}{suffix}")
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


def _run_demo1_pipeline(file_path: str, client_id: Optional[str], doc_id: Optional[str] = None) -> Dict[str, Any]:
    """Runs ingest -> classify -> extract -> validate -> journal entry
    WITHOUT persisting anything -- persistence only happens once a human
    clicks 'Approve & Push' (the review gate)."""
    orch = demo1_orch
    doc = orch.pipeline.ingest_file(file_path)
    if doc_id is not None:
        doc.doc_id = doc_id
    result: Dict[str, Any] = {"doc": doc, "file_path": file_path}
    if not doc.success:
        result["error"] = doc.error
        return result

    ci = doc.to_classifier_input()
    classification = orch.classifier.classify(ci)
    fields = orch.extractor.extract(ci, classification.document_type)
    validation = orch.validator.validate(fields)
    entry = orch.accounting_agent.build_journal_entry(fields, validation)
    supplier_hint = orch.memory.get_supplier_hint(client_id, fields.supplier_name) if fields.supplier_name else None

    result.update(
        classification=classification, fields=fields, validation=validation,
        entry=entry, supplier_hint=supplier_hint,
    )
    return result


def _push_demo1_result(client_id: str) -> None:
    r = st.session_state["demo1_result"]
    doc, classification, fields, validation, entry = r["doc"], r["classification"], r["fields"], r["validation"], r["entry"]
    orch = demo1_orch

    orch.memory.record_document(
        doc_id=doc.doc_id, original_filename=doc.original_filename, source_path=r["file_path"],
        client_id=client_id, file_type=doc.file_type.value,
        classification=classification.document_type.value, classification_confidence=classification.confidence,
        status="accounted", needs_review=False, extracted_fields=fields.model_dump(),
    )
    entry_dict = entry.model_dump()
    entry_dict["status"] = "ready_to_post"
    orch.memory.record_journal_entry(doc.doc_id, entry_dict)

    if fields.supplier_name and entry.lines:
        primary_line = max((l for l in entry.lines if l.debit), key=lambda l: l.debit, default=None)
        if primary_line:
            orch.memory.learn_supplier(
                client_id, fields.supplier_name, fields.supplier_vat,
                primary_line.account_code, primary_line.account_name,
            )
    for item in orch.memory.list_review_queue("demo_1"):
        if item["ref_id"] == doc.doc_id:
            orch.memory.resolve_review_item(item["id"])

    st.session_state["demo1_pushed"] = True


def render_original_preview(file_path: str, doc) -> None:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff"):
            st.image(
                file_path,
                use_container_width=True,
            )
        elif ext == ".pdf":
            import fitz
            pdf = fitz.open(file_path)
            pix = pdf[0].get_pixmap(dpi=110)
            st.image(
                pix.tobytes("png"),
                use_container_width=True,
            )
            pdf.close()
        elif ext == ".xml":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                st.code(f.read(), language="xml", line_numbers=False)
        else:
            with open(
                file_path,
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as f:
                content = f.read()
            import html
            st.markdown(
                f'''
                <div class="original-preview">
                    <pre>{html.escape(content)}</pre>
                </div>
                ''',
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.warning(
            f"Couldn't render a preview ({e}). "
            "Showing raw extracted text below instead."
        )

def _load_existing_documents(client_id: str) -> List[Dict[str, Any]]:
    return memory.list_documents(client_id)


def _render_stored_document(detail: Dict[str, Any]) -> None:
    left, right = st.columns(2)
    source_path = detail.get("source_path")
    EMDASH = "\u2014"

    with left:
        panel_open("BEFORE \u00b7 Original document")
        if source_path and os.path.exists(source_path):
            preview_doc = demo1_orch.pipeline.ingest_file(source_path)
            render_original_preview(source_path, preview_doc)
            panel_close()
            panel_open("BEFORE \u00b7 Raw extracted text")
            st.text_area("raw_text_stored", preview_doc.full_text[:4000] or "(no text extracted)",
                         height=600, label_visibility="collapsed")
        else:
            st.caption("Original file is no longer available on disk.")
        panel_close()

    with right:
        panel_open("AFTER \u00b7 Stored classification & extraction")
        doc_type = detail.get("classification") or "unknown"
        conf = detail.get("classification_confidence")
        st.markdown(
            pill(DOC_TYPE_BADGES.get(doc_type, doc_type), "doctype") + "&nbsp;&nbsp;" +
            pill(f"confidence {conf:.0%}" if conf is not None else "confidence \u2014", "neutral"),
            unsafe_allow_html=True,
        )
        extraction = detail.get("extraction") or {}
        st.markdown(
            f"| Field | Value |\n|---|---|\n"
            f"| Supplier | {extraction.get('supplier_name') or EMDASH} |\n"
            f"| VAT / Tax ID | {extraction.get('supplier_vat') or EMDASH} |\n"
            f"| Date | {extraction.get('document_date') or EMDASH} |\n"
            f"| Currency | {extraction.get('currency') or EMDASH} |\n"
            f"| Total | {extraction.get('total_amount') or EMDASH} |\n"
        )
        line_items = extraction.get("line_items") or []
        if line_items:
            st.dataframe(pd.DataFrame(line_items), use_container_width=True, hide_index=True)
        panel_close()

        panel_open("AFTER \u00b7 Validation (recomputed from stored fields)")
        if extraction:
            try:
                fields_obj = ExtractedFields(**extraction)
                validation = demo1_orch.validator.validate(fields_obj)
                if validation.is_valid and not validation.needs_review:
                    st.markdown(pill("\u2705 Balanced & valid", "ok"), unsafe_allow_html=True)
                elif validation.is_valid:
                    st.markdown(pill("\u26a0 Passed checks, but needs review", "warn"), unsafe_allow_html=True)
                else:
                    st.markdown(pill("\u2717 Validation issues found", "err"), unsafe_allow_html=True)
                for issue in validation.issues:
                    kind = {"error": "err", "warning": "warn", "info": "neutral"}.get(issue.severity.value, "neutral")
                    st.markdown(pill(f"{issue.field}: {issue.message}", kind), unsafe_allow_html=True)
            except Exception as e:
                st.caption(f"Could not recompute validation from stored fields: {e}")
        else:
            st.caption("No extracted fields stored for this document.")
        panel_close()

        panel_open("AFTER \u00b7 Journal entry (Dare / Avere)")
        journal = detail.get("journal_entry")
        if journal and journal.get("lines"):
            je_df = pd.DataFrame([
                {"Account": f"{l['account_code']} \u2014 {l['account_name']}",
                 "Dare": l.get("debit") or "", "Avere": l.get("credit") or "", "Note": l.get("description") or ""}
                for l in journal["lines"]
            ])
            st.dataframe(je_df, use_container_width=True, hide_index=True)
            balanced_pill = pill("Balanced", "ok") if journal.get("is_balanced") else pill("Not balanced", "err")
            status_pill = pill("Ready to post", "ok") if journal.get("status") == "ready_to_post" else pill("Pending review", "warn")
            st.markdown(f"{balanced_pill} &nbsp; {status_pill}", unsafe_allow_html=True)
        else:
            st.caption("No journal entry stored for this document.")
        if detail.get("review_reason"):
            st.markdown(pill(f"Review queue: {detail['review_reason']}", "warn"), unsafe_allow_html=True)
        panel_close()


def render_demo1() -> None:
    st.title("Doc-to-Data")
    st.caption("Chaotic folder in \u2192 validated, bookable journal entries out")
    render_banner()

    clients = client_options()
    col_client, col_upload = st.columns([1, 2])
    with col_client:
        client = st.selectbox("Client", clients, format_func=client_label, key="d1_client")
        client_id = client["id"]

    with col_upload:
        uploaded = st.file_uploader(
            "Upload a document (PDF, image/receipt, or XML e-invoice)",
            type=["pdf", "png", "jpg", "jpeg", "xml", "txt"],
        )

    st.markdown("**Or try a sample:**")
    picks = _sample_picks()
    sample_cols = st.columns(max(len(picks), 1))
    chosen_sample_path = None
    for (label, path), col in zip(picks.items(), sample_cols):
        with col:
            if st.button(label, use_container_width=True, key=f"sample_{label}"):
                chosen_sample_path = path
    if not picks:
        st.caption("No generated samples found yet \u2014 run `src/data/generate_synthetic_samples.py` first.")

    # ---------------- Existing documents for this client ----------------
    panel_open(f"Existing documents \u2014 {client['name']}")
    existing_docs = _load_existing_documents(client_id)
    if not existing_docs:
        st.caption(
            "No documents registered yet for this client. "
        )
    else:
        hdr = st.columns([3, 1.4, 1.6, 1.3, 1.2])
        for col, label in zip(hdr, ["File", "Type", "Classification", "Status", ""]):
            col.markdown(f'<span class="subtext">{label}</span>', unsafe_allow_html=True)
        for d in existing_docs:
            row = st.columns([3, 1.4, 1.6, 1.3, 1.2])
            row[0].write(d["original_filename"])
            row[1].write(d.get("file_type") or "\u2014")
            row[2].write(d.get("classification") or "\u2014")
            status_kind = {
                "accounted": "ok", "needs_review": "warn", "uploaded": "neutral", "failed": "err",
            }.get(d["status"], "neutral")
            row[3].markdown(pill(d["status"], status_kind), unsafe_allow_html=True)
            with row[4]:
                if d["status"] == "uploaded":
                    if st.button("Run", key=f"run_{d['doc_id']}", use_container_width=True):
                        st.session_state["demo1_pending_run"] = d["doc_id"]
                        st.session_state.pop("demo1_view_doc_id", None)
                        st.rerun()
                else:
                    if st.button("View", key=f"view_{d['doc_id']}", use_container_width=True):
                        st.session_state["demo1_view_doc_id"] = d["doc_id"]
                        st.session_state.pop("demo1_result", None)
                        st.rerun()
    panel_close()

    # ---------------- View mode: read-only stored result ----------------
    view_doc_id = st.session_state.get("demo1_view_doc_id")
    if view_doc_id:
        detail = memory.get_document_detail(view_doc_id)
        st.markdown("---")
        if detail is None:
            st.error("Could not load stored document detail.")
        else:
            st.caption(f"Viewing stored result for **{detail['original_filename']}** \u2014 already processed, read-only.")
            _render_stored_document(detail)
        if st.button("\u2190 Back to live pipeline view"):
            st.session_state.pop("demo1_view_doc_id", None)
            st.rerun()
        return

    # ---------------- Decide what to run through the live pipeline ----------------
    pending_run_id = st.session_state.pop("demo1_pending_run", None)
    active_path = None
    active_doc_id = None
    if uploaded is not None:
        active_path = _save_upload(uploaded)
    elif chosen_sample_path is not None:
        active_path = chosen_sample_path
    elif pending_run_id is not None:
        pending_detail = memory.get_document_detail(pending_run_id)
        if pending_detail and pending_detail.get("source_path") and os.path.exists(pending_detail["source_path"]):
            active_path = pending_detail["source_path"]
            active_doc_id = pending_run_id
        else:
            st.error("Source file for this document is missing on disk.")

    if active_path is not None:
        with st.spinner("Ingesting \u2192 classifying \u2192 extracting \u2192 validating..."):
            started = time.time()
            st.session_state["demo1_result"] = _run_demo1_pipeline(active_path, client_id, doc_id=active_doc_id)
            _record_latency("demo-1-process", started)
        st.session_state["demo1_pushed"] = False
        st.session_state.pop("demo1_edit", None)

    result = st.session_state.get("demo1_result")
    if result is None:
        st.info("Upload a document, pick a sample, or click Run on one of the client's existing documents above.")
        return

    if result.get("error"):
        st.error(f"Ingestion failed: {result['error']}")
        return

    doc = result["doc"]
    classification = result["classification"]
    fields = result["fields"]
    validation = result["validation"]
    entry = result["entry"]

    st.markdown("---")
    left, right = st.columns(2)

    # ---------------- LEFT: BEFORE ----------------
    with left:
        panel_open("BEFORE \u00b7 Original document")
        render_original_preview(result["file_path"], doc)
        panel_close()

        panel_open("BEFORE \u00b7 Raw extracted text")
        st.text_area("raw_text", doc.full_text[:4000] or "(no text extracted)",
                     height=220, label_visibility="collapsed")
        if doc.warnings:
            for w in doc.warnings:
                st.markdown(pill(f"\u26a0 {w}", "warn"), unsafe_allow_html=True)
        panel_close()

    # ---------------- RIGHT: AFTER ----------------
    with right:
        panel_open("AFTER \u00b7 Classification & extraction")
        badge = DOC_TYPE_BADGES.get(classification.document_type.value, classification.document_type.value)
        st.markdown(
            pill(badge, "doctype") + "&nbsp;&nbsp;" +
            pill(f"confidence {classification.confidence:.0%}", "neutral") + "&nbsp;&nbsp;" +
            pill(f"method: {classification.method}", "neutral"),
            unsafe_allow_html=True,
        )
        if result.get("supplier_hint"):
            hint = result["supplier_hint"]
            st.markdown(
                pill(f"\u2713 recognized supplier \u2014 seen {hint['seen_count']}x \u2192 {hint['coa_name']}", "ok"),
                unsafe_allow_html=True,
            )

        edited = st.session_state.get("demo1_edit", {})
        f = {
            "supplier_name": edited.get("supplier_name", fields.supplier_name or ""),
            "supplier_vat": edited.get("supplier_vat", fields.supplier_vat or ""),
            "document_date": edited.get("document_date", fields.document_date or ""),
            "currency": edited.get("currency", fields.currency or "EUR"),
            "total_amount": edited.get("total_amount", fields.total_amount or ""),
        }
        EMDASH = "\u2014"
        row_supplier = f["supplier_name"] or EMDASH
        row_vat = f["supplier_vat"] or EMDASH
        row_date = f["document_date"] or EMDASH
        row_currency = f["currency"] or EMDASH
        row_total = f["total_amount"] or EMDASH
        st.markdown(
            f"| Field | Value |\n|---|---|\n"
            f"| Supplier | {row_supplier} |\n"
            f"| VAT / Tax ID | {row_vat} |\n"
            f"| Date | {row_date} |\n"
            f"| Currency | {row_currency} |\n"
            f"| Total | {row_total} |\n"
        )

        if fields.line_items:
            li_df = pd.DataFrame([li.model_dump() for li in fields.line_items])
            st.dataframe(li_df, use_container_width=True, hide_index=True)
        panel_close()

        # ---- Validation status ----
        panel_open("AFTER \u00b7 Validation")
        if validation.is_valid and not validation.needs_review:
            st.markdown(pill("\u2705 Balanced & valid \u2014 ready to post", "ok"), unsafe_allow_html=True)
        elif validation.is_valid:
            st.markdown(pill("\u26a0 Passed checks, but needs review", "warn"), unsafe_allow_html=True)
        else:
            st.markdown(pill("\u2717 Validation issues found", "err"), unsafe_allow_html=True)

        for issue in validation.issues:
            kind = {"error": "err", "warning": "warn", "info": "neutral"}.get(issue.severity.value, "neutral")
            st.markdown(pill(f"{issue.field}: {issue.message}", kind), unsafe_allow_html=True)
        panel_close()

        # ---- Journal entry ----
        panel_open("AFTER \u00b7 Journal entry (Dare / Avere)")
        if entry.lines:
            je_df = pd.DataFrame([
                {"Account": f"{l.account_code} \u2014 {l.account_name}",
                 "Dare": l.debit or "", "Avere": l.credit or "", "Note": l.description or ""}
                for l in entry.lines
            ])
            st.dataframe(je_df, use_container_width=True, hide_index=True)
        balanced_pill = pill("Balanced", "ok") if entry.is_balanced else pill("Not balanced", "err")
        status_pill = pill("Ready to post", "ok") if entry.status == "ready_to_post" else pill("Pending review", "warn")
        st.markdown(f"{balanced_pill} &nbsp; {status_pill}", unsafe_allow_html=True)
        panel_close()

        # ---- Review gate ----
        panel_open("REVIEW GATE")
        if st.session_state.get("demo1_pushed"):
            st.markdown(pill("\u2713 Pushed to books", "ok"), unsafe_allow_html=True)
        else:
            low_confidence = entry.status != "ready_to_post"
            if low_confidence:
                st.caption("Low confidence or unresolved issues \u2014 edit before approving, or push as-is.")
                with st.form("edit_form"):
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        e_supplier = st.text_input("Supplier name", value=f["supplier_name"])
                        e_vat = st.text_input("VAT / Tax ID", value=f["supplier_vat"])
                        e_date = st.text_input("Document date (YYYY-MM-DD)", value=f["document_date"])
                    with ec2:
                        e_currency = st.text_input("Currency", value=f["currency"])
                        e_total = st.text_input("Total amount", value=f["total_amount"])
                    if st.form_submit_button("Recalculate with edits"):
                        new_fields = fields.model_copy(update={
                            "supplier_name": e_supplier or None, "supplier_vat": e_vat or None,
                            "document_date": e_date or None, "currency": e_currency or None,
                            "total_amount": e_total or None,
                        })
                        new_validation = demo1_orch.validator.validate(new_fields)
                        new_entry = demo1_orch.accounting_agent.build_journal_entry(new_fields, new_validation)
                        st.session_state["demo1_result"].update(
                            fields=new_fields, validation=new_validation, entry=new_entry
                        )
                        st.session_state["demo1_edit"] = {
                            "supplier_name": e_supplier, "supplier_vat": e_vat, "document_date": e_date,
                            "currency": e_currency, "total_amount": e_total,
                        }
                        st.rerun()

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("\u2713 Approve & Push", use_container_width=True, key="approve_push"):
                    _push_demo1_result(client_id)
                    st.rerun()
            with btn_col2:
                st.caption("Approving posts the journal entry, resolves any review-queue flag, "
                           "and reinforces the supplier \u2192 account mapping for next time.")
        panel_close()

# ========================================================================
# DEMO 2 -- Client Reminders & Document Collection
# ========================================================================
def render_period_picker(key_prefix: str, default_freq: str = "Monthly") -> str:
    """Frequency selector + a dropdown of concrete periods generated from
    it, instead of free-text entry. Returns the chosen period string
    (e.g. '2026-08', '2026-Q3', or '2026')."""
    freq_col, period_col = st.columns([1, 2])
    with freq_col:
        freq = st.selectbox(
            "Frequency", ["Monthly", "Quarterly", "Yearly"],
            index=["Monthly", "Quarterly", "Yearly"].index(default_freq),
            key=f"{key_prefix}_freq",
        )

    today = date.today()
    options: List[str] = []
    if freq == "Monthly":
        y, m = today.year, today.month
        for _ in range(12):
            options.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                m, y = 12, y - 1
    elif freq == "Quarterly":
        y, q = today.year, (today.month - 1) // 3 + 1
        for _ in range(8):
            options.append(f"{y:04d}-Q{q}")
            q -= 1
            if q == 0:
                q, y = 4, y - 1
    else:  # Yearly
        options = [str(today.year - i) for i in range(5)]

    with period_col:
        return st.selectbox("Period", options, key=f"{key_prefix}_period")
    
def render_demo2() -> None:
    st.title("Client Reminders & Document Collection")
    st.caption(
        "Knows what's owed, detects the gaps, drafts + sends the follow-up "
        "in the client's own tone"
    )
    render_banner()

    clients = client_options()
    # Shared client selection
    if "d2_selected_client_id" not in st.session_state:
        st.session_state["d2_selected_client_id"] = clients[0]["id"]

    # If the available client list changes, make sure the stored client is still valid.
    client_ids = {c["id"] for c in clients}

    if st.session_state["d2_selected_client_id"] not in client_ids:
        st.session_state["d2_selected_client_id"] = clients[0]["id"]

    # Find the currently selected client.
    selected_client_index = next(
        (
            i
            for i, c in enumerate(clients)
            if c["id"] == st.session_state["d2_selected_client_id"]
        ),
        0,
    )

    # --------------------------------------------------------------------
    # Period + Client
    # --------------------------------------------------------------------

    top1, top2 = st.columns([1, 1])
    with top1:
            period = render_period_picker("d2", default_freq="Monthly")
    with top2:
        client = st.selectbox(
            "Client",
            clients,
            index=selected_client_index,
            format_func=client_label,
            key="d2_master_client",
        )

        # Store the current selection as the shared Demo 2 client.
        st.session_state["d2_selected_client_id"] = client["id"]

    # --------------------------------------------------------------------
    # Tabs
    # --------------------------------------------------------------------

    tab_checklist, tab_run, tab_dashboard = st.tabs(
        [
            "Checklist",
            "Run reminders",
            "Dashboard & history",
        ]
    )

    # ====================================================================
    # CHECKLIST TAB
    # ====================================================================

    with tab_checklist:

        panel_open("Set up the expected-document checklist")

        c1, c2 = st.columns([1, 2])

        with c1:
            st.write('<p class="custom-text">Selected client</p>',unsafe_allow_html=True)
            st.markdown(
                f"**{client['name']}**"
            )

        with c2:
            doc_types = st.multiselect(
                "Documents expected this period",
                list(DOC_TYPE_LABELS.keys()),
                default=[
                    "bank_statement",
                    "sales_invoices",
                    "payroll",
                ],
                format_func=lambda k: DOC_TYPE_LABELS[k],
                key="d2_doc_types",
            )

        # ---------------------------------------------------------------
        # Seed checklist
        # ---------------------------------------------------------------

        if st.button(
            "Seed checklist for this client/period",
            key="d2_seed_checklist",
        ):
            memory.seed_expected_documents(
                client["id"],
                period,
                doc_types,
            )

            st.success(
                f"Checklist seeded for "
                f"{client['name']} — {period}."
            )

        panel_close()

        # ---------------------------------------------------------------
        # Checklist
        # ---------------------------------------------------------------

        panel_open(
            f"Checklist — {client['name']} · {period}"
        )

        checklist = memory.get_checklist(
            client["id"],
            period,
        )

        if not checklist:
            st.caption(
                "No checklist yet for this client/period. "
                "Seed one above."
            )

        else:
            for item in checklist:
                cols = st.columns([3, 2, 2])
                with cols[0]:
                    st.write(
                        DOC_TYPE_LABELS.get(
                            item["doc_type"],
                            item["doc_type"],
                        )
                    )
                with cols[1]:
                    if item["status"] == "received":
                        st.markdown(
                            pill("✓ Received", "ok"),
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            pill("Missing", "warn"),
                            unsafe_allow_html=True,
                        )
                with cols[2]:
                    if item["status"] != "received":
                        if st.button(
                            "Mark received",
                            key=f"recv_{item['id']}",
                        ):
                            memory.mark_document_received(
                                client["id"],
                                period,
                                item["doc_type"],
                            )

                            st.rerun()
        panel_close()

    # ====================================================================
    # RUN REMINDERS TAB
    # ====================================================================

    with tab_run:

        panel_open("Run the reminder agent")

        st.caption(
            "Detects missing documents, drafts a personalized message "
            "per client, sends it, and logs it"
        )

        # ---------------------------------------------------------------
        # Show the shared client
        # ---------------------------------------------------------------

        st.write("Selected client")

        st.markdown(
            f"**{client['name']}**"
        )

        recipient_email = st.text_input(
            "Reminder will be sent to",
            value=client.get("email") or "",
            placeholder="client@example.com",
            help="Defaults to this client's email on file. Edit it to send "
                 "this run's reminder(s) to a different address instead -- "
                 "the client's stored record is not changed.",
            key=f"d2_recipient_email_{client['id']}",
        )
        if not recipient_email:
            st.caption("\u26a0 No email on file for this client -- enter one above or the send will fail.")

        run_col1, run_col2 = st.columns(2)

        # ---------------------------------------------------------------
        # Run for selected client
        # ---------------------------------------------------------------

        with run_col1:

            if st.button(
                "Run for this client",
                use_container_width=True,
                key="d2_run_client_button",
            ):

                started = time.time()

                result = demo2_orch.run_for_client(
                    client["id"],
                    period,
                    override_email=recipient_email or None,
                )

                _record_latency(
                    "demo-2-run",
                    started,
                )

                st.session_state["d2_last_run"] = {
                    "scope": "client",
                    "data": result,
                }

        # ---------------------------------------------------------------
        # Run for entire roster
        # ---------------------------------------------------------------

        with run_col2:

            if st.button(
                "Run for entire roster",
                use_container_width=True,
                key="d2_run_roster_button",
            ):

                roster_ids = [
                    c["id"]
                    for c in clients
                ]

                started = time.time()

                summary = demo2_orch.run_for_roster(
                    roster_ids,
                    period,
                )

                _record_latency(
                    "demo-2-run",
                    started,
                )

                st.session_state["d2_last_run"] = {
                    "scope": "roster",
                    "data": summary,
                }

        panel_close()

        # ---------------------------------------------------------------
        # Last run result
        # ---------------------------------------------------------------

        last_run = st.session_state.get(
            "d2_last_run"
        )

        if last_run:

            data = last_run["data"]

            # Individual client result

            if last_run["scope"] == "client":

                panel_open(
                    f"Reminders sent — "
                    f"{data['client_name']} · "
                    f"{data['period']}"
                )

                if data.get("reminders_sent", 0) > 0:
                    sent_to_display = data.get("recipient_email") or "\u2014"
                    st.caption(f"Sent to: {sent_to_display}")

                m1, m2, m3, m4 = st.columns(4)

                m1.metric(
                    "Missing documents",
                    data["missing_count"],
                )

                m2.metric(
                    "Reminders sent",
                    data["reminders_sent"],
                )

                m3.metric(
                    "Escalated (past follow-up cap)",
                    data.get("escalated_count", 0),
                )

                m4.metric(
                    "On cooldown (too soon)",
                    data.get("skipped_too_soon", 0),
                )

                if data.get("skipped_too_soon", 0) > 0:
                    st.caption(
                        f"{data['skipped_too_soon']} missing document(s) already have a reminder "
                        f"logged within the last {REMINDER_FOLLOWUP_INTERVAL_DAYS} day(s) "
                        f"\u2014 that's why nothing was sent. Use "
                        f"**Clear reminder history** on the Dashboard & history tab to reset and "
                        f"test again immediately."
                    )

                for r in data["reminders"]:

                    st.markdown(
                        pill(
                            DOC_TYPE_LABELS.get(
                                r["doc_type"],
                                r["doc_type"],
                            ),
                            "doctype",
                        )
                        + "&nbsp;"
                        + pill(
                            f"follow-up #{r['follow_up_number']}",
                            "neutral",
                        ),
                        unsafe_allow_html=True,
                    )

                    st.markdown(
                        f'<div class="letter-box">'
                        f'{r["message"]}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    st.write("")

                if data.get("escalated_count", 0) > 0:
                    st.caption(
                        f"{data['escalated_count']} missing document(s) already passed "
                        f"the follow-up cap (or failed to send) and were routed to the "
                        f"review queue instead of getting another reminder."
                    )

                panel_close()

            # ===========================================================
            # Roster result
            # ===========================================================

            else:

                panel_open(
                    f"Roster run — {data['period']}"
                )

                m1, m2, m3, m4 = st.columns(4)

                m1.metric(
                    "Clients processed",
                    data["clients_processed"],
                )

                m2.metric(
                    "Reminders sent",
                    data["total_reminders_sent"],
                )

                m3.metric(
                    "Escalated (past cap)",
                    data.get("total_escalated", 0),
                )

                m4.metric(
                    "Est. hours saved",
                    data["estimated_hours_saved"],
                )

                st.caption(
                    f"Based on ~"
                    f"{REMINDER_MANUAL_MINUTES_PER_DOC:.0f}"
                    f" manual minutes per document chased by hand."
                )

                roster_df = pd.DataFrame([
                    {
                        "Client": c["client_name"],
                        "Missing": c["missing_count"],
                        "Reminders sent": c["reminders_sent"],
                        "Escalated": c.get("escalated_count", 0),
                    }
                    for c in data["clients"]
                ])

                st.dataframe(
                    roster_df,
                    use_container_width=True,
                    hide_index=True,
                )

                panel_close()

    # ====================================================================
    # DASHBOARD & HISTORY TAB
    # ====================================================================

    with tab_dashboard:

        # Collection dashboard

        panel_open(
            f"Collection dashboard — {period}"
        )

        dash = memory.dashboard_status(period)

        if not dash["clients"]:

            st.caption(
                "No checklist data for this period yet."
            )

        else:

            dash_df = pd.DataFrame([
                {
                    "Client": c["client_name"],
                    "Expected": c["expected"],
                    "Received": c["received"],
                    "Missing": c["missing"],
                    "Reminders sent": c["reminders_sent"],
                }
                for c in dash["clients"]
            ])

            st.dataframe(
                dash_df,
                use_container_width=True,
                hide_index=True,
            )

        panel_close()

        # Reminder history

        panel_open("Reminder history")

        st.write("Selected client")

        st.markdown(
            f"**{client['name']}**"
        )

        if st.button(
            f"Clear reminder history for {period}",
            key="d2_clear_history",
        ):
            deleted = memory.clear_reminder_history(client["id"], period)
            st.success(f"Deleted {deleted} reminder(s) for {period}. Follow-up count resets to #1 on the next run.")
            st.rerun()

        history = memory.get_reminder_history(
            client["id"]
        )

        if not history:

            st.caption(
                "No reminders logged yet for this client."
            )

        else:

            for h in reversed(history):

                st.markdown(
                    pill(
                        h["channel"],
                        "neutral",
                    )
                    + "&nbsp;"
                    + pill(
                        f"follow-up #{h['follow_up_number']}",
                        "neutral",
                    )
                    + "&nbsp;"
                    + pill(
                        h["status"],
                        "ok"
                        if h["status"] == "sent"
                        else "warn",
                    ),
                    unsafe_allow_html=True,
                )

                st.caption(h["sent_at"])

                st.markdown(
                    f'<div class="letter-box">'
                    f'{h["message"]}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                st.write("")

        panel_close()


# ========================================================================
# DEMO 3 -- Advisory Report + Alerts
# ========================================================================

STATEMENT_FIELDS = [
    ("revenue", "Revenue"), ("cogs", "COGS"), ("operating_expenses", "Operating expenses"),
    ("net_income", "Net income (optional)"), ("current_assets", "Current assets"),
    ("inventory", "Inventory"), ("current_liabilities", "Current liabilities"),
    ("total_debt", "Total debt"), ("equity", "Equity"), ("accounts_receivable", "Accounts receivable"),
]


def _benchmark_inputs() -> Dict[str, float]:
    with st.expander("Industry benchmarks (optional override)"):
        b1, b2, b3 = st.columns(3)
        with b1:
            gm_bm = st.number_input("Gross margin % benchmark", value=40.0, key="d3_bm_gm")
            nm_bm = st.number_input("Net margin % benchmark", value=8.0, key="d3_bm_nm")
        with b2:
            cr_bm = st.number_input("Current ratio benchmark", value=1.5, key="d3_bm_cr")
            qr_bm = st.number_input("Quick ratio benchmark", value=1.0, key="d3_bm_qr")
        with b3:
            dso_bm = st.number_input("DSO days benchmark", value=45.0, key="d3_bm_dso")
            de_bm = st.number_input("Debt/equity benchmark", value=1.0, key="d3_bm_de")
    return {
        "gross_margin_pct": gm_bm, "net_margin_pct": nm_bm, "current_ratio": cr_bm,
        "quick_ratio": qr_bm, "dso_days": dso_bm, "debt_to_equity": de_bm,
    }


def _generate_one_report(
    client_id: str, period: str, statement: Dict[str, float], benchmarks: Dict[str, float],
    store_statement: bool = True,
) -> Optional[Dict[str, Any]]:
    started = time.time()
    try:
        return demo3_orch.generate_report(
            client_id=client_id, period=period, statement=statement, benchmarks=benchmarks,
            store_statement=store_statement,
        )
    except ValueError as e:
        st.error(f"{period}: {e}")
        return None
    finally:
        _record_latency("demo-3-generate", started)


def render_report_block(report: Dict[str, Any], client_id: str) -> None:
    left, right = st.columns([3, 2])

    with left:
        panel_open(f"Ratios \u2014 {report['period']}" + (" (vs. prior period)" if report["compared_to_prior"] else ""))
        ratios = report["ratios"]
        prior = report.get("prior_ratios")
        metric_cols = st.columns(3)
        metric_defs = [
            ("gross_margin_pct", "Gross margin", "%"), ("net_margin_pct", "Net margin", "%"),
            ("current_ratio", "Current ratio", ""), ("quick_ratio", "Quick ratio", ""),
            ("dso_days", "DSO", " days"), ("debt_to_equity", "Debt / equity", ""),
        ]
        for i, (key, label, unit) in enumerate(metric_defs):
            val = ratios.get(key)
            delta = None
            if prior and prior.get(key) is not None and val is not None:
                delta = round(val - prior[key], 2)
            with metric_cols[i % 3]:
                st.metric(label, f"{val}{unit}" if val is not None else "\u2014",
                          delta=f"{delta}{unit}" if delta is not None else None)
        panel_close()

        panel_open("Anomalies")
        anomalies = report.get("anomalies", [])
        if not anomalies:
            st.markdown(pill("\u2705 No anomalies detected", "ok"), unsafe_allow_html=True)
        for a in anomalies:
            kind = "err" if a["severity"] == "alert" else "warn"
            st.markdown(pill(a["severity"].upper(), kind) + f"&nbsp; {a['message']}", unsafe_allow_html=True)
        panel_close()

    with right:
        panel_open(f"Advisory letter (method: {report['narrative_method']})")
        st.markdown(f'<div class="letter-box">{report["letter_text"]}</div>', unsafe_allow_html=True)
        st.download_button(
            "Download letter (.txt)", data=report["letter_text"],
            file_name=f"advisory_letter_{client_id}_{report['period']}.txt",
            key=f"dl_{client_id}_{report['period']}_{report.get('report_id', '')}",
        )
        panel_close()


def render_demo3() -> None:
    st.title("Advisory Report + Alerts")
    st.caption("Raw statement in \u2192 ratios, anomalies vs. prior period/benchmark, and a plain-language letter in under a minute")
    render_banner()

    clients = client_options()
    mode = st.radio(
        "Input mode", ["Existing Client", "Upload Statements", "Manual Input"],
        horizontal=True, key="d3_mode",
    )

    # ---------------- Mode A: Existing Client -- load stored years ----------------
    if mode == "Existing Client":
        panel_open("Existing client \u2014 stored years")
        client = st.selectbox("Client", clients, format_func=client_label, key="d3_existing_client")
        statements = memory.list_financial_statements(client["id"])
        benchmarks = _benchmark_inputs()

        if not statements:
            st.caption(
                "No stored financial statements for this client yet. Run "
                "`python src/data/generate_client_roster.py` to pre-populate 2\u20133 fiscal years "
                "per client, or use Manual Input instead."
            )
        else:
            stmt_df = pd.DataFrame([
                {"Period": s["period"], **{label: s["data"].get(key, "\u2014") for key, label in STATEMENT_FIELDS}}
                for s in statements
            ])
            st.dataframe(stmt_df, use_container_width=True, hide_index=True)

            period_choice = st.selectbox(
                "Generate for period", [s["period"] for s in statements],
                index=len(statements) - 1, key="d3_existing_period",
            )
            if st.button("Generate advisory report", key="d3_gen_existing"):
                stmt = next(s["data"] for s in statements if s["period"] == period_choice)
                with st.spinner("Computing ratios, checking for anomalies, drafting the letter..."):
                    report = _generate_one_report(client["id"], period_choice, stmt, benchmarks, store_statement=False)
                if report:
                    st.session_state["d3_reports"] = [report]
        panel_close()
        history_client = client

    # ---------------- Mode B: Upload Statements -- multi-year CSV ----------------
    elif mode == "Upload Statements":
        panel_open("Upload a multi-year CSV")
        st.caption(
            "One row per fiscal year: a `period` column plus any of "
            + ", ".join(k for k, _ in STATEMENT_FIELDS) + " as columns."
        )
        client = st.selectbox("Client", clients, format_func=client_label, key="d3_upload_client")
        benchmarks = _benchmark_inputs()
        csv_file = st.file_uploader("Statement CSV", type=["csv"], key="d3_csv")

        if st.button("Upload & generate report(s)", disabled=csv_file is None, key="d3_gen_upload"):
            try:
                df = pd.read_csv(csv_file)
            except Exception as e:
                st.error(f"Could not parse CSV: {e}")
                df = None
            if df is not None and "period" not in df.columns:
                st.error("CSV must include a 'period' column (e.g. 2024, 2025, 2026).")
            elif df is not None:
                statement_keys = {k for k, _ in STATEMENT_FIELDS}
                generated: List[Dict[str, Any]] = []
                with st.spinner(f"Generating {len(df)} report(s)..."):
                    for _, row in df.iterrows():
                        stmt = {
                            k: float(row[k]) for k in statement_keys
                            if k in df.columns and pd.notna(row[k])
                        }
                        report = _generate_one_report(client["id"], str(row["period"]), stmt, benchmarks)
                        if report:
                            generated.append(report)
                if generated:
                    st.session_state["d3_reports"] = generated
                    st.success(f"Generated {len(generated)} report(s).")
        panel_close()
        history_client = client

    # ---------------- Mode C: Manual Input ----------------
    else:
        panel_open("Manual statement entry")
        c1, c2 = st.columns([1, 1])
        with c1:
            client = st.selectbox("Client", clients, format_func=client_label, key="d3_manual_client")
        with c2:
            period = render_period_picker("d3_manual", default_freq="Quarterly")
        st.caption("Figures for the current period. Leave a field at 0 if not applicable.")
        values: Dict[str, float] = {}
        cols = st.columns(3)
        for i, (key, label) in enumerate(STATEMENT_FIELDS):
            with cols[i % 3]:
                values[key] = st.number_input(label, min_value=0.0, step=1000.0, value=0.0, key=f"d3_manual_{key}")

        benchmarks = _benchmark_inputs()

        if st.button("Generate report", key="d3_gen_manual"):
            statement = {k: v for k, v in values.items() if v}
            with st.spinner("Computing ratios, checking for anomalies, drafting the letter..."):
                report = _generate_one_report(client["id"], period, statement, benchmarks)
            if report:
                st.session_state["d3_reports"] = [report]
        panel_close()
        history_client = client

    reports = st.session_state.get("d3_reports") or []
    if reports:
        st.markdown("---")
        for report in reports:
            st.markdown(f"#### {report['client_name']} \u2014 {report['period']}")
            render_report_block(report, history_client["id"])

    panel_open(f"Report history \u2014 {history_client['name']}")
    past_reports = memory.get_reports(history_client["id"])
    if not past_reports:
        st.caption("No reports generated yet for this client.")
    else:
        for r in past_reports:
            with st.expander(f"{r['period']} \u00b7 {r['status']} \u00b7 {r['generated_at'][:10]}"):
                n_alerts = sum(1 for a in r["anomalies"] if a["severity"] == "alert")
                n_warn = sum(1 for a in r["anomalies"] if a["severity"] == "warning")
                st.markdown(
                    pill(f"{n_alerts} alert(s)", "err" if n_alerts else "ok") + "&nbsp;" +
                    pill(f"{n_warn} warning(s)", "warn" if n_warn else "ok"),
                    unsafe_allow_html=True,
                )
                st.markdown(f'<div class="letter-box">{r["letter_text"]}</div>', unsafe_allow_html=True)
                if st.button("\U0001f5d1 Delete this report", key=f"del_report_{r['id']}"):
                    memory.delete_report(r["id"])
                    st.rerun()
    panel_close()


# ========================================================================
# METRICS -- the "wow, measured" numbers (React's Dashboard.tsx equivalent)
# ========================================================================

def render_metrics() -> None:
    st.title("Live Metrics")
    render_banner()

    from src.database.database import Document, SupplierPattern, session_scope

    with session_scope() as s:
        documents = s.query(Document).all()
        total_docs = len(documents)
        accounted = sum(1 for d in documents if d.status == "accounted")
        confidences = [d.classification_confidence for d in documents if d.classification_confidence is not None]
        patterns = s.query(SupplierPattern).all()
        recognized_suppliers = sum(1 for p in patterns if p.seen_count >= 2)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Documents processed", total_docs)
    m2.metric("Accounted without review",
              f"{round(100 * accounted / total_docs, 1)}%" if total_docs else "\u2014")
    avg_conf = _avg(confidences)
    m3.metric("Avg. classification confidence", f"{avg_conf:.2f}" if avg_conf is not None else "\u2014")
    m4.metric("Recurring suppliers learned (2nd+ sighting)", recognized_suppliers)

    panel_open("On-board inference latency (ms, observed this session)")
    latency = get_latency_store()
    rows = [
        {
            "Call": key,
            "Avg (ms)": f"{_avg(values):.1f}" if values else "\u2014",
            "Last (ms)": f"{values[-1]:.0f}" if values else "\u2014",
            "Samples": len(values),
        }
        for key, values in latency.items()
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("Resets when this process restarts \u2014 displays this server's own observed timings")
    panel_close()

    panel_open("Open review-queue items (shared across all 3 demos)")
    review = memory.list_review_queue()
    if not review:
        st.markdown(pill("\u2705 Queue is empty", "ok"), unsafe_allow_html=True)
    else:
        hdr = st.columns([1, 1.5, 1, 4, 1.3])
        for col, label in zip(hdr, ["Demo", "Type", "Ref ID", "Reason", ""]):
            col.markdown(f'<span class="subtext">{label}</span>', unsafe_allow_html=True)
        st.markdown('<hr style="margin:4px 0 10px;">', unsafe_allow_html=True)

        for item in review:
            cols = st.columns([1, 1.5, 1, 4, 1.3])
            cols[0].write(item["demo"])
            cols[1].write(item["ref_type"])
            cols[2].write(item["ref_id"])
            cols[3].write(item["reason"])
            with cols[4]:
                if st.button("Resolve", key=f"resolve_{item['id']}", use_container_width=True):
                    memory.resolve_review_item(item["id"])
                    st.rerun()
    panel_close()


dashboard_page = st.Page(
    render_metrics,
    title="Dashboard",
    icon=":material/dashboard:",
    url_path="",
    default=True,
)

demo1_page = st.Page(
    render_demo1,
    title="Doc-to-Data",
    icon=":material/description:",
    url_path="doc-to-data",
)

demo2_page = st.Page(
    render_demo2,
    title="Client Reminders",
    icon=":material/notifications:",
    url_path="client-reminders",
)

demo3_page = st.Page(
    render_demo3,
    title="Advisory Reports",
    icon=":material/analytics:",
    url_path="advisory-reports",
)

pg = st.navigation(
    [
        dashboard_page,
        demo1_page,
        demo2_page,
        demo3_page,
    ],
    position="hidden",
)


# ========================================================================
# Main
# ========================================================================

def main() -> None:
    render_sidebar()
    pg.run()


if __name__ == "__main__":
    main()