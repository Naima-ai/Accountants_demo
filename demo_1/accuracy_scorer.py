"""
accuracy_scorer.py

Runs the full Demo 1 pipeline (ingest -> classify -> extract -> validate)
against N random real samples from the local SROIE test set, and scores
extracted fields against SROIE's own ground truth labels. Gives a real
accuracy percentage instead of anecdotal single-document impressions.

Setup: SROIE dataset can be placed in data_set/sroie or pointed to via
the SROIE_PATH environment variable.

Run (from inside demo_1/ or wherever your modules live):
    python accuracy_scorer.py
"""

import os
import random
import re
import tempfile
from datetime import datetime

from datasets import load_from_disk, concatenate_datasets

from ingestion import IngestionPipeline
from classifier import DocumentClassifier
from extractor import FieldExtractor
from validator import Validator
from ollama_client import warm_up

# Windows: pytesseract can't find tesseract.exe unless it's on PATH.
if os.name == "nt":
    import pytesseract
    tesseract_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(tesseract_default):
        pytesseract.pytesseract.tesseract_cmd = tesseract_default

# Dynamic path resolution: checks environment variable, then repo data_set/, then desktop fallback
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATASET_DIR = os.path.join(CURRENT_DIR, "..", "data_set", "sroie")
DESKTOP_FALLBACK = os.path.expanduser(r"~\Desktop\sroie")
LOCAL_SROIE_PATH = os.getenv("SROIE_PATH", DEFAULT_DATASET_DIR if os.path.exists(DEFAULT_DATASET_DIR) else DESKTOP_FALLBACK)

N_SAMPLES = 8
RANDOM_SEED = 42  # fixed, so results are reproducible run-to-run


def get_entity(example, key):
    return example.get("objects", {}).get("entities", {}).get(key)


def normalize(s):
    """Loose text comparison: lowercase, strip, collapse whitespace, drop punctuation."""
    if s is None:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_amount(s):
    """For totals: pull out the numeric value only, ignore currency/commas/formatting."""
    if s is None:
        return None
    s = str(s)
    s = re.sub(r"[^\d.,]", "", s)
    s = s.replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


DATE_FORMATS = [
    "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y"
]


