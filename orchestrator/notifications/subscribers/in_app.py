from __future__ import annotations

from typing import Any


class InAppSubscriber:
    name = "in_app"
    channel = "in_app"

    async def deliver(self, notification: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "notification_id": notification.get("id"),
            "title": notification.get("title"),
            "detail": "Notification is available through the in-app activity feed.",
        }
