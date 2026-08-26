"""
reminder_agent.py

Demo 2 Reminder Agent.

Responsible for:
    - Drafting personalized reminder messages.
    - Sending reminders through the configured channel.

The orchestration, document-gap detection, follow-up counting,
and memory/status updates are handled by demo2_orchestrator.py.
"""

import logging
from typing import Any, Dict

from ..integrations.ollama_client import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    call_ollama,
)

logger = logging.getLogger("reminder_agent")


class ReminderAgent:
    """Generates and sends personalized document reminders."""

    def __init__(
        self,
        ollama_host: str = OLLAMA_HOST,
        ollama_model: str = OLLAMA_MODEL,
    ):
        self.ollama_host = ollama_host
        self.ollama_model = ollama_model

    def draft_reminder(
        self,
        client: Dict[str, Any],
        missing_doc: Dict[str, Any],
        follow_up_number: int,
    ) -> str:
        """
        Generate a personalized reminder message.

        Args:
            client: Client information from MemoryStore.
            missing_doc: Missing document information.
            follow_up_number: Current reminder number.
        """

        client_name = client.get("name", "there")
        preferred_tone = client.get("preferred_tone", "formal")
        doc_type = missing_doc.get("doc_type", "document")
        period = missing_doc.get("period", "the current period")

        prompt = f"""
You are an accounting document collection assistant.

Draft a short, professional reminder to a client requesting
a missing accounting document.

Client name:
{client_name}

Preferred tone:
{preferred_tone}

Missing document:
{doc_type}

Accounting period:
{period}

This is follow-up number:
{follow_up_number}

Rules:
- Be concise.
- Be polite and professional.
- Match the requested tone.
- Mention only the document provided above.
- Do not invent deadlines or additional information.
- Return only the message itself.
"""

        try:
            message = call_ollama(
                prompt,
                model=self.ollama_model,
                host=self.ollama_host,
                num_predict=250,
            )

            message = message.strip()

            if message:
                return message

        except Exception as exc:
            logger.warning(
                "Ollama reminder generation failed: %s",
                exc,
            )

        return self._fallback_message(
            client_name,
            doc_type,
            period,
            preferred_tone,
            follow_up_number,
        )

    def send_reminder(
        self,
        client: Dict[str, Any],
        channel: str,
        message: str,
    ) -> bool:
        """
        Simulate sending a reminder.

        Real email/PEC/WhatsApp integration can be added later.
        """

        logger.info(
            "[SIMULATED SEND via %s] to %s: %s",
            channel,
            client.get("name", "unknown"),
            message,
        )

        return True

    @staticmethod
    def _fallback_message(
        client_name: str,
        doc_type: str,
        period: str,
        tone: str,
        follow_up_number: int,
    ) -> str:

        document = doc_type.replace("_", " ")

        if tone.lower() == "friendly":
            if follow_up_number == 1:
                return (
                    f"Hi {client_name}! Quick reminder — could you send "
                    f"over the {document} for {period} when you get a chance?"
                )

            return (
                f"Hi {client_name}, just following up again regarding "
                f"the {document} for {period}. Please send it when "
                f"you get a chance."
            )

        if follow_up_number == 1:
            return (
                f"Dear {client_name}, we are still awaiting the "
                f"{document} for the period {period}. "
                f"Please provide it at your earliest convenience."
            )

        return (
            f"Dear {client_name}, this is follow-up #{follow_up_number} "
            f"regarding the {document} for {period}, which remains "
            f"outstanding. Please provide it as soon as possible."
        )