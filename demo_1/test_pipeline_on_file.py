"""
test_pipeline_on_file.py

Runs the FULL Demo 1 chain (ingestion -> classifier -> extractor ->
validator) on ANY document you point it at. One reusable script instead
of a new one per test file.

Usage (from inside demo_1/, Ollama must be running):
    python test_pipeline_on_file.py ../data_set/clean_invoice.png
    python test_pipeline_on_file.py ../data_set/sample_receipt.png
    python test_pipeline_on_file.py ../data_set/IT01234567890_FPR01.xml
    python test_pipeline_on_file.py ../data_set/my_real_receipt.jpg

If you don't pass a path, it defaults to data_set/clean_invoice.png.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from classifier import DocumentClassifier
from extractor import FieldExtractor
from ingestion import IngestionPipeline
from ollama_client import warm_up
from validator import Validator

# Configure clean logging instead of raw print statements
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("PipelineTest")

if os.name == "nt":
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        )
    except ImportError:
        pass

DEFAULT_PATH = os.path.join("..", "data_set", "clean_invoice.png")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the full document processing pipeline on a target file."
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        default=DEFAULT_PATH,
        help="Path to the document file (invoice, receipt, XML, etc.)",
    )
    parser.add_argument(
        "--skip-warmup",
        action="store_true",
        help="Skip the local Ollama model warm-up check.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the final summary and extraction results as raw JSON.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    target_path = args.file_path

    if not os.path.exists(target_path):
        logger.error(f"Target file not found: {target_path}")
        sys.exit(1)

    pipeline = IngestionPipeline()
    clf = DocumentClassifier()
    extractor = FieldExtractor()
    validator = Validator()

    if not args.skip_warmup:
        print(
            "Warming up local model (best-effort -- not required for XML/CSV documents)..."
        )
        if not warm_up():
            print(
                "Warm-up failed (Ollama not running?) -- continuing anyway. "
                "XML/CSV documents don't need the model at all; anything that does "
                "will fail with a clear message instead of blocking here.\n"
            )
        else:
            print("Model warm.\n")

    print(f"Ingesting: {target_path}")
    doc = pipeline.ingest_file(target_path)

    print("\n--- Ingestion result ---")
    print(f"success:        {doc.success}")
    print(f"file_type:      {getattr(doc.file_type, 'value', doc.file_type)}")
    print(f"ocr_confidence: {doc.metadata.get('ocr_confidence', 'N/A')}")
    print(f"warnings:       {doc.warnings}")

    if not doc.success:
        print(f"error:          {getattr(doc, 'error', 'Unknown error')}")
        print("\nIngestion failed -- stopping here.")
        sys.exit(1)

    print(f"\nExtracted text preview:\n{doc.full_text[:500]}...")

    ci = doc.to_classifier_input()

    print("\n--- Classification ---")
    classification = clf.classify(ci)
    print(classification)

    print("\n--- Extraction ---")
    fields = extractor.extract(ci, classification.document_type)
    fields_dict = (
        fields.model_dump()
        if hasattr(fields, "model_dump")
        else fields.dict()
    )
    print(json.dumps(fields_dict, indent=2, default=str))

    print("\n--- Validation ---")
    result = validator.validate(fields)
    result_dict = (
        result.model_dump() if hasattr(result, "model_dump") else result.dict()
    )
    print(json.dumps(result_dict, indent=2, default=str))

    print("\n--- Summary ---")
    is_valid = getattr(result, "is_valid", False)
    needs_review = getattr(result, "needs_review", True)
    confidence = getattr(result, "confidence", 0.0)

    print(f"is_valid:     {is_valid}")
    print(f"needs_review: {needs_review}")
    print(f"confidence:   {confidence}")

    if args.json:
        output_payload = {
            "file": target_path,
            "success": doc.success,
            "classification": str(classification),
            "extraction": fields_dict,
            "validation": result_dict,
        }
        print("\n--- JSON Payload ---")
        print(json.dumps(output_payload, indent=2, default=str))


if __name__ == "__main__":
    main()