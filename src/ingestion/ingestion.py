"""
ingestion.py

Usage:
    from ingestion import IngestionPipeline

    pipeline = IngestionPipeline()
    doc = pipeline.ingest_file("invoice.pdf")
    payload = doc.to_classifier_input()   # <- hand this to classifier.py
"""

import os
import io
import csv
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF
import pdfplumber
import pandas as pd
from lxml import etree
from PIL import Image, ImageOps
import pytesseract

from ..database.schemas import (
    IngestedDocument, PageContent, TableData,
    SourceFileType, ExtractionMethod,
)

logger = logging.getLogger("ingestion")
logging.basicConfig(level=logging.INFO)

# If a PDF page has fewer than this many extracted chars, treat it as
# scanned and fall back to OCR on that page.
OCR_TEXT_THRESHOLD = 20

# Below this average OCR confidence, flag the document for human review.
OCR_CONFIDENCE_WARNING_THRESHOLD = 60

# Pixel intensity (0-255) used to binarize preprocessed images before OCR.
# Anything darker than this becomes black, anything lighter becomes white.
OCR_BINARIZE_THRESHOLD = 150

# Tesseract page segmentation mode used for the preprocessed OCR path.
# 6 = "assume a single uniform block of text", which works well for
# receipts/invoices after binarization.
OCR_PSM_CONFIG = "--psm 6"


class BaseIngestor:
    """All format-specific ingestors inherit from this."""

    def ingest(self, file_path: str) -> IngestedDocument:
        raise NotImplementedError

    def _new_doc(self, file_path: str, file_type: SourceFileType) -> IngestedDocument:
        return IngestedDocument(
            doc_id=str(uuid.uuid4()),
            source_path=file_path,
            original_filename=os.path.basename(file_path),
            file_type=file_type,
            ingested_at=datetime.utcnow().isoformat(),
            full_text="",
        )


class PDFIngestor(BaseIngestor):
    """
    Handles both native-text PDFs and scanned PDFs.
    OCR fallback is applied per-page, so a PDF with some native-text
    pages and some scanned pages (common with mixed accounting bundles)
    is handled correctly rather than all-or-nothing.
    """

    def ingest(self, file_path: str) -> IngestedDocument:
        doc = self._new_doc(file_path, SourceFileType.PDF_TEXT)
        full_text_parts = []

        try:
            pdf = fitz.open(file_path)
        except Exception as e:
            doc.success = False
            doc.error = f"Failed to open PDF: {e}"
            return doc

        tables_by_page = {}
        try:
            with pdfplumber.open(file_path) as plumber_pdf:
                for i, page in enumerate(plumber_pdf.pages):
                    extracted = page.extract_tables()
                    if extracted:
                        tables_by_page[i] = extracted
        except Exception as e:
            doc.warnings.append(f"Table extraction failed: {e}")

        any_scanned = False
        for page_num in range(len(pdf)):
            page = pdf[page_num]
            native_text = page.get_text("text").strip()

            if len(native_text) >= OCR_TEXT_THRESHOLD:
                method = ExtractionMethod.NATIVE_TEXT
                page_text = native_text
                ocr_conf = None
            else:
                any_scanned = True
                pix = page.get_pixmap(dpi=300)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                preprocessed = _preprocess_for_ocr(img)
                page_text, ocr_conf = _run_ocr(preprocessed, config=OCR_PSM_CONFIG)
                method = ExtractionMethod.OCR
                if ocr_conf is not None and ocr_conf < OCR_CONFIDENCE_WARNING_THRESHOLD:
                    doc.warnings.append(
                        f"Page {page_num + 1}: low OCR confidence ({ocr_conf:.1f}) — recommend human review"
                    )

            page_tables = []
            for t in tables_by_page.get(page_num, []):
                page_tables.append(TableData(
                    page_number=page_num + 1,
                    rows=t,
                    n_rows=len(t),
                    n_cols=len(t[0]) if t else 0,
                ))

            doc.pages.append(PageContent(
                page_number=page_num + 1,
                text=page_text,
                extraction_method=method,
                ocr_confidence=ocr_conf,
                tables=page_tables,
            ))
            doc.tables.extend(page_tables)
            full_text_parts.append(page_text)

        doc.file_type = SourceFileType.PDF_SCANNED if any_scanned else SourceFileType.PDF_TEXT
        doc.full_text = "\n\n".join(full_text_parts)
        doc.metadata["page_count"] = len(pdf)
        doc.metadata["has_scanned_pages"] = any_scanned
        pdf.close()
        return doc


