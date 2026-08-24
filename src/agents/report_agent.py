"""
report_agent.py

Demo 3: Financial Analysis Agent (Advisory Report + Alerts).

Takes a client's financial statement, compares it against the prior
period and industry benchmarks, flags anomalies (cash strain, declining
margins, worsening DSO), and writes a plain-language advisory letter --
the "from a raw statement, a letter ready to send in 1 minute" wow
moment from the brief.

Ratio math and anomaly detection are deterministic (no model call,
mirrors validator.py's philosophy of keeping anything checkable out of
the SLM's hands). Only the narrative write-up goes through the local
model, via the shared src/llm/ollama_client.py -- and falls back to a
template narrative if Ollama isn't reachable, so this agent (and its
self-test) works with zero external services.

Usage:
    from src.agents.report_agent import FinancialAnalysisAgent

    agent = FinancialAnalysisAgent()
    report = agent.analyze("Rossi Impianti Srl", "2026-Q2", statement, prior_statement)
    print(report.letter_text)
"""

import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

logger = logging.getLogger("report_agent")
logging.basicConfig(level=logging.INFO)

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/agents/report_agent.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from src.llm.ollama_client import OLLAMA_HOST, OLLAMA_MODEL, call_ollama
    _OLLAMA_AVAILABLE = True
except ImportError:
    _OLLAMA_AVAILABLE = False
    OLLAMA_HOST, OLLAMA_MODEL = "http://localhost:11434", "qwen2.5:7b-instruct"

from src.config import DEFAULT_BENCHMARKS, ANOMALY_MARGIN_DECLINE_PCT_POINTS, \
    ANOMALY_DSO_INCREASE_DAYS, ANOMALY_CASH_STRAIN_CURRENT_RATIO


# ----------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------

class RatioSet(BaseModel):
    revenue: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    dso_days: Optional[float] = None
    debt_to_equity: Optional[float] = None


class Anomaly(BaseModel):
    metric: str
    severity: str  # "alert" | "warning"
    message: str
    current_value: Optional[float] = None
    reference_value: Optional[float] = None
    reference_type: str = "prior_period"  # "prior_period" | "benchmark"


class AnalysisReport(BaseModel):
    client_name: str
    period: str
    ratios: RatioSet
    prior_ratios: Optional[RatioSet] = None
    benchmark: Dict[str, float]
    anomalies: List[Anomaly] = []
    narrative_method: str = "template"  # "model" | "template"
    letter_text: str = ""
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


# ----------------------------------------------------------------------
# Agent
# ----------------------------------------------------------------------

