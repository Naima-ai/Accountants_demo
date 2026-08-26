"""
schemas.py

Defines the unified output contract for ingestion.py.
Every ingestor (PDF, XML, CSV, TXT, Image) returns an IngestedDocument,
regardless of source format. This is the interface classifier.py and
extractor.py should be built against — they should never need to know
whether a document originally was a scanned receipt or an XML e-invoice.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum


class SourceFileType(str, Enum):
    PDF_TEXT = "pdf_text"          # PDF with extractable native text
    PDF_SCANNED = "pdf_scanned"     # PDF that required OCR on some/all pages
    TXT = "txt"
    XML = "xml"
    CSV = "csv"
    IMAGE = "image"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    NATIVE_TEXT = "native_text"
    OCR = "ocr"
    XML_PARSE = "xml_parse"
    CSV_PARSE = "csv_parse"
    TXT_PARSE = "txt_parse"


class TableData(BaseModel):
    page_number: Optional[int] = None
    rows: List[List[str]]
    n_rows: int
    n_cols: int


class PageContent(BaseModel):
    page_number: int
    text: str
    extraction_method: ExtractionMethod
    ocr_confidence: Optional[float] = None  # 0-100, None if not OCR'd
    tables: List[TableData] = Field(default_factory=list)


class IngestedDocument(BaseModel):
    doc_id: str
    source_path: str
    original_filename: str
    file_type: SourceFileType
    ingested_at: str
    full_text: str
    pages: List[PageContent] = Field(default_factory=list)
    # Populated for XML/CSV where native structure exists
    structured_data: Optional[Dict[str, Any]] = None
    tables: List[TableData] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None

    def to_classifier_input(self) -> Dict[str, Any]:
        """
        Minimal, stable payload for classifier.py / extractor.py.
        Meet should build against THIS shape, not the full object.
        """
        return {
            "doc_id": self.doc_id,
            "file_type": self.file_type.value,
            "text": self.full_text,
            "structured_data": self.structured_data,
            "tables": [t.dict() for t in self.tables],
            "metadata": self.metadata,
            "needs_review": len(self.warnings) > 0,
            "warnings": self.warnings,
        }
