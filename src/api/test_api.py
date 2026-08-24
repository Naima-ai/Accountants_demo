"""
test_api.py

A testing/dev API for Demo 1 -- separate from api.py (Harith's real
endpoint contract) so this can be iterated on freely without stepping
on his file. Exposes each pipeline stage as its own route, plus a
one-click route to run the whole sample dataset, so the full chain
(ingest -> classify -> extract -> validate against the Chart of
Accounts -> learn recurring suppliers -> push bookkeeping -> human
review only for low-confidence lines) can be exercised from Swagger UI
(/docs) without writing any client code.

Included into the app in main.py:
    from src.api.test_api import router as test_router
    app.include_router(test_router)

Routes:
    POST /api/test/ingest-samples   -- run all 53 data_set/samples/ files through the full chain
    POST /api/test/ingestion        -- upload one file, run ONLY ingestion.py
    POST /api/test/classify         -- upload one file, run ingestion -> classifier.py
    POST /api/test/extract          -- upload one file, run ingestion -> classifier -> extractor.py
    POST /api/test/validate         -- upload one file, run ingestion -> classifier -> extractor -> validator.py
    POST /api/test/learn-supplier   -- upload one file, run the full chain and show memory.py's
                                        before/after supplier hint (the auto-learn mechanism)
    POST /api/test/bookkeeping      -- upload one file, run the FULL chain end to end and push
                                        the journal entry via Demo1Orchestrator (memory/database)
    GET  /api/test/review-queue     -- items currently flagged for human review (low-confidence lines)
    GET  /api/test/samples          -- lists the sample files available under data_set/samples/
"""

import glob
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.ingestion.ingestion import IngestionPipeline
from src.classifier.classifier import DocumentClassifier
from src.extraction.extractor import FieldExtractor
from src.validation.validator import Validator
from src.agents.accounting_agent import AccountingAgent
from src.memory.memory import MemoryStore
from src.orchestration.demo1_orchestrator import Demo1Orchestrator

