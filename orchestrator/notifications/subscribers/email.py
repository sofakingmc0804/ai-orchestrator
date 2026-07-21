from __future__ import annotations

import os
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from orchestrator.config import Settings


# Configurable so this subscriber can notify a different owner in a
# non-Example deployment; default preserves the real operational recipient.
DEFAULT_NOTIFICATION_RECIPIENT = os.environ.get("ORCHESTRATOR_NOTIFICATION_EMAIL", "owner@example.com")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:80] or "notification"


class EmailDraftSubscriber:
    name = "email"
    channel = "email"

    def __init__(self, settings: Settings, recipient: str = DEFAULT_NOTIFICATION_RECIPIENT) -> None:
        self.settings = settings
        self.recipient = recipient
        self.outbox = settings.home / "email_drafts"
        self.outbox.mkdir(parents=True, exist_ok=True)

    async def deliver(self, notification: dict[str, Any]) -> dict[str, Any]:
        severity = str(notification.get("severity") or "info")
        title = str(notification.get("title") or "Orchestrator notification")
        project = str(notification.get("project_id") or "machine")
        message = EmailMessage()
        message["To"] = self.recipient
        message["From"] = "ai-orchestrator@local"
        message["Subject"] = f"[{severity}] {project}: {title}"
        body = "\n".join(
            [
                str(notification.get("body") or ""),
                "",
                f"notification_id={notification.get('id')}",
                f"intent_id={notification.get('intent_id')}",
                "delivery_mode=local_draft_only",
            ]
        )
        message.set_content(body)
        path = self.outbox / f"{_safe_name(str(notification.get('id') or title))}.eml"
        path.write_text(message.as_string(), encoding="utf-8")
        return {
            "ok": True,
            "notification_id": notification.get("id"),
            "draft_path": str(path),
            "delivery_mode": "local_draft_only",
        }