def normalize_date(s):
    """Dates come in different formats from ground truth vs extraction
    (DD/MM/YYYY vs YYYY-MM-DD) -- compare actual calendar dates, not raw
    strings, or correct extractions get wrongly marked as failures."""
    if s is None:
        return None
    s = str(s).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def main():
    if not os.path.exists(LOCAL_SROIE_PATH):
        print(f"SROIE dataset not found at: {LOCAL_SROIE_PATH}")
        print("Please point the SROIE_PATH environment variable to your SROIE directory.")
        return

    print(f"Loading local SROIE dataset from: {LOCAL_SROIE_PATH}")
    ds_dict = load_from_disk(LOCAL_SROIE_PATH)
    print(f"Splits found: {list(ds_dict.keys())}")

    if "train" in ds_dict and "test" in ds_dict:
        ds = concatenate_datasets([ds_dict["train"], ds_dict["test"]])
        print(f"Combined train ({len(ds_dict['train'])}) + test ({len(ds_dict['test'])}) = {len(ds)} total examples.\n")
    else:
        split_name = list(ds_dict.keys())[0]
        ds = ds_dict[split_name]
        print(f"Only found split '{split_name}', {len(ds)} examples available.\n")

    # --- Show schema confirmation ---
    sample0 = ds[0]
    entities = sample0.get("objects", {}).get("entities", {})
    if not all(k in entities for k in ("company", "date", "total")):
        print("\nExpected 'company'/'date'/'total' inside objects.entities but didn't find them.")
        return
    print("Confirmed: nested objects.entities.{company,date,total} structure. Proceeding.\n")

    random.seed(RANDOM_SEED)
    indices = random.sample(range(len(ds)), min(N_SAMPLES, len(ds)))

    pipeline = IngestionPipeline()
    clf = DocumentClassifier()
    extractor = FieldExtractor()
    validator = Validator()

    print("Warming up local model...")
    warm_up()
    print()

    results = []
    tmpdir = tempfile.mkdtemp()

    for i, idx in enumerate(indices):
        example = ds[idx]
        img_path = os.path.join(tmpdir, f"sample_{idx}.png")
        example["image"].convert("RGB").save(img_path)

        print(f"[{i+1}/{len(indices)}] Testing sample #{idx}...")
        doc = pipeline.ingest_file(img_path)
        if not doc.success:
            print(f"  Ingestion failed: {doc.error}")
            results.append({"idx": idx, "ingestion_failed": True})
            continue

        ci = doc.to_classifier_input()
        classification = clf.classify(ci)
        fields = extractor.extract(ci, classification.document_type)
        validation = validator.validate(fields)

        norm_extracted = normalize(fields.supplier_name)
        norm_gt = normalize(get_entity(example, "company"))
        supplier_correct = norm_extracted == norm_gt
        supplier_correct_loose = bool(norm_extracted) and bool(norm_gt) and (
            norm_extracted == norm_gt or norm_gt in norm_extracted or norm_extracted in norm_gt
        )
        gt_total = normalize_amount(get_entity(example, "total"))
        extracted_total = normalize_amount(fields.total_amount)
        total_correct = gt_total is not None and extracted_total is not None and abs(gt_total - extracted_total) < 0.01
        gt_date = normalize_date(get_entity(example, "date"))
        extracted_date = normalize_date(fields.document_date)
        date_correct = gt_date is not None and gt_date == extracted_date

        results.append({
            "idx": idx,
            "ingestion_failed": False,
            "supplier_correct": supplier_correct,
            "supplier_correct_loose": supplier_correct_loose,
            "total_correct": total_correct,
            "date_correct": date_correct,
            "needs_review": validation.needs_review,
            "confidence": validation.confidence,
            "extracted_supplier": fields.supplier_name,
            "gt_supplier": get_entity(example, "company"),
            "extracted_total": fields.total_amount,
            "gt_total": get_entity(example, "total"),
            "extracted_date": fields.document_date,
            "gt_date": get_entity(example, "date"),
        })
        print(f"  supplier: {'OK' if supplier_correct else ('CLOSE' if supplier_correct_loose else 'WRONG')}  "
              f"total: {'OK' if total_correct else 'WRONG'}  "
              f"date: {'OK' if date_correct else 'WRONG'}  "
              f"needs_review: {validation.needs_review}")

    # --- Aggregate report ---
    attempted = [r for r in results if not r["ingestion_failed"]]
    n = len(attempted)
    print("\n" + "=" * 50)
    print(f"RESULTS: {n}/{len(results)} documents processed")
    if n > 0:
        print(f"Supplier name accuracy (exact):   {sum(r['supplier_correct'] for r in attempted)}/{n} "
              f"({100*sum(r['supplier_correct'] for r in attempted)/n:.0f}%)")
        print(f"Supplier name accuracy (contains): {sum(r['supplier_correct_loose'] for r in attempted)}/{n} "
              f"({100*sum(r['supplier_correct_loose'] for r in attempted)/n:.0f}%)")
        print(f"Total amount accuracy:            {sum(r['total_correct'] for r in attempted)}/{n} "
              f"({100*sum(r['total_correct'] for r in attempted)/n:.0f}%)")
        print(f"Date accuracy:                    {sum(r['date_correct'] for r in attempted)}/{n} "
              f"({100*sum(r['date_correct'] for r in attempted)/n:.0f}%)")
        print(f"Flagged needs_review:             {sum(r['needs_review'] for r in attempted)}/{n} "
              f"({100*sum(r['needs_review'] for r in attempted)/n:.0f}%)")

        print("\n--- Wrong ones, for a closer look (shows exactly which field(s) failed) ---")
        for r in attempted:
            if r["supplier_correct_loose"] and r["total_correct"] and r["date_correct"]:
                continue
            failed_fields = []
            if not r["supplier_correct_loose"]:
                failed_fields.append(f"SUPPLIER (got '{r['extracted_supplier']}', actual '{r['gt_supplier']}')")
            if not r["total_correct"]:
                failed_fields.append(f"TOTAL (got '{r['extracted_total']}', actual '{r['gt_total']}')")
            if not r["date_correct"]:
                failed_fields.append(f"DATE (got '{r['extracted_date']}', actual '{r['gt_date']}')")
            print(f"  #{r['idx']}: " + " | ".join(failed_fields))


if __name__ == "__main__":
    main()