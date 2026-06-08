from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from orchestrator.config import Settings
from orchestrator.state.store import iso


class Subscriber(Protocol):
    name: str
    channel: str

    async def deliver(self, notification: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class SubscriberCursor:
    path: Path

    def read(self) -> int:
        if not self.path.exists():
            return 0
        try:
            return int(self.path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            return 0

    def write(self, offset: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(offset), encoding="utf-8")


def subscriber_root(settings: Settings) -> Path:
    root = settings.home / "subscribers"
    root.mkdir(parents=True, exist_ok=True)
    return root


def receipt_path(settings: Settings, subscriber_name: str) -> Path:
    path = subscriber_root(settings) / f"{subscriber_name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def write_receipt(settings: Settings, subscriber_name: str, payload: dict[str, Any]) -> None:
    receipt = {"ts": iso(), "subscriber": subscriber_name, **payload}
    with receipt_path(settings, subscriber_name).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False) + "\n")


def read_new_notifications(settings: Settings, subscriber_name: str) -> tuple[list[dict[str, Any]], int]:
    cursor = SubscriberCursor(subscriber_root(settings) / f"{subscriber_name}.cursor")
    start = cursor.read()
    path = settings.notifications_path
    if not path.exists():
        return [], start
    notifications: list[dict[str, Any]] = []
    with path.open("rb") as fh:
        fh.seek(start)
        for raw in fh:
            try:
                item = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                notifications.append(item)
        end = fh.tell()
    return notifications, end


async def run_subscriber_once(settings: Settings, subscriber: Subscriber) -> dict[str, Any]:
    notifications, end = read_new_notifications(settings, subscriber.name)
    delivered = 0
    skipped = 0
    failures = 0
    for notification in notifications:
        requested = notification.get("channels_requested") or []
        if subscriber.channel not in requested:
            skipped += 1
            continue
        result = await subscriber.deliver(notification)
        write_receipt(settings, subscriber.name, result)
        if result.get("ok"):
            delivered += 1
        else:
            failures += 1
    SubscriberCursor(subscriber_root(settings) / f"{subscriber.name}.cursor").write(end)
    return {"subscriber": subscriber.name, "channel": subscriber.channel, "delivered": delivered, "skipped": skipped, "failures": failures, "offset": end}
