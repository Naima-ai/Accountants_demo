"""
demo1_orchestrator.py

Chains the Demo 1 pipeline (ingestion -> classifier -> extractor ->
validator -> accounting_agent) together with the shared memory/database
layer, and returns one result object the API layer or a CLI can hand
back as-is.

This is the piece that turns "five separate modules" into "drop a
folder in, get clean journal entries out" -- the Demo 1 wow moment
from the brief.

Usage:
    from src.orchestration.demo1_orchestrator import Demo1Orchestrator

    orch = Demo1Orchestrator()
    result = orch.process_file("data_set/IT01234567890_FPR01.xml", client_id="c-001")
    summary = orch.process_folder("data_set/samples/xml", client_id="c-001")
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("demo1_orchestrator")
logging.basicConfig(level=logging.INFO)

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/orchestration/demo1_orchestrator.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.memory.memory import MemoryStore
from src.ingestion.ingestion import IngestionPipeline
from src.classifier.classifier import DocumentClassifier
from src.extraction.extractor import FieldExtractor
from src.validation.validator import Validator
from src.agents.accounting_agent import AccountingAgent


class Demo1Orchestrator:
    """Runs the full Demo 1 chain and persists every step through MemoryStore."""

    def __init__(self, memory: Optional[MemoryStore] = None):
        self.pipeline = IngestionPipeline()
        self.classifier = DocumentClassifier()
        self.extractor = FieldExtractor()
        self.validator = Validator()
        self.accounting_agent = AccountingAgent()
        self.memory = memory or MemoryStore()

    def process_file(self, file_path: str, client_id: Optional[str] = None, doc_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs one document through the entire Demo 1 chain and persists
        the result. Never raises for a single bad document -- a failure
        at any stage comes back as needs_review=True with the reason in
        `notes`/`error`, so process_folder() can keep going.

        `doc_id`: if the file is already registered in the DB (e.g. a
        document generated/uploaded earlier and now being "Run" from
        the UI), pass its existing id so this updates that same row
        instead of creating a duplicate -- every persistence call below
        keys off doc.doc_id, so overwriting it here is enough.
        """
        doc = self.pipeline.ingest_file(file_path)
        if doc_id is not None:
            doc.doc_id = doc_id

        if not doc.success:
            self.memory.record_document(
                doc_id=doc.doc_id, original_filename=doc.original_filename,
                source_path=file_path, client_id=client_id, file_type=doc.file_type.value,
                status="failed", needs_review=True,
            )
            self.memory.flag_for_review("demo_1", "document", doc.doc_id, f"Ingestion failed: {doc.error}")
            return self._error_result(doc.doc_id, file_path, f"Ingestion failed: {doc.error}")

        classifier_input = doc.to_classifier_input()
        classification = self.classifier.classify(classifier_input)

        fields = self.extractor.extract(classifier_input, classification.document_type)

        # Apply learned supplier memory: if we've seen this supplier for
        # this client before with a confident category, that's a strong
        # signal worth surfacing even though validator.py does its own
        # keyword/model categorization independently. Exposed on the
        # result so the UI can show "recognized recurring supplier".
        supplier_hint = None
        if fields.supplier_name:
            supplier_hint = self.memory.get_supplier_hint(client_id, fields.supplier_name)

        validation = self.validator.validate(fields)
        entry = self.accounting_agent.build_journal_entry(fields, validation)

        needs_review = entry.status != "ready_to_post"
        status = "accounted" if not needs_review else "needs_review"

        self.memory.record_document(
            doc_id=doc.doc_id, original_filename=doc.original_filename, source_path=file_path,
            client_id=client_id, file_type=doc.file_type.value,
            classification=classification.document_type.value,
            classification_confidence=classification.confidence,
            status=status, needs_review=needs_review,
            extracted_fields=fields.model_dump(),
        )
        self.memory.record_journal_entry(doc.doc_id, entry.model_dump())

        if not needs_review and fields.supplier_name and entry.lines:
            # Reinforce the pattern only once the entry is clean --
            # don't teach the memory from a shaky extraction.
            primary_line = max((l for l in entry.lines if l.debit), key=lambda l: l.debit, default=None)
            if primary_line:
                self.memory.learn_supplier(
                    client_id, fields.supplier_name, fields.supplier_vat,
                    primary_line.account_code, primary_line.account_name,
                )

        if needs_review:
            reason = "; ".join(i.message for i in validation.issues if i.severity == "error") or "Low confidence output"
            self.memory.flag_for_review("demo_1", "document", doc.doc_id, reason)

        return {
            "doc_id": doc.doc_id,
            "status": status,
            "file": file_path,
            "classification": classification.model_dump(),
            "extraction": fields.model_dump(),
            "validation": validation.model_dump(),
            "journal_entry": entry.model_dump(),
            "supplier_hint": supplier_hint,
        }

    def process_folder(self, folder_path: str, client_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs every file in a folder through process_file() and returns
        an aggregate summary -- the "drop a chaotic folder in" demo path.
        """
        results: List[Dict[str, Any]] = []
        for fname in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                results.append(self.process_file(fpath, client_id=client_id))
            except Exception as e:
                logger.exception(f"Unhandled error processing {fpath}")
                results.append(self._error_result(fname, fpath, str(e)))

        ready = sum(1 for r in results if r.get("status") == "accounted")
        review = sum(1 for r in results if r.get("status") == "needs_review")
        errors = sum(1 for r in results if r.get("status") == "error")

        return {
            "folder": folder_path,
            "total": len(results),
            "ready_to_post": ready,
            "needs_review": review,
            "errors": errors,
            "results": results,
        }

    @staticmethod
    def _error_result(doc_id: str, file_path: str, message: str) -> Dict[str, Any]:
        return {"doc_id": doc_id, "status": "error", "file": file_path, "error": message}


# ----------------------------------------------------------------------
# Quick manual test: runs the real XML sample end to end (no Ollama
# needed -- XML is fully structured) and checks it lands ready_to_post,
# then runs it a second time to confirm the supplier is now remembered.
# Run: python src/orchestration/demo1_orchestrator.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    import importlib
    import src.config as _config
    importlib.reload(_config)
    import src.database.database as _database
    importlib.reload(_database)
    import src.memory.memory as _memory
    importlib.reload(_memory)
    from src.memory.memory import MemoryStore  # noqa: F811, E402

    sample_xml = os.path.join(_REPO_ROOT, "data_set", "IT01234567890_FPR01.xml")

    mem = MemoryStore()
    mem.upsert_client("c-001", "Test Client Srl")
    orch = Demo1Orchestrator(memory=mem)

    print("=== Run 1: first time seeing this supplier ===")
    result = orch.process_file(sample_xml, client_id="c-001")
    print(f"status: {result['status']}")
    print(f"supplier_hint before learning: {result['supplier_hint']}")
    assert result["status"] == "accounted", result

    print("\n=== Run 2: same document again -- supplier should now be recognized ===")
    result2 = orch.process_file(sample_xml, client_id="c-001")
    print(f"supplier_hint after learning: {result2['supplier_hint']}")
    assert result2["supplier_hint"] is not None

    print("\ndemo1_orchestrator.py self-test passed.")