class FinancialAnalysisAgent:
    """
    Statement in -> ratios + anomalies (deterministic) -> narrative
    letter (local SLM, with a template fallback).

    Expected `statement` shape (all figures numeric, any missing key is
    just treated as unavailable -- ratios/anomalies that need it are
    skipped rather than the whole analysis failing):
        {
            "revenue": float, "cogs": float, "operating_expenses": float,
            "net_income": float, "current_assets": float, "inventory": float,
            "current_liabilities": float, "total_debt": float, "equity": float,
            "accounts_receivable": float,
        }
    """

    def __init__(self, ollama_host: str = OLLAMA_HOST, ollama_model: str = OLLAMA_MODEL):
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def analyze(
        self, client_name: str, period: str, statement: Dict[str, Any],
        prior_statement: Optional[Dict[str, Any]] = None,
        benchmarks: Optional[Dict[str, float]] = None,
    ) -> AnalysisReport:
        benchmarks = {**DEFAULT_BENCHMARKS, **(benchmarks or {})}

        ratios = self.compute_ratios(statement)
        prior_ratios = self.compute_ratios(prior_statement) if prior_statement else None

        anomalies = self.detect_anomalies(ratios, prior_ratios, benchmarks)

        narrative_method, letter_text = self._generate_narrative(client_name, period, ratios, prior_ratios, anomalies)

        return AnalysisReport(
            client_name=client_name, period=period, ratios=ratios, prior_ratios=prior_ratios,
            benchmark=benchmarks, anomalies=anomalies, narrative_method=narrative_method,
            letter_text=letter_text, generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Ratio calculation (deterministic)
    # ------------------------------------------------------------------

    def compute_ratios(self, statement: Dict[str, Any]) -> RatioSet:
        revenue = self._num(statement.get("revenue"))
        cogs = self._num(statement.get("cogs"))
        opex = self._num(statement.get("operating_expenses"))
        net_income = self._num(statement.get("net_income"))
        current_assets = self._num(statement.get("current_assets"))
        inventory = self._num(statement.get("inventory"))
        current_liabilities = self._num(statement.get("current_liabilities"))
        total_debt = self._num(statement.get("total_debt"))
        equity = self._num(statement.get("equity"))
        receivables = self._num(statement.get("accounts_receivable"))

        gross_margin_pct = None
        if revenue and cogs is not None:
            gross_margin_pct = round(((revenue - cogs) / revenue) * 100, 2)

        net_margin_pct = None
        if revenue:
            if net_income is None and revenue is not None and cogs is not None and opex is not None:
                net_income = revenue - cogs - opex
            if net_income is not None:
                net_margin_pct = round((net_income / revenue) * 100, 2)

        current_ratio = None
        if current_liabilities:
            current_ratio = round(current_assets / current_liabilities, 2) if current_assets is not None else None

        quick_ratio = None
        if current_liabilities and current_assets is not None:
            quick_assets = current_assets - (inventory or 0.0)
            quick_ratio = round(quick_assets / current_liabilities, 2)

        dso_days = None
        if revenue and receivables is not None:
            dso_days = round((receivables / revenue) * 365, 1)

        debt_to_equity = None
        if equity:
            debt_to_equity = round(total_debt / equity, 2) if total_debt is not None else None

        return RatioSet(
            revenue=revenue, gross_margin_pct=gross_margin_pct, net_margin_pct=net_margin_pct,
            current_ratio=current_ratio, quick_ratio=quick_ratio, dso_days=dso_days,
            debt_to_equity=debt_to_equity,
        )

    # ------------------------------------------------------------------
    # Anomaly detection (deterministic)
    # ------------------------------------------------------------------

    def detect_anomalies(
        self, ratios: RatioSet, prior_ratios: Optional[RatioSet], benchmarks: Dict[str, float],
    ) -> List[Anomaly]:
        anomalies: List[Anomaly] = []

        # Cash strain -- current ratio below the "can cover short-term
        # liabilities" threshold, regardless of prior period/benchmark.
        if ratios.current_ratio is not None and ratios.current_ratio < ANOMALY_CASH_STRAIN_CURRENT_RATIO:
            anomalies.append(Anomaly(
                metric="current_ratio", severity="alert",
                message=(
                    f"Current ratio is {ratios.current_ratio}, below {ANOMALY_CASH_STRAIN_CURRENT_RATIO} -- "
                    f"current liabilities exceed current assets, a sign of potential cash strain."
                ),
                current_value=ratios.current_ratio, reference_value=ANOMALY_CASH_STRAIN_CURRENT_RATIO,
                reference_type="benchmark",
            ))

        # Declining margins vs. prior period.
        if prior_ratios is not None:
            for field, label in (("gross_margin_pct", "Gross margin"), ("net_margin_pct", "Net margin")):
                current_val = getattr(ratios, field)
                prior_val = getattr(prior_ratios, field)
                if current_val is None or prior_val is None:
                    continue
                delta = current_val - prior_val
                if delta <= -ANOMALY_MARGIN_DECLINE_PCT_POINTS:
                    anomalies.append(Anomaly(
                        metric=field, severity="warning",
                        message=f"{label} fell {abs(delta):.1f} points vs. the prior period ({prior_val}% -> {current_val}%).",
                        current_value=current_val, reference_value=prior_val, reference_type="prior_period",
                    ))

            # Worsening DSO (collections slowing down).
            if ratios.dso_days is not None and prior_ratios.dso_days is not None:
                delta = ratios.dso_days - prior_ratios.dso_days
                if delta >= ANOMALY_DSO_INCREASE_DAYS:
                    anomalies.append(Anomaly(
                        metric="dso_days", severity="warning",
                        message=(
                            f"Days Sales Outstanding increased by {delta:.0f} days vs. the prior period "
                            f"({prior_ratios.dso_days} -> {ratios.dso_days}) -- collections are slowing down."
                        ),
                        current_value=ratios.dso_days, reference_value=prior_ratios.dso_days,
                        reference_type="prior_period",
                    ))

        # Below-benchmark comparisons (only when no prior-period anomaly
        # already covered the same metric, to avoid double-flagging).
        flagged_metrics = {a.metric for a in anomalies}
        for field, label in (("gross_margin_pct", "Gross margin"), ("net_margin_pct", "Net margin")):
            if field in flagged_metrics:
                continue
            current_val = getattr(ratios, field)
            benchmark_val = benchmarks.get(field)
            if current_val is None or benchmark_val is None:
                continue
            if current_val <= benchmark_val - ANOMALY_MARGIN_DECLINE_PCT_POINTS:
                anomalies.append(Anomaly(
                    metric=field, severity="warning",
                    message=f"{label} ({current_val}%) is below the industry benchmark ({benchmark_val}%).",
                    current_value=current_val, reference_value=benchmark_val, reference_type="benchmark",
                ))

        return anomalies

    # ------------------------------------------------------------------
    # Narrative generation -- local SLM, with a deterministic fallback
    # ------------------------------------------------------------------

    def _generate_narrative(
        self, client_name: str, period: str, ratios: RatioSet,
        prior_ratios: Optional[RatioSet], anomalies: List[Anomaly],
    ) -> Tuple[str, str]:
        if _OLLAMA_AVAILABLE:
            try:
                prompt = self._build_prompt(client_name, period, ratios, prior_ratios, anomalies)
                raw = call_ollama(prompt, model=self.ollama_model, host=self.ollama_host, num_predict=500)
                text = raw.strip()
                if len(text) > 20:
                    return "model", text
                logger.warning("Model narrative came back too short -- using template fallback.")
            except Exception as e:
                logger.warning(f"Narrative model call failed ({e}) -- using template fallback.")

        return "template", self._template_narrative(client_name, period, ratios, anomalies)

    def _build_prompt(
        self, client_name: str, period: str, ratios: RatioSet,
        prior_ratios: Optional[RatioSet], anomalies: List[Anomaly],
    ) -> str:
        anomaly_lines = "\n".join(f"- {a.message}" for a in anomalies) or "- None detected."
        prior_line = f"Prior period ratios: {prior_ratios.model_dump()}" if prior_ratios else "No prior period available for comparison."

        return f"""You are an accountant writing a short advisory letter to a small-business client, in plain,
non-technical language. Do not invent numbers beyond what is given below.

Client: {client_name}
Period: {period}
Current ratios: {ratios.model_dump()}
{prior_line}

Detected issues:
{anomaly_lines}

Write a short letter (150-250 words) that:
1. Summarizes financial health in plain language (no jargon like "DSO" without explaining it).
2. Calls out each detected issue above and what it means practically.
3. Ends with 1-3 concrete, actionable recommendations.
4. Uses a professional but warm tone, addressed to the client by name.

Letter:"""

    def _template_narrative(self, client_name: str, period: str, ratios: RatioSet, anomalies: List[Anomaly]) -> str:
        lines = [f"Dear {client_name},", "", f"Here is a summary of your financial performance for {period}."]

        if ratios.net_margin_pct is not None:
            lines.append(f"Your net margin was {ratios.net_margin_pct}% of revenue.")
        if ratios.gross_margin_pct is not None:
            lines.append(f"Your gross margin was {ratios.gross_margin_pct}%.")
        if ratios.current_ratio is not None:
            lines.append(f"Your current ratio (ability to cover short-term obligations) was {ratios.current_ratio}.")

        if anomalies:
            lines.append("")
            lines.append("A few things worth your attention:")
            for a in anomalies:
                lines.append(f"- {a.message}")
            lines.append("")
            lines.append("Recommendation: review the items above with your accountant and consider adjusting pricing, "
                          "collections follow-up, or expense management accordingly.")
        else:
            lines.append("")
            lines.append("No significant anomalies were detected this period. Keep up the good work.")

        lines.append("")
        lines.append("Best regards,")
        lines.append("Your accounting team")
        return "\n".join(lines)

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


# ----------------------------------------------------------------------
# Export helpers
# ----------------------------------------------------------------------

def export_text(report: AnalysisReport) -> str:
    return report.letter_text


def export_pdf(report: AnalysisReport, path: str) -> bool:
    """
    Best-effort PDF export via reportlab. Returns False (and logs a
    clear reason) instead of raising if reportlab isn't installed --
    the text/API path never depends on PDF export working.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm
    except ImportError:
        logger.warning("reportlab not installed -- skipping PDF export. `pip install reportlab` to enable it.")
        return False

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    text_obj = c.beginText(2 * cm, height - 2 * cm)
    text_obj.setFont("Helvetica", 11)
    for line in report.letter_text.splitlines():
        for wrapped in _wrap(line, 90):
            text_obj.textLine(wrapped)
    c.drawText(text_obj)
    c.save()
    return True


def _wrap(line: str, width: int) -> List[str]:
    if not line:
        return [""]
    return re.findall(f".{{1,{width}}}(?:\\s+|$)", line) or [line]


# ----------------------------------------------------------------------
# Quick manual test: two synthetic periods showing declining margins,
# worsening DSO, and cash strain -- should trip all three anomaly types
# and produce a letter (template narrative if Ollama isn't running).
# Run: python report_agent.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    agent = FinancialAnalysisAgent()

    prior_statement = {
        "revenue": 500_000, "cogs": 280_000, "operating_expenses": 150_000,
        "current_assets": 120_000, "inventory": 20_000, "current_liabilities": 80_000,
        "total_debt": 100_000, "equity": 150_000, "accounts_receivable": 60_000,
    }
    current_statement = {
        "revenue": 480_000, "cogs": 300_000, "operating_expenses": 155_000,
        "current_assets": 70_000, "inventory": 15_000, "current_liabilities": 90_000,
        "total_debt": 110_000, "equity": 140_000, "accounts_receivable": 90_000,
    }

    report = agent.analyze("Rossi Impianti Srl", "2026-Q2", current_statement, prior_statement)

    print(" Ratios ===")
    print(report.ratios.model_dump_json(indent=2))
    print("\n=== Anomalies ===")
    for a in report.anomalies:
        print(f"[{a.severity}] {a.message}")
    print(f"\n=== Letter (method={report.narrative_method}) ===")
    print(report.letter_text)

    assert any(a.metric == "current_ratio" for a in report.anomalies), "expected cash-strain alert"
    assert any(a.metric == "dso_days" for a in report.anomalies), "expected DSO warning"
    assert len(report.anomalies) >= 2

    print("\nreport_agent.py self-test passed.")
