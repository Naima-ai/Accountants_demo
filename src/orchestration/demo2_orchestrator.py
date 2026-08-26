"""
demo2_orchestrator.py

Demo 2 orchestration:

    MemoryStore
        ↓
    Check client document checklist
        ↓
    Identify missing documents
        ↓
    ReminderAgent
        ↓
    Draft + send reminder
        ↓
    Log reminder
        ↓
    Dashboard/status

The ReminderAgent is responsible for drafting and sending reminders.
Memory/status management and orchestration remain here.
"""

import logging
from typing import Any, Dict, List, Optional

from ..agents.reminder_agent import ReminderAgent
from ..config import (
    REMINDER_DEFAULT_CHANNEL,
    REMINDER_MANUAL_MINUTES_PER_DOC,
    REMINDER_MAX_FOLLOWUPS,
)
from ..memory.memory import MemoryStore


logger = logging.getLogger("demo2_orchestrator")
logging.basicConfig(level=logging.INFO)


class FallbackReminderAgent:
    """
    Deterministic fallback used when the real ReminderAgent
    cannot generate or send a reminder.
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

        tone = (
            client.get("preferred_tone") or "formal"
        ).lower()

        name = client.get("name", "there")
        period = missing_doc["period"]

        if follow_up_number == 1:

            if tone == "friendly":
                return (
                    f"Hi {name}! Quick reminder — could you send over "
                    f"{label} for {period} when you get a chance?"
                )

            return (
                f"Dear {name}, we are still awaiting {label} for "
                f"the period {period}. Please provide it at your "
                f"earliest convenience."
            )

        if tone == "friendly":
            return (
                f"Hi {name}, following up again — we still need "
                f"{label} for {period}. Let us know if you're missing "
                f"anything on your end!"
            )

        return (
            f"Dear {name}, this is follow-up #{follow_up_number} "
            f"regarding {label} for {period}, which remains "
            f"outstanding. Please send it as soon as possible."
        )

    def send_reminder(
        self,
        client: Dict[str, Any],
        channel: str,
        message: str,
    ) -> bool:

        logger.info(
            "[SIMULATED SEND via %s] to %s: %s",
            channel,
            client.get("name", "unknown"),
            message,
        )

        return True


class ReminderOrchestrator:
    """
    Coordinates the Demo 2 document collection and reminder workflow.
    """

    def __init__(
        self,
        memory: Optional[MemoryStore] = None,
        reminder_agent: Optional[Any] = None,
    ):
        self.memory = memory or MemoryStore()
        self.agent = (
            reminder_agent
            or self._load_reminder_agent()
        )

    @staticmethod
    def _load_reminder_agent() -> Any:
        """
        Load the real ReminderAgent.

        The ReminderAgent itself contains its own fallback for
        Ollama/model failures. If the agent cannot be initialized
        at all, use the deterministic fallback.
        """

        try:

            agent = ReminderAgent()

            logger.info(
                "Using ReminderAgent from "
                "src/agents/reminder_agent.py"
            )

            return agent

        except Exception as exc:

            logger.warning(
                "Could not initialize ReminderAgent: %s. "
                "Using FallbackReminderAgent.",
                exc,
            )

            return FallbackReminderAgent()

    def run_for_client(
        self,
        client_id: str,
        period: str,
    ) -> Dict[str, Any]:
        """
        Process one client's document checklist.

        Steps:

            1. Load client.
            2. Find missing documents.
            3. Determine follow-up number.
            4. Generate reminder.
            5. Send reminder.
            6. Log reminder.
            7. Escalate after maximum follow-ups.
        """

        client = self.memory.get_client(client_id)

        if client is None:
            raise ValueError(
                f"Unknown client_id: {client_id}"
            )

        missing = self.memory.get_missing_documents(
            client_id,
            period,
        )

        reminders_sent: List[Dict[str, Any]] = []
        escalated: List[Dict[str, Any]] = []

        for missing_doc in missing:

            history = [
                reminder
                for reminder in self.memory.get_reminder_history(
                    client_id
                )
                if reminder["expected_document_id"]
                == missing_doc["id"]
            ]

            follow_up_number = len(history) + 1

            # ----------------------------------------------------------
            # Escalation
            # ----------------------------------------------------------

            if follow_up_number > REMINDER_MAX_FOLLOWUPS:

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
                    (
                        f"No response after "
                        f"{REMINDER_MAX_FOLLOWUPS} reminders for "
                        f"{missing_doc['doc_type']} "
                        f"({period})."
                    ),
                )

                escalated.append(
                    {
                        "doc_type": missing_doc["doc_type"],
                        "reason": (
                            f"Exceeded maximum of "
                            f"{REMINDER_MAX_FOLLOWUPS} follow-ups"
                        ),
                    }
                )

                continue

            # ----------------------------------------------------------
            # Draft reminder
            # ----------------------------------------------------------

            try:

                message = self.agent.draft_reminder(
                    client,
                    missing_doc,
                    follow_up_number,
                )

            except Exception as exc:

                logger.exception(
                    "Failed to draft reminder for %s: %s",
                    client_id,
                    exc,
                )

                fallback = FallbackReminderAgent()

                message = fallback.draft_reminder(
                    client,
                    missing_doc,
                    follow_up_number,
                )

            # ----------------------------------------------------------
            # Send reminder
            # ----------------------------------------------------------

            try:

                sent = self.agent.send_reminder(
                    client,
                    REMINDER_DEFAULT_CHANNEL,
                    message,
                )

            except Exception as exc:

                logger.exception(
                    "Failed to send reminder for %s: %s",
                    client_id,
                    exc,
                )

                sent = False

            if not sent:

                logger.warning(
                    "Reminder could not be sent to %s",
                    client.get("name", client_id),
                )

                continue

            # ----------------------------------------------------------
            # Persist reminder
            # ----------------------------------------------------------

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
        Process multiple clients for a single accounting period.
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

            total_reminders += result[
                "reminders_sent"
            ]

        hours_saved = round(
            (
                total_reminders
                * REMINDER_MANUAL_MINUTES_PER_DOC
            )
            / 60,
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
# Self-test
# ----------------------------------------------------------------------

if __name__ == "__main__":

    """
    IMPORTANT:

    The application database is intentionally not cleared here.

    Instead, the self-test uses unique client IDs so existing production/
    development reminder history cannot interfere with the assertions.

    This makes the test safe to run against the existing database.
    """

    period = "2026-07"

    # Unique IDs prevent contamination from previous self-test runs.
    roster = [
        (
            "selftest-c-001",
            "Selftest Rossi Srl",
            "friendly",
        ),
        (
            "selftest-c-002",
            "Selftest Bianchi Srl",
            "formal",
        ),
        (
            "selftest-c-003",
            "Selftest Verdi Srl",
            "formal",
        ),
    ]

    mem = MemoryStore()

    # --------------------------------------------------------------
    # Seed test clients
    # --------------------------------------------------------------

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

    # --------------------------------------------------------------
    # Client 1
    #
    # Complete — no reminders expected.
    # --------------------------------------------------------------

    mem.mark_document_received(
        "selftest-c-001",
        period,
        "bank_statement",
    )

    mem.mark_document_received(
        "selftest-c-001",
        period,
        "sales_invoices",
    )

    mem.mark_document_received(
        "selftest-c-001",
        period,
        "payroll",
    )

    # --------------------------------------------------------------
    # Client 2
    #
    # Received sales invoices.
    # Missing bank statement + payroll.
    # Expected reminders = 2.
    # --------------------------------------------------------------

    mem.mark_document_received(
        "selftest-c-002",
        period,
        "sales_invoices",
    )

    # --------------------------------------------------------------
    # Client 3
    #
    # Received nothing.
    # Missing all three.
    # Expected reminders = 3.
    # --------------------------------------------------------------

    orch = ReminderOrchestrator(
        memory=mem,
    )

    summary = orch.run_for_roster(
        [
            client_id
            for client_id, _, _ in roster
        ],
        period,
    )

    # --------------------------------------------------------------
    # Output
    # --------------------------------------------------------------

    print(
        f"Clients processed: "
        f"{summary['clients_processed']}"
    )

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
            f"reminders_sent={client['reminders_sent']}"
        )

    print(
        "\nDashboard:",
        mem.dashboard_status(period),
    )

    # --------------------------------------------------------------
    # Assertions
    # --------------------------------------------------------------

    assert summary["clients_processed"] == 3

    client_1 = summary["clients"][0]
    client_2 = summary["clients"][1]
    client_3 = summary["clients"][2]

    assert client_1["missing_count"] == 0
    assert client_1["reminders_sent"] == 0

    assert client_2["missing_count"] == 2
    assert client_2["reminders_sent"] == 2

    assert client_3["missing_count"] == 3
    assert client_3["reminders_sent"] == 3

    assert summary["total_reminders_sent"] == 5

    print(
        "\ndemo2_orchestrator.py self-test passed."
    )