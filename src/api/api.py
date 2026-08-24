#dummy endpoint for demo 1, to be replaced with the actual orchestrator call

from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from typing import Any, List


router = APIRouter(
    prefix="/api",
    tags=["Demo 1"],
)


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float


class DocumentData(BaseModel):
    filename: str
    classification: str
    confidence: float
    raw_text: str
    supplier: str
    vat_id: str
    date: str
    currency: str
    total_amount: float
    line_items: List[LineItem]


class ValidationData(BaseModel):
    status: str
    balanced: bool
    warnings: List[str]


class JournalEntry(BaseModel):
    account: str
    debit: float
    credit: float


class AccountingData(BaseModel):
    status: str
    journal_entry: List[JournalEntry]


class ProcessResponse(BaseModel):
    status: str
    document: DocumentData
    validation: ValidationData
    accounting: AccountingData



@router.post(
    "/demo-1/process",
    response_model=ProcessResponse,
)
async def process_demo_1(file: UploadFile = File(...)):
    return {
        "status": "success",
        "document": {
            "filename": file.filename,
            "classification": "Invoice",
            "confidence": 0.97,
            "raw_text": (
                "INVOICE\n"
                "Acme Italia S.r.l.\n"
                "VAT: IT12345678901\n"
                "Date: 2026-08-20\n"
                "Total: EUR 1250.00"
            ),
            "supplier": "Acme Italia S.r.l.",
            "vat_id": "IT12345678901",
            "date": "2026-08-20",
            "currency": "EUR",
            "total_amount": 1250.00,
            "line_items": [
                {
                    "description": "Consulting Services",
                    "quantity": 1,
                    "unit_price": 1250.00,
                    "total": 1250.00,
                }
            ],
        },
        "validation": {
            "status": "valid",
            "balanced": True,
            "warnings": [],
        },
        "accounting": {
            "status": "balanced",
            "journal_entry": [
                {
                    "account": "Professional Services",
                    "debit": 1250.00,
                    "credit": 0.00,
                },
                {
                    "account": "Accounts Payable",
                    "debit": 0.00,
                    "credit": 1250.00,
                },
            ],
        },
    }