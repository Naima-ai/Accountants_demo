"""
demo3_orchestrator.py

My Demo 3 deliverable: "Report_agent.py, Reporting/orchestration/API
integration" (report_agent.py itself lives in src/agents/, this file is
the orchestration glue).

Pulls the prior period's statement from memory automatically (so
callers only ever hand in the current period), runs it through
report_agent.py's FinancialAnalysisAgent, and persists the result --
so a second call for the same client naturally has something to
compare against, same growth pattern as Demo 1's supplier learning.

Regenerating for the SAME client/period replaces the previously stored
report instead of piling up a duplicate -- store_report() itself has
no existence check, so without this every re-generate (or every click
of "Generate advisory report" against the same data) added another row.

Usage:
    from src.orchestration.demo3_orchestrator import ReportOrchestrator

    orch = ReportOrchestrator()
    report = orch.generate_report("c-001", "2026-Q2", statement)
"""

import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("demo3_orchestrator")
logging.basicConfig(level=logging.INFO)

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly (python src/orchestration/demo3_orchestrator.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.memory.memory import MemoryStore


class ReportOrchestrator:
    """Orchestrates Demo 3: fetch prior period -> analyze -> persist -> return."""

    def __init__(self, memory: Optional[MemoryStore] = None, agent: Optional[Any] = None):
        from src.agents.report_agent import FinancialAnalysisAgent

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

        # Regenerating for this exact client/period replaces the old report rather than inserting a duplicate 
        self.memory.delete_reports_for_period(client_id, period)

        report_id = self.memory.store_report(
            client_id=client_id, period=period,
            ratios=report.ratios.model_dump(), anomalies=[a.model_dump() for a in report.anomalies],
            letter_text=report.letter_text,
        )

        if any(a.severity == "alert" for a in report.anomalies):
            self.memory.flag_for_review(
                "demo_3", "analysis_report", report_id,
                f"Report contains at least one alert-severity anomaly. Client: {client['name']} ({client_id}), period {period}.",
            )

        result = report.to_dict()
        result["report_id"] = report_id
        result["compared_to_prior"] = prior_statement is not None
        return result


# ----------------------------------------------------------------------
# Quick manual test: generates a report for Q1 (no prior period), then
# a report for Q2 (should automatically pick up Q1 from memory as the
# prior period for comparison). Also confirms regenerating Q1 replaces
# the stored report instead of duplicating it.
# Run: python demo3_orchestrator.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    import importlib
    import src.config as _config
    importlib.reload(_config)
    import src.database.database as _database
    importlib.reload(_database)
    import src.memory.memory as _memory
    importlib.reload(_memory)
    from src.memory.memory import MemoryStore  # noqa: F811, E402

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
    assert q1_report["compared_to_prior"] is False

    print("\n=== Q2 report (Q1 should be picked up automatically) ===")
    q2_report = orch.generate_report("c-001", "2026-Q2", q2_statement)
    print(f"compared_to_prior: {q2_report['compared_to_prior']}")
    assert q2_report["compared_to_prior"] is True
    assert len(q2_report["anomalies"]) >= 2

    print("\n=== Re-generating Q1 should REPLACE, not duplicate ===")
    orch.generate_report("c-001", "2026-Q1", q1_statement)
    orch.generate_report("c-001", "2026-Q1", q1_statement)
    q1_reports = [r for r in mem.get_reports("c-001") if r["period"] == "2026-Q1"]
    print(f"Stored reports for 2026-Q1 after 3 total generate calls: {len(q1_reports)}")
    assert len(q1_reports) == 1

    print("\nStored reports for c-001:", len(mem.get_reports("c-001")))
    print("Review queue:", mem.list_review_queue("demo_3"))

    print("\ndemo3_orchestrator.py self-test passed.")