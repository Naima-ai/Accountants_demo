"""
demo3_orchestrator.py

My Demo 3 deliverable: "Report_agent.py, Reporting/orchestration/API
integration" (report_agent.py itself lives in demo_3/, this file is
the orchestration glue).

Pulls the prior period's statement from memory automatically (so
callers only ever hand in the current period), runs it through
report_agent.py's FinancialAnalysisAgent, and persists the result --
so a second call for the same client naturally has something to
compare against, same growth pattern as Demo 1's supplier learning.

Usage:
    from demo3_orchestrator import ReportOrchestrator

    orch = ReportOrchestrator()
    report = orch.generate_report("c-001", "2026-Q2", statement)
"""

import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("demo3_orchestrator")
logging.basicConfig(level=logging.INFO)

_CURR_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_CURR_DIR)
_BASE_DIR = os.path.dirname(_SRC_DIR)
_DEMO_3_DIR = os.path.join(_BASE_DIR, "demo_3")

for path in (_SRC_DIR, os.path.join(_SRC_DIR, "memory"), os.path.join(_SRC_DIR, "database"), _DEMO_3_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from ..memory.memory import MemoryStore  # noqa: E402


class ReportOrchestrator:
    """Orchestrates Demo 3: fetch prior period -> analyze -> persist -> return."""

    def __init__(self, memory: Optional[MemoryStore] = None, agent: Optional[Any] = None):
        from report_agent import FinancialAnalysisAgent

        self.memory = memory or MemoryStore()
        self.agent = agent or FinancialAnalysisAgent()

    def generate_report(
        self, client_id: str, period: str, statement: Dict[str, Any],
        statement_type: str = "income_statement", benchmarks: Optional[Dict[str, float]] = None,
        store_statement: bool = True,
    ) -> Dict[str, Any]:
        client = self.memory.get_client(client_id)
        if client is None:
            raise ValueError(f"Unknown client_id: {client_id}")

        prior = self.memory.get_prior_statement(client_id, period, statement_type)
        prior_statement = prior["data"] if prior else None

        report = self.agent.analyze(
            client_name=client["name"], period=period, statement=statement,
            prior_statement=prior_statement, benchmarks=benchmarks,
        )

        if store_statement:
            self.memory.store_financial_statement(client_id, period, statement, statement_type)

        report_id = self.memory.store_report(
            client_id=client_id, period=period,
            ratios=report.ratios.model_dump(), anomalies=[a.model_dump() for a in report.anomalies],
            letter_text=report.letter_text,
        )

        if any(a.severity == "alert" for a in report.anomalies):
            self.memory.flag_for_review(
                "demo_3", "analysis_report", report_id,
                "Report contains at least one alert-severity anomaly.",
            )

        result = report.to_dict()
        result["report_id"] = report_id
        result["compared_to_prior"] = prior_statement is not None
        return result


# ----------------------------------------------------------------------
# Quick manual test: generates a report for Q1 (no prior period), then
# a report for Q2 (should automatically pick up Q1 from memory as the
# prior period for comparison).
# Run: python demo3_orchestrator.py
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

    mem = MemoryStore()
    mem.upsert_client("c-001", "Rossi Impianti Srl")
    orch = ReportOrchestrator(memory=mem)

    q1_statement = {
        "revenue": 500_000, "cogs": 280_000, "operating_expenses": 150_000,
        "current_assets": 120_000, "inventory": 20_000, "current_liabilities": 80_000,
        "total_debt": 100_000, "equity": 150_000, "accounts_receivable": 60_000,
    }
    q2_statement = {
        "revenue": 480_000, "cogs": 300_000, "operating_expenses": 155_000,
        "current_assets": 70_000, "inventory": 15_000, "current_liabilities": 90_000,
        "total_debt": 110_000, "equity": 140_000, "accounts_receivable": 90_000,
    }

    print("=== Q1 report (no prior period yet) ===")
    q1_report = orch.generate_report("c-001", "2026-Q1", q1_statement)
    print(f"compared_to_prior: {q1_report['compared_to_prior']}")
    print(f"anomalies: {len(q1_report['anomalies'])}")
    assert q1_report["compared_to_prior"] is False

    print("\n=== Q2 report (Q1 should be picked up automatically) ===")
    q2_report = orch.generate_report("c-001", "2026-Q2", q2_statement)
    print(f"compared_to_prior: {q2_report['compared_to_prior']}")
    print(f"anomalies: {len(q2_report['anomalies'])}")
    assert q2_report["compared_to_prior"] is True
    assert len(q2_report["anomalies"]) >= 2

    print("\nStored reports for c-001:", len(mem.get_reports("c-001")))
    print("Review queue:", mem.list_review_queue("demo_3"))

    print("\ndemo3_orchestrator.py self-test passed.")
