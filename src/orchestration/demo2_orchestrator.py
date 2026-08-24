"""
demo2_orchestrator.py

My Demo 2 deliverable: "memory/status integration, orchestration".

Cross-checks each client's document checklist (via memory.py) against
what's been received, and for anything missing, asks demo_2's reminder
agent to draft + "send" a personalized reminder, then logs the result
back into memory so status/dashboard queries stay accurate.

demo_2/reminder_agent.py is Chrislin/Harith's file and isn't implemented
yet (it's currently an empty stub). Rather than block on that, this
orchestrator defines the contract it expects (draft_reminder /
send_reminder) and falls back to a minimal built-in template agent when
the real one isn't available -- so the orchestration + memory/status
path is fully testable today, and swaps to the real agent automatically
the moment demo_2/reminder_agent.py implements ReminderAgent.

Usage:
    from demo2_orchestrator import ReminderOrchestrator

    orch = ReminderOrchestrator()
    orch.memory.seed_expected_documents("c-001", "2026-07", ["bank_statement", "sales_invoices"])
    result = orch.run_for_client("c-001", "2026-07")
    summary = orch.run_for_roster(["c-001", "c-002"], "2026-07")
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("demo2_orchestrator")
logging.basicConfig(level=logging.INFO)

_CURR_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_CURR_DIR)
_BASE_DIR = os.path.dirname(_SRC_DIR)
_DEMO_2_DIR = os.path.join(_BASE_DIR, "demo_2")

for path in (_SRC_DIR, os.path.join(_SRC_DIR, "memory"), os.path.join(_SRC_DIR, "database"), _DEMO_2_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from config import REMINDER_DEFAULT_CHANNEL, REMINDER_MAX_FOLLOWUPS, REMINDER_MANUAL_MINUTES_PER_DOC
except ImportError:
    REMINDER_DEFAULT_CHANNEL, REMINDER_MAX_FOLLOWUPS, REMINDER_MANUAL_MINUTES_PER_DOC = "email", 3, 12.0

from memory import MemoryStore  # noqa: E402


class FallbackReminderAgent:
    """
    Minimal template-based stand-in for demo_2/reminder_agent.py, used
    only until that file implements ReminderAgent. Keeps the same
    draft_reminder(client, missing_doc, follow_up_number) -> str
    contract so swapping in the real agent (an SLM-drafted, per-client-
    tone message) requires no orchestrator changes.
    """

    _DOC_TYPE_LABELS = {
        "bank_statement": "the latest bank statement",
        "sales_invoices": "this period's sales invoices",
        "purchase_invoices": "this period's purchase invoices",
        "payroll": "the payroll documentation",
    }

    def draft_reminder(self, client: Dict[str, Any], missing_doc: Dict[str, Any], follow_up_number: int) -> str:
        label = self._DOC_TYPE_LABELS.get(missing_doc["doc_type"], missing_doc["doc_type"].replace("_", " "))
        tone = (client.get("preferred_tone") or "formal").lower()
        name = client.get("name", "there")

        if follow_up_number == 1:
            if tone == "friendly":
                return f"Hi {name}! Quick nudge — could you send over {label} for {missing_doc['period']} when you get a chance?"
            return f"Dear {name}, we are still awaiting {label} for the period {missing_doc['period']}. Please provide it at your earliest convenience."

        if tone == "friendly":
            return f"Hi {name}, following up again — we still need {label} for {missing_doc['period']}. Let us know if you're missing anything on your end!"
        return (
            f"Dear {name}, this is follow-up #{follow_up_number} regarding {label} for {missing_doc['period']}, "
            f"which remains outstanding. Please send it as soon as possible so we can keep your books current."
        )

    def send_reminder(self, client: Dict[str, Any], channel: str, message: str) -> bool:
        # Demo/simulated send -- no real email/PEC/WhatsApp integration
        # here, matching the brief's "follow-up loop (simulated)" build step.
        logger.info(f"[SIMULATED SEND via {channel}] to {client.get('name')}: {message}")
        return True


class ReminderOrchestrator:
    """Orchestrates Demo 2: gap detection -> drafting -> simulated send -> status update."""

    def __init__(self, memory: Optional[MemoryStore] = None, reminder_agent: Optional[Any] = None):
        self.memory = memory or MemoryStore()
        self.agent = reminder_agent or self._load_reminder_agent()

    @staticmethod
    def _load_reminder_agent() -> Any:
        try:
            from reminder_agent import ReminderAgent  # demo_2/reminder_agent.py
            logger.info("Using real ReminderAgent from demo_2/reminder_agent.py")
            return ReminderAgent()
        except Exception as e:
            logger.info(f"demo_2/reminder_agent.py not available yet ({e}) -- using FallbackReminderAgent")
            return FallbackReminderAgent()

    def run_for_client(self, client_id: str, period: str) -> Dict[str, Any]:
        client = self.memory.get_client(client_id)
        if client is None:
            raise ValueError(f"Unknown client_id: {client_id}")

        missing = self.memory.get_missing_documents(client_id, period)
        reminders_sent = []

        for missing_doc in missing:
            history = [
                r for r in self.memory.get_reminder_history(client_id)
                if r["expected_document_id"] == missing_doc["id"]
            ]
            follow_up_number = len(history) + 1
            if follow_up_number > REMINDER_MAX_FOLLOWUPS:
                logger.info(
                    f"[{client_id}] {missing_doc['doc_type']} exceeded max follow-ups "
                    f"({REMINDER_MAX_FOLLOWUPS}) -- escalating instead of sending again."
                )
                self.memory.flag_for_review(
                    "demo_2", "expected_document", missing_doc["id"],
                    f"No response after {REMINDER_MAX_FOLLOWUPS} reminders for {missing_doc['doc_type']} ({period}).",
                )
                continue

            message = self.agent.draft_reminder(client, missing_doc, follow_up_number)
            self.agent.send_reminder(client, REMINDER_DEFAULT_CHANNEL, message)

            log_entry = self.memory.log_reminder(
                client_id=client_id, expected_document_id=missing_doc["id"],
                channel=REMINDER_DEFAULT_CHANNEL, message=message,
                tone=client.get("preferred_tone"), follow_up_number=follow_up_number,
            )
            reminders_sent.append({
                "doc_type": missing_doc["doc_type"], "follow_up_number": follow_up_number,
                "message": message, **log_entry,
            })

        return {
            "client_id": client_id, "client_name": client["name"], "period": period,
            "missing_count": len(missing), "reminders_sent": len(reminders_sent),
            "reminders": reminders_sent,
        }

    def run_for_roster(self, client_ids: List[str], period: str) -> Dict[str, Any]:
        """
        The "'run' on 50 clients" demo beat: process every client's
        checklist in one call and report how much manual chasing time
        this replaced.
        """
        per_client = []
        total_reminders = 0
        for client_id in client_ids:
            try:
                result = self.run_for_client(client_id, period)
            except ValueError as e:
                logger.warning(str(e))
                continue
            per_client.append(result)
            total_reminders += result["reminders_sent"]

        hours_saved = round((total_reminders * REMINDER_MANUAL_MINUTES_PER_DOC) / 60, 1)

        return {
            "period": period,
            "clients_processed": len(per_client),
            "total_reminders_sent": total_reminders,
            "estimated_hours_saved": hours_saved,
            "clients": per_client,
        }


# ----------------------------------------------------------------------
# Quick manual test: seeds a 3-client roster with a mix of complete and
# missing documents, runs the roster, and checks the dashboard reflects
# it. Run: python demo2_orchestrator.py
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
    period = "2026-07"
    roster = [
        ("c-001", "Rossi Impianti Srl", "friendly"),
        ("c-002", "Bianchi Consulting Srl", "formal"),
        ("c-003", "Verdi Logistica Srl", "formal"),
    ]
    for client_id, name, tone in roster:
        mem.upsert_client(client_id, name, preferred_tone=tone)
        mem.seed_expected_documents(client_id, period, ["bank_statement", "sales_invoices", "payroll"])

    # c-001: fully up to date. c-002/c-003: missing documents.
    mem.mark_document_received("c-001", period, "bank_statement")
    mem.mark_document_received("c-001", period, "sales_invoices")
    mem.mark_document_received("c-001", period, "payroll")
    mem.mark_document_received("c-002", period, "sales_invoices")

    orch = ReminderOrchestrator(memory=mem)
    summary = orch.run_for_roster([cid for cid, _, _ in roster], period)

    print(f"Clients processed: {summary['clients_processed']}")
    print(f"Total reminders sent: {summary['total_reminders_sent']}")
    print(f"Estimated hours saved: {summary['estimated_hours_saved']}")
    for c in summary["clients"]:
        print(f"  - {c['client_name']}: missing={c['missing_count']}, reminders_sent={c['reminders_sent']}")

    assert summary["clients"][0]["reminders_sent"] == 0  # c-001 fully up to date
    assert summary["clients"][1]["reminders_sent"] == 2  # c-002 missing 2

    print("\nDashboard:", mem.dashboard_status(period))
    print("\ndemo2_orchestrator.py self-test passed.")
