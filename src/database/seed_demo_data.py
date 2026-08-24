"""
seed_demo_data.py

Populates the real (non-in-memory) demo database with a full pass over
the sample dataset: 10 documents in every format ingestion.py supports
(XML, native PDF, scanned PDF, plain text, image) -- SROIE/CORD are real
photographed receipts (data_set/samples/images/, see
download_real_samples.py), the rest are synthetic Italian invoices built
to exercise the other format code paths (data_set/samples/,
see generate_synthetic_samples.py) -- run through the full Demo 1 chain
and persisted, so there's real, browsable demo data instead of an empty
database.

This is what "Database" (my Demo 1 assignment) was actually for:
database.py defines the schema, this script is what fills it.

Usage (from repo root or here, doesn't matter):
    python generate_synthetic_samples.py         # once, or whenever you want fresh synthetic docs
    python download_real_samples.py               # once -- pulls real SROIE/CORD images (network)
    python src/database/seed_demo_data.py          # runs everything through the pipeline into the DB

Needs Ollama running locally for full accuracy on non-XML documents
(PDF/image/text classification+extraction fall back to the local SLM --
see demo_1/README.md). Without it, those documents still get processed
end-to-end but land in needs_review, same as any other low-confidence
result -- the run itself won't fail.
"""

import glob
import logging
import os
import sys

logger = logging.getLogger("seed_demo_data")
logging.basicConfig(level=logging.INFO)

_CURR_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_CURR_DIR)
_BASE_DIR = os.path.dirname(_SRC_DIR)
_DATA_SET_DIR = os.path.join(_BASE_DIR, "data_set")
_SAMPLES_DIR = os.path.join(_DATA_SET_DIR, "samples")

def _prioritize(path: str) -> None:
    # database.py/memory.py share their filename with their parent
    # folder, so they must be forced to the front of sys.path (in this
    # order) or `import database`/`import memory` can resolve to the
    # wrong thing (the package's __init__.py instead of the module).
    # This matters especially here: this script's own directory IS
    # src/database, which Python auto-adds to sys.path[0] before this
    # code even runs, so a naive "insert if not present" guard leaves
    # it stuck behind src/ once src/ gets inserted for other reasons.
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


for path in (_SRC_DIR, os.path.join(_SRC_DIR, "orchestration"), os.path.join(_SRC_DIR, "memory"), os.path.join(_SRC_DIR, "database")):
    _prioritize(path)


def _configure_tesseract_windows() -> None:
    """
    Best-effort: on Windows, pytesseract needs to find tesseract.exe
    explicitly if it isn't on PATH. Tries a couple of common install
    locations (winget's per-user install, and the classic Program Files
    admin install that test_pipeline_on_file.py already assumes) and
    leaves pytesseract's default lookup alone if neither exists --
    OCR calls fail per-document with a clear error either way, they
    don't block the rest of the run.
    """
    if os.name != "nt":
        return
    try:
        import pytesseract
    except ImportError:
        return

    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


# Demo client roster -- same three clients demo2_orchestrator.py's
# self-test uses, so a full seed run gives Demo 1 AND Demo 2 something
# to show against the same clients.
DEMO_CLIENTS = [
    ("c-001", "Rossi Impianti Srl", "friendly"),
    ("c-002", "Bianchi Consulting Srl", "formal"),
    ("c-003", "Verdi Logistica Srl", "formal"),
]

# (subfolder under data_set/samples/, glob pattern, human label)
SAMPLE_GROUPS = [
    ("xml", "*.xml", "XML e-invoices (synthetic)"),
    ("pdf", "*.pdf", "Native-text PDFs (synthetic)"),
    ("pdf_scanned", "*.pdf", "Scanned PDFs -- OCR path (synthetic)"),
    ("text", "*.txt", "Plain text invoices (synthetic)"),
    ("images", "real_sroie_receipt_*.png", "SROIE receipts (real, photographed)"),
    ("images", "real_cord_receipt_*.png", "CORD receipts (real, photographed)"),
]


