"""
demo1_orchestrator.py

Chains demo_1's pipeline (ingestion -> classifier -> extractor ->
validator -> accounting_agent, all Meet's + my ingestion.py) together
with the shared memory/database layer, and returns one result object
the API layer (Harith's api.py) or a CLI can hand back as-is.

This is the piece that turns "five separate scripts" into "drop a
folder in, get clean journal entries out" -- the Demo 1 wow moment
from the brief.

Usage:
    from demo1_orchestrator import Demo1Orchestrator

    orch = Demo1Orchestrator()
    result = orch.process_file("../data_set/IT01234567890_FPR01.xml", client_id="c-001")
    summary = orch.process_folder("../data_set", client_id="c-001")
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("demo1_orchestrator")
logging.basicConfig(level=logging.INFO)

_CURR_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_CURR_DIR)
_BASE_DIR = os.path.dirname(_SRC_DIR)
_DEMO_1_DIR = os.path.join(_BASE_DIR, "demo_1")

for path in (_SRC_DIR, os.path.join(_SRC_DIR, "memory"), os.path.join(_SRC_DIR, "database"), _DEMO_1_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from memory import MemoryStore  # noqa: E402


class Demo1Orchestrator:
    """Runs the full Demo 1 chain and persists every step through MemoryStore."""

    def __init__(self, memory: Optional[MemoryStore] = None):
        # Imported lazily (after sys.path is patched above) so this module
        # can be imported even in environments where demo_1's deps
        # (PyMuPDF, pytesseract, ...) aren't installed, as long as nobody
        # actually calls process_file/process_folder.
        from ingestion import IngestionPipeline
        from classifier import DocumentClassifier
        from extractor import FieldExtractor
        from validator import Validator
        from accounting_agent import AccountingAgent

        self.pipeline = IngestionPipeline()
        self.classifier = DocumentClassifier()
        self.extractor = FieldExtractor()
        self.validator = Validator()
        self.accounting_agent = AccountingAgent()
        self.memory = memory or MemoryStore()

    def process_file(self, file_path: str, client_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs one document through the entire Demo 1 chain and persists
        the result. Never raises for a single bad document -- a failure
        at any stage comes back as needs_review=True with the reason in
        `notes`/`error`, so process_folder() can keep going.
        """
        doc = self.pipeline.ingest_file(file_path)

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
# Run: python demo1_orchestrator.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    import importlib
    import config as _config
    importlib.reload(_config)
    import database as _database
    importlib.reload(_database)
    import memory as _memory
    importlib.reload(_memory)
    from memory import MemoryStore  # noqa: F811, E402

    sample_xml = os.path.join(_BASE_DIR, "data_set", "IT01234567890_FPR01.xml")

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
