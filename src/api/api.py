"""
api.py

The single HTTP surface for all three demos -- previously split across
api.py (a hardcoded, never-wired stub), reporting_api.py (Demo 3) and
test_api.py (Demo 1 dev routes), with Demo 2 (the reminder agent) never
exposed over HTTP at all despite demo2_orchestrator.py fully
implementing it. Consolidated here so there is one file, one router,
and one contract to integrate against -- matching the brief's "plug &
play via API" principle (src/main.py mounts this one router).

Routes:
    Demo 1 -- Sovereign Doc-to-Data (Demo1Orchestrator)
        POST /api/demo-1/process          -- upload one file, run the full chain
        POST /api/demo-1/ingest-samples   -- run every file under data_set/samples/
        POST /api/demo-1/pipeline/{stage} -- upload one file, run up to one stage
                                              (stage: ingestion|classify|extract|validate)
        GET  /api/demo-1/samples          -- lists data_set/samples/ contents

    Demo 2 -- Reminder Agent & Document Collection (ReminderOrchestrator)
        POST /api/demo-2/seed             -- seed a client's expected-document checklist
        POST /api/demo-2/run-roster       -- the "run on N clients" wow button
        GET  /api/demo-2/dashboard/{period} -- per-client missing/received/reminders rollup

    Demo 3 -- Advisory Report + Alerts (ReportOrchestrator)
        POST /api/demo-3/generate         -- generate (and persist) an advisory report
        GET  /api/demo-3/reports/{client_id} -- every previously generated report

    Shared
        GET  /api/clients, POST /api/clients
        GET  /api/review-queue            -- human-review queue, all demos
        GET  /api/metrics                 -- the brief's "wow effect, measured" numbers
"""