class XMLIngestor(BaseIngestor):
    """
    Handles XML e-invoices. Uses a generic recursive parser rather than
    hardcoding one schema (e.g. FatturaPA), so it survives whatever
    e-invoice format a given client sends. Also flattens to plain text
    so anything downstream that just wants searchable text still works.
    """

    def ingest(self, file_path: str) -> IngestedDocument:
        doc = self._new_doc(file_path, SourceFileType.XML)
        try:
            tree = etree.parse(file_path)
            root = tree.getroot()
        except Exception as e:
            doc.success = False
            doc.error = f"Failed to parse XML: {e}"
            return doc

        def elem_to_dict(elem):
            tag = etree.QName(elem).localname
            children = list(elem)
            if not children:
                return {tag: (elem.text or "").strip()}
            result = {}
            for child in children:
                child_dict = elem_to_dict(child)
                for k, v in child_dict.items():
                    if k in result:
                        if not isinstance(result[k], list):
                            result[k] = [result[k]]
                        result[k].append(v)
                    else:
                        result[k] = v
            return {tag: result}

        structured = elem_to_dict(root)
        doc.structured_data = structured

        def flatten(d, parts):
            if isinstance(d, dict):
                for v in d.values():
                    flatten(v, parts)
            elif isinstance(d, list):
                for item in d:
                    flatten(item, parts)
            elif d:
                parts.append(str(d))

        parts = []
        flatten(structured, parts)
        doc.full_text = "\n".join(parts)
        doc.metadata["root_tag"] = etree.QName(root).localname
        return doc


class CSVIngestor(BaseIngestor):
    """Handles CSV exports — e.g. bank statements, ledger exports."""

    def ingest(self, file_path: str) -> IngestedDocument:
        doc = self._new_doc(file_path, SourceFileType.CSV)
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                sample = f.read(4096)
                try:
                    dialect = csv.Sniffer().sniff(sample)
                    delimiter = dialect.delimiter
                except csv.Error:
                    delimiter = ","

            df = pd.read_csv(file_path, delimiter=delimiter, dtype=str, keep_default_na=False)
        except Exception as e:
            doc.success = False
            doc.error = f"Failed to parse CSV: {e}"
            return doc

        rows = [df.columns.tolist()] + df.values.tolist()
        doc.tables.append(TableData(
            page_number=None,
            rows=rows,
            n_rows=len(rows),
            n_cols=len(df.columns),
        ))
        doc.structured_data = {"records": df.to_dict(orient="records")}
        doc.full_text = df.to_csv(index=False)
        doc.metadata["n_rows"] = len(df)
        doc.metadata["columns"] = df.columns.tolist()
        return doc

        
class TextIngestor(BaseIngestor):
    """Handles plain-text documents."""

    def ingest(self, file_path: str) -> IngestedDocument:
        doc = self._new_doc(file_path, SourceFileType.TXT)

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()

            doc.full_text = text

            doc.pages.append(PageContent(
                page_number=1,
                text=text,
                extraction_method=ExtractionMethod.TXT_PARSE,
            ))

            doc.metadata["character_count"] = len(text)
            doc.metadata["line_count"] = len(text.splitlines())

            return doc

        except Exception as e:
            doc.success = False
            doc.error = f"Failed to read TXT: {e}"
            return doc


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """
    Grayscale -> autocontrast -> binarize.

    This is the fix validated in ocr_fix_comparison.py's ocr_with_fix():
    plain pytesseract.image_to_data() on the raw RGB image (the old
    ocr_as_is() behavior) produced noticeably lower-confidence, noisier
    OCR on scanned receipts than running it on a cleaned-up black/white
    version of the image.
    """
    gray = ImageOps.grayscale(img)
    contrast = ImageOps.autocontrast(gray)
    binarized = contrast.point(lambda x: 0 if x < OCR_BINARIZE_THRESHOLD else 255, "1")
    return binarized