def _ensure_samples_exist() -> None:
    missing_synthetic = not os.path.isdir(os.path.join(_SAMPLES_DIR, "xml")) or not glob.glob(
        os.path.join(_SAMPLES_DIR, "xml", "*.xml")
    )
    if missing_synthetic:
        logger.info("No synthetic samples found -- generating them now.")
        sys.path.insert(0, _DATA_SET_DIR)
        from generate_synthetic_samples import generate_all
        generate_all(10)

    images_dir = os.path.join(_SAMPLES_DIR, "images")
    if not os.path.isdir(images_dir) or not glob.glob(os.path.join(images_dir, "*.png")):
        logger.warning(
            "No real sample images found in data_set/samples/images/. "
            "Run `python data_set/download_real_samples.py` first (needs network + `datasets`) "
            "to pull real SROIE/CORD receipts -- continuing without them for now."
        )


def seed() -> None:
    _configure_tesseract_windows()
    _ensure_samples_exist()

    from memory import MemoryStore
    from demo1_orchestrator import Demo1Orchestrator

    memory = MemoryStore()
    for client_id, name, tone in DEMO_CLIENTS:
        memory.upsert_client(client_id, name, preferred_tone=tone)

    orchestrator = Demo1Orchestrator(memory=memory)

    group_stats = {}
    client_cycle = 0

    for subfolder, pattern, label in SAMPLE_GROUPS:
        folder = os.path.join(_SAMPLES_DIR, subfolder)
        files = sorted(glob.glob(os.path.join(folder, pattern)))
        if not files:
            group_stats[label] = {"total": 0, "ready_to_post": 0, "needs_review": 0, "errors": 0}
            continue

        stats = {"total": 0, "ready_to_post": 0, "needs_review": 0, "errors": 0}
        for file_path in files:
            client_id = DEMO_CLIENTS[client_cycle % len(DEMO_CLIENTS)][0]
            client_cycle += 1
            stats["total"] += 1
            try:
                result = orchestrator.process_file(file_path, client_id=client_id)
                status = result.get("status")
                if status == "accounted":
                    stats["ready_to_post"] += 1
                elif status == "needs_review":
                    stats["needs_review"] += 1
                else:
                    stats["errors"] += 1
            except Exception:
                logger.exception(f"Unhandled error seeding {file_path}")
                stats["errors"] += 1

        group_stats[label] = stats

    _print_summary(group_stats, memory)


def _print_summary(group_stats: dict, memory) -> None:
    print("\n" + "=" * 72)
    print("DEMO DATABASE SEEDED")
    print("=" * 72)

    grand_total = grand_ready = grand_review = grand_error = 0
    for label, stats in group_stats.items():
        print(f"\n{label}")
        print(f"  total={stats['total']}  ready_to_post={stats['ready_to_post']}  "
              f"needs_review={stats['needs_review']}  errors={stats['errors']}")
        grand_total += stats["total"]
        grand_ready += stats["ready_to_post"]
        grand_review += stats["needs_review"]
        grand_error += stats["errors"]

    print("\n" + "-" * 72)
    print(f"TOTAL: {grand_total} documents  |  {grand_ready} ready_to_post  |  "
          f"{grand_review} needs_review  |  {grand_error} errors")
    if grand_total:
        print(f"Ready-to-post rate: {grand_ready / grand_total * 100:.1f}%")
    print("-" * 72)

    review_queue = memory.list_review_queue("demo_1")
    print(f"\nDemo 1 review queue: {len(review_queue)} open item(s)")

    # The synthetic samples deliberately reuse 10 suppliers across the
    # generated documents (see generate_synthetic_samples.py), so a
    # supplier seen more than once per client proves the auto-learn
    # path (memory.learn_supplier) actually fired during this run.
    recognized = 0
    for client_id, _, _ in DEMO_CLIENTS:
        for supplier_name, _, _, _ in _synthetic_suppliers():
            if memory.get_supplier_hint(client_id, supplier_name):
                recognized += 1
    print(f"Recurring suppliers recognized across the roster: {recognized}")

    print("\nDatabase ready for demo testing.")


def _synthetic_suppliers():
    sys.path.insert(0, _DATA_SET_DIR)
    from generate_synthetic_samples import SUPPLIERS
    return SUPPLIERS


if __name__ == "__main__":
    seed()