import glob
import logging
import os
import shutil
import tempfile
import time
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.ingestion.ingestion import IngestionPipeline
from src.classifier.classifier import DocumentClassifier
from src.extraction.extractor import FieldExtractor
from src.validation.validator import Validator
from src.agents.accounting_agent import AccountingAgent
from src.memory.memory import MemoryStore
from src.orchestration.demo1_orchestrator import Demo1Orchestrator
from src.orchestration.demo2_orchestrator import ReminderOrchestrator
from src.orchestration.demo3_orchestrator import ReportOrchestrator
from src.database.database import Document, SupplierPattern, session_scope

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SAMPLES_DIR = os.path.join(_REPO_ROOT, "data_set", "samples")
_UPLOAD_DIR = os.path.join(_REPO_ROOT, "var", "uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)

_pipeline = IngestionPipeline()
_classifier = DocumentClassifier()
_extractor = FieldExtractor()
_validator = Validator()
_accounting_agent = AccountingAgent()
_memory = MemoryStore()
_demo1 = Demo1Orchestrator(memory=_memory)
_demo2 = ReminderOrchestrator(memory=_memory)
_demo3 = ReportOrchestrator(memory=_memory)


async def _save_upload(file: UploadFile) -> str:
    """Writes the uploaded file to var/uploads/ and returns its path."""
    suffix = os.path.splitext(file.filename or "")[1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=_UPLOAD_DIR)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(file.file, out)
    return tmp_path


# ----------------------------------------------------------------------
# In-process latency tracking for GET /api/metrics -- a simple rolling
# window keyed by call site, reset on process restart. No new DB tables:
# this is "how fast were the calls this server actually served",
# exactly what the brief's "on-board inference latency" slide wants
# visible, not a fabricated number.
# ----------------------------------------------------------------------

_metrics_lock = threading.Lock()
_latencies_ms: Dict[str, List[float]] = {"demo-1-process": [], "demo-2-run-roster": [], "demo-3-generate": []}
_LATENCY_WINDOW = 200


def _record_latency(key: str, started_at: float) -> None:
    elapsed_ms = (time.time() - started_at) * 1000
    with _metrics_lock:
        bucket = _latencies_ms[key]
        bucket.append(elapsed_ms)
        if len(bucket) > _LATENCY_WINDOW:
            del bucket[: len(bucket) - _LATENCY_WINDOW]


# ========================================================================
# Demo 1 -- Sovereign Doc-to-Data
# ========================================================================

class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit_price: Optional[str] = None
    total: Optional[str] = None
    vat_rate: Optional[str] = None


class ProcessResponse(BaseModel):
    doc_id: str
    status: str
    file: str
    classification: Optional[Dict[str, Any]] = None
    extraction: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None
    journal_entry: Optional[Dict[str, Any]] = None
    supplier_hint: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SeedSamplesResponse(BaseModel):
    total: int
    ready_to_post: int
    needs_review: int
    errors: int
    by_group: Dict[str, Dict[str, int]]


@router.post("/demo-1/process", response_model=ProcessResponse)
async def demo1_process(file: UploadFile = File(...), client_id: str = "c-001"):
    """Uploads one document and runs the full Demo 1 chain (ingest ->
    classify -> extract -> validate -> learn -> bookkeep) via
    Demo1Orchestrator -- the "drop a chaotic folder in, get clean
    entries out" wow moment from the brief, for a single file."""
    path = await _save_upload(file)
    started = time.time()
    try:
        result = _demo1.process_file(path, client_id=client_id)
        return result
    finally:
        _record_latency("demo-1-process", started)
        os.remove(path)


@router.post("/demo-1/ingest-samples", response_model=SeedSamplesResponse)
async def demo1_ingest_samples(client_id: str = "c-001"):
    """Runs every file under data_set/samples/ (XML, native PDF, scanned
    PDF, text, real images) through the full Demo 1 chain and persists
    everything -- the "click ingest, run all samples" demo button."""
    groups = [
        ("xml", "*.xml"), ("pdf", "*.pdf"), ("pdf_scanned", "*.pdf"),
        ("text", "*.txt"), ("images", "*.png"),
    ]
    by_group: Dict[str, Dict[str, int]] = {}
    total = ready = review = errors = 0

    for subfolder, pattern in groups:
        files = sorted(glob.glob(os.path.join(_SAMPLES_DIR, subfolder, pattern)))
        stats = {"total": 0, "ready_to_post": 0, "needs_review": 0, "errors": 0}
        for file_path in files:
            stats["total"] += 1
            try:
                result = _demo1.process_file(file_path, client_id=client_id)
                status = result.get("status")
                if status == "accounted":
                    stats["ready_to_post"] += 1
                elif status == "needs_review":
                    stats["needs_review"] += 1
                else:
                    stats["errors"] += 1
            except Exception:
                logger.exception(f"Unhandled error processing {file_path}")
                stats["errors"] += 1
        by_group[subfolder] = stats
        total += stats["total"]
        ready += stats["ready_to_post"]
        review += stats["needs_review"]
        errors += stats["errors"]

    if total == 0:
        raise HTTPException(
            status_code=404,
            detail="No sample files found under data_set/samples/. "
                   "Run `python src/data/generate_synthetic_samples.py` and "
                   "`python src/data/download_real_samples.py` first.",
        )

    return SeedSamplesResponse(total=total, ready_to_post=ready, needs_review=review, errors=errors, by_group=by_group)


@router.get("/demo-1/samples")
async def demo1_list_samples() -> Dict[str, List[str]]:
    """Lists the sample files available under data_set/samples/, grouped by format."""
    if not os.path.isdir(_SAMPLES_DIR):
        return {}
    result = {}
    for subfolder in sorted(os.listdir(_SAMPLES_DIR)):
        folder = os.path.join(_SAMPLES_DIR, subfolder)
        if os.path.isdir(folder):
            result[subfolder] = sorted(os.listdir(folder))
    return result


@router.post("/demo-1/pipeline/{stage}")
async def demo1_pipeline_stage(stage: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """Uploads one file and runs the chain up to (and including) `stage`
    -- ingestion|classify|extract|validate -- so the UI's before/after
    panel can show each intermediate step, not just the final result."""
    if stage not in ("ingestion", "classify", "extract", "validate"):
        raise HTTPException(status_code=404, detail=f"Unknown stage '{stage}'. Use one of: ingestion, classify, extract, validate.")

    path = await _save_upload(file)
    try:
        doc = _pipeline.ingest_file(path)
        if stage == "ingestion":
            return {
                "doc_id": doc.doc_id,
                "success": doc.success,
                "file_type": doc.file_type.value if hasattr(doc.file_type, "value") else doc.file_type,
                "error": doc.error,
                "warnings": doc.warnings,
                "text_preview": doc.full_text[:500],
                "metadata": doc.metadata,
            }
        if not doc.success:
            raise HTTPException(status_code=422, detail=f"Ingestion failed: {doc.error}")

        ci = doc.to_classifier_input()
        classification = _classifier.classify(ci)
        if stage == "classify":
            return classification.model_dump()

        fields = _extractor.extract(ci, classification.document_type)
        if stage == "extract":
            return {"classification": classification.model_dump(), "extraction": fields.model_dump()}

        validation = _validator.validate(fields)
        return {"extraction": fields.model_dump(), "validation": validation.model_dump()}
    finally:
        os.remove(path)


# ========================================================================
# Demo 2 -- Reminder Agent & Document Collection
# ========================================================================

class SeedChecklistRequest(BaseModel):
    client_id: str
    client_name: str
    period: str
    doc_types: List[str]
    preferred_tone: Optional[str] = "formal"


class RunRosterRequest(BaseModel):
    client_ids: List[str]
    period: str


@router.post("/demo-2/seed")
async def demo2_seed(request: SeedChecklistRequest) -> Dict[str, Any]:
    """Registers a client (if new) and seeds its expected-document
    checklist for a period -- demo/dev setup so the roster has
    something to chase before /run-roster is called."""
    _memory.upsert_client(request.client_id, request.client_name, preferred_tone=request.preferred_tone)
    _memory.seed_expected_documents(request.client_id, request.period, request.doc_types)
    return {"client_id": request.client_id, "period": request.period, "checklist": _memory.get_checklist(request.client_id, request.period)}


@router.post("/demo-2/run-roster")
async def demo2_run_roster(request: RunRosterRequest) -> Dict[str, Any]:
    """The "'run' on 50 clients" wow button: cross-checks every client's
    checklist, drafts + sends reminders for anything missing, and
    reports the hours saved."""
    started = time.time()
    try:
        return _demo2.run_for_roster(request.client_ids, request.period)
    finally:
        _record_latency("demo-2-run-roster", started)


@router.get("/demo-2/dashboard/{period}")
async def demo2_dashboard(period: str) -> Dict[str, Any]:
    """Per-client missing/received/reminders-sent rollup for a period."""
    return _memory.dashboard_status(period)


# ========================================================================
# Demo 3 -- Advisory Report + Alerts
# ========================================================================

class StatementInput(BaseModel):
    revenue: Optional[float] = None
    cogs: Optional[float] = None
    operating_expenses: Optional[float] = None
    net_income: Optional[float] = None
    current_assets: Optional[float] = None
    inventory: Optional[float] = None
    current_liabilities: Optional[float] = None
    total_debt: Optional[float] = None
    equity: Optional[float] = None
    accounts_receivable: Optional[float] = None


class GenerateReportRequest(BaseModel):
    client_id: str
    period: str = Field(..., description="e.g. '2026-Q2'")
    statement: StatementInput
    benchmarks: Optional[Dict[str, float]] = None


class RatioSetResponse(BaseModel):
    revenue: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    dso_days: Optional[float] = None
    debt_to_equity: Optional[float] = None


class AnomalyResponse(BaseModel):
    metric: str
    severity: str
    message: str
    current_value: Optional[float] = None
    reference_value: Optional[float] = None
    reference_type: str


class GenerateReportResponse(BaseModel):
    report_id: int
    client_name: str
    period: str
    ratios: RatioSetResponse
    prior_ratios: Optional[RatioSetResponse] = None
    anomalies: List[AnomalyResponse]
    narrative_method: str
    letter_text: str
    compared_to_prior: bool
    generated_at: str


@router.post("/demo-3/generate", response_model=GenerateReportResponse)
async def demo3_generate_report(request: GenerateReportRequest):
    """Runs the Demo 3 pipeline: fetch prior period from memory (if any)
    -> compute ratios -> detect anomalies -> generate the advisory
    letter -> persist -> return."""
    started = time.time()
    try:
        result = _demo3.generate_report(
            client_id=request.client_id,
            period=request.period,
            statement=request.statement.model_dump(exclude_none=True),
            benchmarks=request.benchmarks,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        _record_latency("demo-3-generate", started)
    return result


@router.get("/demo-3/reports/{client_id}")
async def demo3_list_reports(client_id: str) -> List[Dict[str, Any]]:
    """Every previously generated report for a client, most recent first."""
    return _demo3.memory.get_reports(client_id)


# ========================================================================
# Shared -- clients, review queue, metrics
# ========================================================================

class ClientRequest(BaseModel):
    client_id: str
    name: str
    vat_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    preferred_tone: Optional[str] = "formal"


@router.get("/clients")
async def list_clients() -> List[Dict[str, Any]]:
    return _memory.list_clients()


@router.post("/clients")
async def upsert_client(request: ClientRequest) -> Dict[str, Any]:
    _memory.upsert_client(
        request.client_id, request.name, vat_number=request.vat_number,
        email=request.email, phone=request.phone, preferred_tone=request.preferred_tone,
    )
    return _memory.get_client(request.client_id)


@router.get("/review-queue")
async def review_queue(demo: Optional[str] = None, status: str = "open") -> List[Dict[str, Any]]:
    """Items currently needing human review (low-confidence lines),
    across all three demos or filtered to one via `demo`
    (demo_1|demo_2|demo_3)."""
    return _memory.list_review_queue(demo=demo, status=status)


@router.get("/metrics")
async def metrics() -> Dict[str, Any]:
    """The brief's "wow effect, measured" numbers: extraction accuracy
    proxy, on-board latency actually observed by this server, 0-byte
    data egress, and the per-client learning curve (recurring suppliers
    recognized after a 2nd+ sighting)."""
    with session_scope() as s:
        documents = s.query(Document).all()
        total_docs = len(documents)
        accounted = sum(1 for d in documents if d.status == "accounted")
        confidences = [d.classification_confidence for d in documents if d.classification_confidence is not None]

        patterns = s.query(SupplierPattern).all()
        recognized_suppliers = sum(1 for p in patterns if p.seen_count >= 2)

    with _metrics_lock:
        latency_snapshot = {k: list(v) for k, v in _latencies_ms.items()}

    def _avg(values: List[float]) -> Optional[float]:
        return round(sum(values) / len(values), 1) if values else None

    return {
        "documents_processed": total_docs,
        "accounted_without_review_pct": round(100 * accounted / total_docs, 1) if total_docs else None,
        "avg_classification_confidence": _avg(confidences),
        "review_queue_open": len(_memory.list_review_queue(status="open")),
        "recurring_suppliers_learned": recognized_suppliers,
        "data_egress_bytes": 0,
        "latency_ms": {
            key: {"avg": _avg(values), "count": len(values), "last": (values[-1] if values else None)}
            for key, values in latency_snapshot.items()
        },
    }