router = APIRouter(
    prefix="/api/test",
    tags=["Demo 1 -- Test/Dev"],
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SAMPLES_DIR = os.path.join(_REPO_ROOT, "data_set", "samples")
_UPLOAD_DIR = os.path.join(_REPO_ROOT, "var", "test_uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)

_pipeline = IngestionPipeline()
_classifier = DocumentClassifier()
_extractor = FieldExtractor()
_validator = Validator()
_accounting_agent = AccountingAgent()
_memory = MemoryStore()
_orchestrator = Demo1Orchestrator(memory=_memory)


class SeedResponse(BaseModel):
    total: int
    ready_to_post: int
    needs_review: int
    errors: int
    by_group: Dict[str, Dict[str, int]]


class ReviewQueueItem(BaseModel):
    id: int
    demo: str
    ref_type: str
    ref_id: str
    reason: str
    status: str
    created_at: str


async def _save_upload(file: UploadFile) -> str:
    """Writes the uploaded file to var/test_uploads/ and returns its path."""
    suffix = os.path.splitext(file.filename or "")[1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=_UPLOAD_DIR)
    with os.fdopen(fd, "wb") as out:
        shutil.copyfileobj(file.file, out)
    return tmp_path


# ----------------------------------------------------------------------
# One-click: run the full 53-sample dataset through the full chain
# ----------------------------------------------------------------------

@router.post("/ingest-samples", response_model=SeedResponse)
async def ingest_samples(client_id: str = "c-001"):
    """
    Runs every file under data_set/samples/ (10 XML, 10 native PDF, 10
    scanned PDF, 10 text, ~13 real images) through the full Demo 1 chain
    -- ingest -> classify -> extract -> validate -> learn -> bookkeep --
    and persists everything. This is the "click ingest, run all 53
    samples" button.
    """
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
                result = _orchestrator.process_file(file_path, client_id=client_id)
                status = result.get("status")
                if status == "accounted":
                    stats["ready_to_post"] += 1
                elif status == "needs_review":
                    stats["needs_review"] += 1
                else:
                    stats["errors"] += 1
            except Exception:
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

    return SeedResponse(total=total, ready_to_post=ready, needs_review=review, errors=errors, by_group=by_group)


@router.get("/samples")
async def list_samples() -> Dict[str, List[str]]:
    """Lists the sample files available under data_set/samples/, grouped by format."""
    if not os.path.isdir(_SAMPLES_DIR):
        return {}
    result = {}
    for subfolder in sorted(os.listdir(_SAMPLES_DIR)):
        folder = os.path.join(_SAMPLES_DIR, subfolder)
        if os.path.isdir(folder):
            result[subfolder] = sorted(os.listdir(folder))
    return result


# ----------------------------------------------------------------------
# Per-stage routes -- each uploads one file and runs the chain up to
# (and including) that stage, so every module is independently testable
# from Swagger UI.
# ----------------------------------------------------------------------

@router.post("/ingestion")
async def test_ingestion(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Runs ONLY ingestion.py on the uploaded file."""
    path = await _save_upload(file)
    try:
        doc = _pipeline.ingest_file(path)
        return {
            "doc_id": doc.doc_id,
            "success": doc.success,
            "file_type": doc.file_type.value if hasattr(doc.file_type, "value") else doc.file_type,
            "error": doc.error,
            "warnings": doc.warnings,
            "text_preview": doc.full_text[:500],
            "metadata": doc.metadata,
        }
    finally:
        os.remove(path)


@router.post("/classify")
async def test_classify(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Runs ingestion.py -> classifier.py on the uploaded file."""
    path = await _save_upload(file)
    try:
        doc = _pipeline.ingest_file(path)
        if not doc.success:
            raise HTTPException(status_code=422, detail=f"Ingestion failed: {doc.error}")
        classification = _classifier.classify(doc.to_classifier_input())
        return classification.model_dump()
    finally:
        os.remove(path)


@router.post("/extract")
async def test_extract(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Runs ingestion.py -> classifier.py -> extractor.py on the uploaded file."""
    path = await _save_upload(file)
    try:
        doc = _pipeline.ingest_file(path)
        if not doc.success:
            raise HTTPException(status_code=422, detail=f"Ingestion failed: {doc.error}")
        ci = doc.to_classifier_input()
        classification = _classifier.classify(ci)
        fields = _extractor.extract(ci, classification.document_type)
        return {"classification": classification.model_dump(), "extraction": fields.model_dump()}
    finally:
        os.remove(path)


@router.post("/validate")
async def test_validate(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Runs ingestion -> classifier -> extractor -> validator.py (against the Chart of Accounts)."""
    path = await _save_upload(file)
    try:
        doc = _pipeline.ingest_file(path)
        if not doc.success:
            raise HTTPException(status_code=422, detail=f"Ingestion failed: {doc.error}")
        ci = doc.to_classifier_input()
        classification = _classifier.classify(ci)
        fields = _extractor.extract(ci, classification.document_type)
        validation = _validator.validate(fields)
        return {"extraction": fields.model_dump(), "validation": validation.model_dump()}
    finally:
        os.remove(path)


@router.post("/learn-supplier")
async def test_learn_supplier(file: UploadFile = File(...), client_id: str = "c-001") -> Dict[str, Any]:
    """
    Runs the full chain, then calls memory.py's supplier auto-learn.
    Returns the supplier hint BEFORE and AFTER this run, so calling it
    twice on the same supplier's documents shows the "recognized on the
    2nd invoice" effect from the brief.
    """
    path = await _save_upload(file)
    try:
        doc = _pipeline.ingest_file(path)
        if not doc.success:
            raise HTTPException(status_code=422, detail=f"Ingestion failed: {doc.error}")
        ci = doc.to_classifier_input()
        classification = _classifier.classify(ci)
        fields = _extractor.extract(ci, classification.document_type)
        validation = _validator.validate(fields)
        entry = _accounting_agent.build_journal_entry(fields, validation)

        hint_before = (
            _memory.get_supplier_hint(client_id, fields.supplier_name) if fields.supplier_name else None
        )

        learned = False
        if entry.status == "ready_to_post" and fields.supplier_name and entry.lines:
            primary_line = max((l for l in entry.lines if l.debit), key=lambda l: l.debit, default=None)
            if primary_line:
                _memory.learn_supplier(
                    client_id, fields.supplier_name, fields.supplier_vat,
                    primary_line.account_code, primary_line.account_name,
                )
                learned = True

        hint_after = (
            _memory.get_supplier_hint(client_id, fields.supplier_name) if fields.supplier_name else None
        )

        return {
            "supplier_name": fields.supplier_name,
            "journal_entry_status": entry.status,
            "learned_this_run": learned,
            "supplier_hint_before": hint_before,
            "supplier_hint_after": hint_after,
        }
    finally:
        os.remove(path)


@router.post("/bookkeeping")
async def test_bookkeeping(file: UploadFile = File(...), client_id: str = "c-001") -> Dict[str, Any]:
    """
    The full pipeline in one call: ingest -> classify -> extract ->
    validate against the Chart of Accounts -> learn recurring suppliers
    -> push the journal entry (memory/database) -> flagged for human
    review only if confidence is low. This is Demo1Orchestrator end to
    end, the same chain /ingest-samples runs in bulk.
    """
    path = await _save_upload(file)
    try:
        result = _orchestrator.process_file(path, client_id=client_id)
        return result
    finally:
        os.remove(path)


# ----------------------------------------------------------------------
# Human review queue
# ----------------------------------------------------------------------

@router.get("/review-queue", response_model=List[ReviewQueueItem])
async def get_review_queue(status: str = "open"):
    """Documents currently needing human review (low-confidence lines)."""
    return _memory.list_review_queue(demo="demo_1", status=status)
