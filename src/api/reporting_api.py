"""
reporting_api.py

Demo 3 API integration (my deliverable: "Report_agent.py, Reporting/
orchestration/API integration"). Wraps demo3_orchestrator.ReportOrchestrator
so the Advisory Report agent is reachable over HTTP, the same way
api.py exposes Demo 1.

Included into the app in main.py:
    from src.api.reporting_api import router as reporting_router
    app.include_router(reporting_router)
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.orchestration.demo3_orchestrator import ReportOrchestrator

router = APIRouter(
    prefix="/api/demo-3",
    tags=["Demo 3"],
)

_orchestrator: Optional[ReportOrchestrator] = None


def get_orchestrator() -> ReportOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ReportOrchestrator()
    return _orchestrator


class StatementInput(BaseModel):
    revenue: Optional[float] = None
    cogs: Optional[float] = None
    operating_expenses: Optional[float] = None
    net_income: Optional[float] = None
    current_assets: Optional[float] = None
    inventory: Optional[float] = None
    current_liabilities: Optional[float] = None
    total_debt: Optional[float] = None
    equity: Optional[float] = None
    accounts_receivable: Optional[float] = None


class GenerateReportRequest(BaseModel):
    client_id: str
    period: str = Field(..., description="e.g. '2026-Q2'")
    statement: StatementInput
    benchmarks: Optional[Dict[str, float]] = None


class RatioSetResponse(BaseModel):
    revenue: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    dso_days: Optional[float] = None
    debt_to_equity: Optional[float] = None


class AnomalyResponse(BaseModel):
    metric: str
    severity: str
    message: str
    current_value: Optional[float] = None
    reference_value: Optional[float] = None
    reference_type: str


class GenerateReportResponse(BaseModel):
    report_id: int
    client_name: str
    period: str
    ratios: RatioSetResponse
    prior_ratios: Optional[RatioSetResponse] = None
    anomalies: List[AnomalyResponse]
    narrative_method: str
    letter_text: str
    compared_to_prior: bool
    generated_at: str


@router.post("/generate", response_model=GenerateReportResponse)
async def generate_report(request: GenerateReportRequest):
    """
    Runs the Demo 3 pipeline: fetch prior period from memory (if any) ->
    compute ratios -> detect anomalies -> generate the advisory letter ->
    persist -> return.
    """
    orchestrator = get_orchestrator()
    try:
        result = orchestrator.generate_report(
            client_id=request.client_id,
            period=request.period,
            statement=request.statement.model_dump(exclude_none=True),
            benchmarks=request.benchmarks,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.get("/reports/{client_id}")
async def list_reports(client_id: str) -> List[Dict[str, Any]]:
    """Returns every previously generated report for a client, most recent first."""
    orchestrator = get_orchestrator()
    return orchestrator.memory.get_reports(client_id)
