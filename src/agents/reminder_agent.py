"""
reminder_agent.py

Demo 2 Reminder Agent.

Responsibilities:
    - Draft personalized reminder messages using the shared local SLM.
    - Send reminders through the configured email channel using SMTP.
    - Fall back to a deterministic message if the local SLM is unavailable.

The orchestration, missing-document detection, follow-up counting,
escalation, and memory/status updates remain in
src/orchestration/demo2_orchestrator.py.
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Dict

from ..config import (
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USERNAME,
)
from ..llm.slm_client import call_llm

logger = logging.getLogger("reminder_agent")


class ReminderAgent:
    """Generates and sends personalized document reminders."""

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
        smtp_use_tls: bool | None = None,
    ):
        self.smtp_host = smtp_host or SMTP_HOST
        self.smtp_port = smtp_port or SMTP_PORT
        self.smtp_username = smtp_username or SMTP_USERNAME
        self.smtp_password = smtp_password or SMTP_PASSWORD
        self.smtp_from = smtp_from or SMTP_FROM or self.smtp_username
        self.smtp_use_tls = (
            SMTP_USE_TLS if smtp_use_tls is None else smtp_use_tls
        )

    def draft_reminder(
        self,
        client: Dict[str, Any],
        missing_doc: Dict[str, Any],
        follow_up_number: int,
    ) -> str:
        """Generate a short reminder using the shared local SLM."""

        client_name = client.get("name") or "there"
        preferred_tone = client.get("preferred_tone") or "formal"
        doc_type = missing_doc.get("doc_type") or "document"
        period = missing_doc.get("period") or "the current period"

        document = str(doc_type).replace("_", " ")

        prompt = f"""
You are an accounting document collection assistant.

Draft a short, professional email reminder to a client requesting
one missing accounting document.

Client name: {client_name}
Preferred tone: {preferred_tone}
Missing document: {document}
Accounting period: {period}
Follow-up number: {follow_up_number}

Rules:
- Return only the email body.
- Be concise, polite, and professional.
- Match the requested tone.
- Mention only the missing document and accounting period above.
- Do not invent deadlines, amounts, attachments, contact details, or other facts.
- Do not claim the document was received.
- Do not use placeholders such as [Your Name].
"""

        try:
            message = call_llm(
                prompt,
                num_predict=220,
                temperature=0.2,
            ).strip()

            if message:
                return message

        except Exception as exc:
            logger.warning("Local SLM reminder generation failed: %s", exc)

        return self._fallback_message(
            client_name=client_name,
            document=document,
            period=period,
            tone=preferred_tone,
            follow_up_number=follow_up_number,
        )

    def send_reminder(
        self,
        client: Dict[str, Any],
        channel: str,
        message: str,
    ) -> bool:
        """
        Send a reminder through the configured channel.

        Currently the production-capable channel is email via SMTP.
        Returns True only after SMTP accepts the message for delivery.
        """

        if channel.lower() != "email":
            logger.error("Unsupported reminder channel: %s", channel)
            return False

        recipient = (client.get("email") or "").strip()
        if not recipient:
            logger.error(
                "Cannot send reminder to %s: no email address configured.",
                client.get("name", "unknown"),
            )
            return False

        if not self.smtp_host:
            logger.error("SMTP_HOST is not configured.")
            return False

        if not self.smtp_from:
            logger.error("SMTP_FROM is not configured.")
            return False

        email = EmailMessage()
        email["From"] = self.smtp_from
        email["To"] = recipient
        email["Subject"] = "Reminder: Missing accounting document"
        email.set_content(message)

        try:
            with smtplib.SMTP(
                self.smtp_host,
                self.smtp_port,
                timeout=30,
            ) as server:
                server.ehlo()

                if self.smtp_use_tls:
                    server.starttls()
                    server.ehlo()

                if self.smtp_username:
                    server.login(self.smtp_username, self.smtp_password)

                server.sendmail(
                    self.smtp_username,
                    [recipient],
                    email.as_string(),
                )

            logger.info(
                "[EMAIL SENT] to %s <%s>",
                client.get("name", "unknown"),
                recipient,
            )
            return True

        except Exception as exc:
            logger.exception(
                "Reminder delivery failed for %s <%s>: %s",
                client.get("name", "unknown"),
                recipient,
                exc,
            )
            return False

    @staticmethod
    def _fallback_message(
        client_name: str,
        document: str,
        period: str,
        tone: str,
        follow_up_number: int,
    ) -> str:
        """Deterministic fallback when the local SLM cannot generate text."""

        if tone.lower() == "friendly":
            if follow_up_number == 1:
                return (
                    f"Hi {client_name},\n\n"
                    f"Quick reminder — could you send over the {document} "
                    f"for {period} when you get a chance?\n\n"
                    "Thank you."
                )

            return (
                f"Hi {client_name},\n\n"
                f"Just following up again regarding the {document} for "
                f"{period}. Please send it when you get a chance.\n\n"
                "Thank you."
            )

        if follow_up_number == 1:
            return (
                f"Dear {client_name},\n\n"
                f"We are still awaiting the {document} for the period "
                f"{period}. Please provide it at your earliest convenience.\n\n"
                "Best regards"
            )

        return (
            f"Dear {client_name},\n\n"
            f"This is follow-up #{follow_up_number} regarding the "
            f"{document} for {period}, which remains outstanding. "
            "Please provide it as soon as possible.\n\n"
            "Best regards"
        )


if __name__ == "__main__":
    print("ReminderAgent loaded successfully.")