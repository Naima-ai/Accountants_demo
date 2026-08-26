from typing import Any, List, Optional

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from ..agents.reminder_agent import ReminderAgent, ReminderResult


router = APIRouter(
    prefix="/api",
    tags=["Demo 1", "Demo 2"],
)


# ----------------------------------------------------------------------
# Demo 1 - Response Models
# ----------------------------------------------------------------------


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


# ----------------------------------------------------------------------
# Demo 2 - Request Models
# ----------------------------------------------------------------------


class DocumentCheckRequest(BaseModel):
    client_id: str
    client_name: Optional[str] = None
    period: str
    expected_documents: List[str]
    received_documents: List[str]


class FollowUpRequest(BaseModel):
    client_id: str
    client_name: Optional[str] = None
    period: str
    expected_documents: List[str]
    received_documents: List[str]
    days: int = 3


# ----------------------------------------------------------------------
# Agents
# ----------------------------------------------------------------------


reminder_agent = ReminderAgent()


# ----------------------------------------------------------------------
# Demo 1 - Document Processing
# ----------------------------------------------------------------------


@router.post(
    "/demo-1/process",
    response_model=ProcessResponse,
)
async def process_demo_1(
    file: UploadFile = File(...),
):
    """
    Demo 1 document processing endpoint.

    Currently returns dummy data.
    The LangGraph orchestrator will be connected later.
    """

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


# ----------------------------------------------------------------------
# Demo 2 - Document Collection
# ----------------------------------------------------------------------


@router.post(
    "/demo-2/check-documents",
    response_model=ReminderResult,
)
async def check_demo_2_documents(
    request: DocumentCheckRequest,
):
    """
    Demo 2:
    Compare expected client documents with received documents
    and generate a reminder when documents are missing.
    """

    return reminder_agent.check_documents(
        client_id=request.client_id,
        client_name=request.client_name,
        period=request.period,
        expected_documents=request.expected_documents,
        received_documents=request.received_documents,
    )


@router.post(
    "/demo-2/follow-up",
    response_model=ReminderResult,
)
async def schedule_demo_2_follow_up(
    request: FollowUpRequest,
):
    """
    Demo 2:
    Check for missing documents and schedule a follow-up.
    """

    result = reminder_agent.check_documents(
        client_id=request.client_id,
        client_name=request.client_name,
        period=request.period,
        expected_documents=request.expected_documents,
        received_documents=request.received_documents,
    )

    return reminder_agent.schedule_follow_up(
        result,
        days=request.days,
    )