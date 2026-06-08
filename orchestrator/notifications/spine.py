from __future__ import annotations

import json
from pathlib import Path

from orchestrator.models import Notification
from orchestrator.state.store import StateStore


class NotificationSpine:
    def __init__(self, path: Path, store: StateStore):
        self.path = path
        self.store = store

    async def publish(self, notification: Notification) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(notification.model_dump(mode="json"), ensure_ascii=False) + "\n")
        await self.store.add_notification(notification, delivered_channels=["file", "in_app"])