def _run_ocr(img: Image.Image, config: str = ""):
    """Runs pytesseract on `img` and returns (joined_text, avg_confidence)."""
    ocr_data = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
    words = [w for w in ocr_data["text"] if w.strip()]
    confs = [int(c) for c, w in zip(ocr_data["conf"], ocr_data["text"])
             if w.strip() and c != "-1"]
    text = " ".join(words)
    avg_conf = (sum(confs) / len(confs)) if confs else None
    return text, avg_conf


class ImageIngestor(BaseIngestor):
    """Handles photos/scans of receipts and invoices via OCR."""

    def ingest(self, file_path: str) -> IngestedDocument:
        doc = self._new_doc(file_path, SourceFileType.IMAGE)
        try:
            img = Image.open(file_path).convert("RGB")
        except Exception as e:
            doc.success = False
            doc.error = f"Failed to open image: {e}"
            return doc

        try:
            preprocessed = _preprocess_for_ocr(img)
            text, avg_conf = _run_ocr(preprocessed, config=OCR_PSM_CONFIG)
        except Exception as e:
            doc.success = False
            doc.error = f"OCR failed: {e}"
            return doc

        doc.full_text = text
        doc.pages.append(PageContent(
            page_number=1,
            text=text,
            extraction_method=ExtractionMethod.OCR,
            ocr_confidence=avg_conf,
        ))
        doc.metadata["image_size"] = list(img.size)
        doc.metadata["ocr_confidence"] = avg_conf
        if avg_conf is not None and avg_conf < OCR_CONFIDENCE_WARNING_THRESHOLD:
            doc.warnings.append(f"Low OCR confidence ({avg_conf:.1f}) — recommend human review")
        return doc


class IngestionPipeline:
    """Auto-detects file type by extension and routes to the right ingestor."""

    EXT_MAP = {
        ".pdf": PDFIngestor,
        ".xml": XMLIngestor,
        ".txt": TextIngestor,
        ".csv": CSVIngestor,
        ".png": ImageIngestor,
        ".jpg": ImageIngestor,
        ".jpeg": ImageIngestor,
        ".tiff": ImageIngestor,
        ".bmp": ImageIngestor,
    }

    def __init__(self):
        self._instances = {}

    def _get_ingestor(self, ext: str) -> Optional[BaseIngestor]:
        cls = self.EXT_MAP.get(ext.lower())
        if cls is None:
            return None
        if cls not in self._instances:
            self._instances[cls] = cls()
        return self._instances[cls]

    def ingest_file(self, file_path: str) -> IngestedDocument:
        ext = Path(file_path).suffix.lower()
        ingestor = self._get_ingestor(ext)
        if ingestor is None:
            return IngestedDocument(
                doc_id=str(uuid.uuid4()),
                source_path=file_path,
                original_filename=os.path.basename(file_path),
                file_type=SourceFileType.UNKNOWN,
                ingested_at=datetime.utcnow().isoformat(),
                full_text="",
                success=False,
                error=f"Unsupported file extension: {ext}",
            )
        logger.info(f"Ingesting {file_path} with {ingestor.__class__.__name__}")
        return ingestor.ingest(file_path)

    def ingest_folder(self, folder_path: str) -> List[IngestedDocument]:
        results = []
        for fname in sorted(os.listdir(folder_path)):
            fpath = os.path.join(folder_path, fname)
            if os.path.isfile(fpath):
                results.append(self.ingest_file(fpath))
        return results
