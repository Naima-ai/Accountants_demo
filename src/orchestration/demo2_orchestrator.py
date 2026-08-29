"""
demo2_orchestrator.py

Demo 2 orchestration: document-gap detection -> reminder drafting ->
real channel delivery -> memory/status integration.

The ReminderAgent owns message generation and delivery.
This orchestrator owns workflow decisions, follow-up limits,
escalation, and persistence through MemoryStore.
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("demo2_orchestrator")
logging.basicConfig(level=logging.INFO)

# Makes `from src...` imports work whether this file is imported as part
# of the package or run directly.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config import (
    REMINDER_DEFAULT_CHANNEL,
    REMINDER_MANUAL_MINUTES_PER_DOC,
    REMINDER_MAX_FOLLOWUPS,
)
from src.memory.memory import MemoryStore


class FallbackReminderAgent:
    """
    Minimal fallback used only if the real ReminderAgent cannot be imported.

    It preserves the same interface as ReminderAgent so the orchestration
    layer remains testable even when an optional dependency is unavailable.
    """

    _DOC_TYPE_LABELS = {
        "bank_statement": "the latest bank statement",
        "sales_invoices": "this period's sales invoices",
        "purchase_invoices": "this period's purchase invoices",
        "payroll": "the payroll documentation",
    }

    def draft_reminder(
        self,
        client: Dict[str, Any],
        missing_doc: Dict[str, Any],
        follow_up_number: int,
    ) -> str:
        label = self._DOC_TYPE_LABELS.get(
            missing_doc["doc_type"],
            missing_doc["doc_type"].replace("_", " "),
        )
        tone = (client.get("preferred_tone") or "formal").lower()
        name = client.get("name", "there")
        period = missing_doc["period"]

        if follow_up_number == 1:
            if tone == "friendly":
                return (
                    f"Hi {name}! Quick reminder — could you send over "
                    f"{label} for {period} when you get a chance?"
                )

            return (
                f"Dear {name}, we are still awaiting {label} for the "
                f"period {period}. Please provide it at your earliest convenience."
            )

        if tone == "friendly":
            return (
                f"Hi {name}, just following up again regarding {label} "
                f"for {period}. Please send it when you get a chance."
            )

        return (
            f"Dear {name}, this is follow-up #{follow_up_number} regarding "
            f"{label} for {period}, which remains outstanding. "
            f"Please provide it as soon as possible."
        )

    def send_reminder(
        self,
        client: Dict[str, Any],
        channel: str,
        message: str,
    ) -> bool:
        logger.info(
            "[FALLBACK SIMULATED SEND via %s] to %s: %s",
            channel,
            client.get("name", "unknown"),
            message,
        )
        return True


class ReminderOrchestrator:
    """
    Orchestrates Demo 2:

        gap detection
            -> follow-up decision
            -> reminder drafting
            -> delivery
            -> memory update

    The orchestrator does not generate reminder text itself and does not
    access the database directly.
    """

    def __init__(
        self,
        memory: Optional[MemoryStore] = None,
        reminder_agent: Optional[Any] = None,
    ):
        self.memory = memory or MemoryStore()
        self.agent = reminder_agent or self._load_reminder_agent()

    @staticmethod
    def _load_reminder_agent() -> Any:
        try:
            from src.agents.reminder_agent import ReminderAgent

            logger.info(
                "Using ReminderAgent from src/agents/reminder_agent.py"
            )
            return ReminderAgent()

        except Exception as exc:
            logger.warning(
                "Could not load ReminderAgent (%s). "
                "Using fallback agent.",
                exc,
            )
            return FallbackReminderAgent()

    def run_for_client(
        self,
        client_id: str,
        period: str,
    ) -> Dict[str, Any]:
        """Process all missing documents for one client."""

        client = self.memory.get_client(client_id)

        if client is None:
            raise ValueError(f"Unknown client_id: {client_id}")

        missing = self.memory.get_missing_documents(
            client_id,
            period,
        )

        reminders_sent: List[Dict[str, Any]] = []
        escalated: List[Dict[str, Any]] = []

        for missing_doc in missing:
            history = [
                reminder
                for reminder in self.memory.get_reminder_history(client_id)
                if reminder["expected_document_id"] == missing_doc["id"]
            ]

            follow_up_number = len(history) + 1

            # Maximum follow-ups reached: stop sending and escalate.
            if follow_up_number > REMINDER_MAX_FOLLOWUPS:
                reason = (
                    f"No response after {REMINDER_MAX_FOLLOWUPS} "
                    f"reminders for {missing_doc['doc_type']} "
                    f"({period})."
                )

                logger.info(
                    "[%s] %s exceeded maximum follow-ups (%s). "
                    "Escalating instead of sending again.",
                    client_id,
                    missing_doc["doc_type"],
                    REMINDER_MAX_FOLLOWUPS,
                )

                self.memory.flag_for_review(
                    "demo_2",
                    "expected_document",
                    missing_doc["id"],
                    reason,
                )

                escalated.append(
                    {
                        "doc_type": missing_doc["doc_type"],
                        "follow_up_number": follow_up_number,
                        "reason": reason,
                    }
                )
                continue

            # Agent drafts the message.
            message = self.agent.draft_reminder(
                client,
                missing_doc,
                follow_up_number,
            )

            # Delivery MUST succeed before the reminder is persisted
            # as sent.
            sent = self.agent.send_reminder(
                client,
                REMINDER_DEFAULT_CHANNEL,
                message,
            )

            if not sent:
                reason = (
                    f"Failed to send reminder via "
                    f"{REMINDER_DEFAULT_CHANNEL}."
                )

                logger.error(
                    "[%s] Reminder delivery failed for %s.",
                    client_id,
                    missing_doc["doc_type"],
                )

                self.memory.flag_for_review(
                    "demo_2",
                    "expected_document",
                    missing_doc["id"],
                    reason,
                )

                escalated.append(
                    {
                        "doc_type": missing_doc["doc_type"],
                        "follow_up_number": follow_up_number,
                        "reason": reason,
                    }
                )
                continue

            # Only successful delivery reaches the reminder log.
            log_entry = self.memory.log_reminder(
                client_id=client_id,
                expected_document_id=missing_doc["id"],
                channel=REMINDER_DEFAULT_CHANNEL,
                message=message,
                tone=client.get("preferred_tone"),
                follow_up_number=follow_up_number,
            )

            reminders_sent.append(
                {
                    "doc_type": missing_doc["doc_type"],
                    "follow_up_number": follow_up_number,
                    "message": message,
                    **log_entry,
                }
            )

        return {
            "client_id": client_id,
            "client_name": client["name"],
            "period": period,
            "missing_count": len(missing),
            "reminders_sent": len(reminders_sent),
            "reminders": reminders_sent,
            "escalated": escalated,
        }

    def run_for_roster(
        self,
        client_ids: List[str],
        period: str,
    ) -> Dict[str, Any]:
        """
        Process multiple clients in one call and calculate the
        estimated manual time saved.
        """

        per_client: List[Dict[str, Any]] = []
        total_reminders = 0

        for client_id in client_ids:
            try:
                result = self.run_for_client(
                    client_id,
                    period,
                )
            except ValueError as exc:
                logger.warning(str(exc))
                continue

            per_client.append(result)
            total_reminders += result["reminders_sent"]

        hours_saved = round(
            (
                total_reminders
                * REMINDER_MANUAL_MINUTES_PER_DOC
            ) / 60,
            1,
        )

        return {
            "period": period,
            "clients_processed": len(per_client),
            "total_reminders_sent": total_reminders,
            "estimated_hours_saved": hours_saved,
            "clients": per_client,
        }


# ----------------------------------------------------------------------
# Quick manual test
#
# IMPORTANT:
# This self-test uses an injected fake agent so it does not attempt to
# send real email. Real SMTP is tested separately through ReminderAgent.
# ----------------------------------------------------------------------

if __name__ == "__main__":
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    import importlib
    import src.config as _config
    import src.database.database as _database
    import src.memory.memory as _memory

    importlib.reload(_config)
    importlib.reload(_database)
    importlib.reload(_memory)

    from src.memory.memory import MemoryStore

    class SelfTestReminderAgent:
        """Deterministic agent for the orchestrator self-test."""

        def draft_reminder(
            self,
            client: Dict[str, Any],
            missing_doc: Dict[str, Any],
            follow_up_number: int,
        ) -> str:
            return (
                f"TEST reminder #{follow_up_number}: "
                f"{missing_doc['doc_type']} for {missing_doc['period']}."
            )

        def send_reminder(
            self,
            client: Dict[str, Any],
            channel: str,
            message: str,
        ) -> bool:
            logger.info(
                "[SELF-TEST SEND via %s] to %s: %s",
                channel,
                client.get("name", "unknown"),
                message,
            )
            return True

    mem = MemoryStore()
    period = "2026-07"

    roster = [
        ("selftest-c-001", "Selftest Rossi Srl", "friendly"),
        ("selftest-c-002", "Selftest Bianchi Srl", "formal"),
        ("selftest-c-003", "Selftest Verdi Srl", "formal"),
    ]

    for client_id, name, tone in roster:
        mem.upsert_client(
            client_id,
            name,
            preferred_tone=tone,
        )
        mem.seed_expected_documents(
            client_id,
            period,
            [
                "bank_statement",
                "sales_invoices",
                "payroll",
            ],
        )

    # Client 1: complete.
    for doc_type in [
        "bank_statement",
        "sales_invoices",
        "payroll",
    ]:
        mem.mark_document_received(
            "selftest-c-001",
            period,
            doc_type,
        )

    # Client 2: missing two.
    mem.mark_document_received(
        "selftest-c-002",
        period,
        "sales_invoices",
    )

    orch = ReminderOrchestrator(
        memory=mem,
        reminder_agent=SelfTestReminderAgent(),
    )

    summary = orch.run_for_roster(
        [client_id for client_id, _, _ in roster],
        period,
    )

    print(f"Clients processed: {summary['clients_processed']}")
    print(
        f"Total reminders sent: "
        f"{summary['total_reminders_sent']}"
    )
    print(
        f"Estimated hours saved: "
        f"{summary['estimated_hours_saved']}"
    )

    for client in summary["clients"]:
        print(
            f"  - {client['client_name']}: "
            f"missing={client['missing_count']}, "
            f"reminders_sent={client['reminders_sent']}, "
            f"escalated={len(client['escalated'])}"
        )

    assert summary["clients_processed"] == 3
    assert summary["clients"][0]["reminders_sent"] == 0
    assert summary["clients"][1]["reminders_sent"] == 2
    assert summary["clients"][2]["reminders_sent"] == 3

    print("\nDashboard:", mem.dashboard_status(period))
    print("\ndemo2_orchestrator.py self-test passed.")
