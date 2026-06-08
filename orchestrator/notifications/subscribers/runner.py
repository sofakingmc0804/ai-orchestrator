from __future__ import annotations

from typing import Any

from orchestrator.config import Settings
from orchestrator.notifications.subscribers.base import Subscriber, run_subscriber_once
from orchestrator.notifications.subscribers.email import EmailDraftSubscriber
from orchestrator.notifications.subscribers.in_app import InAppSubscriber
from orchestrator.notifications.subscribers.tray_windows import WindowsTraySubscriber


async def run_all_subscribers_once(settings: Settings, include_tray: bool = False) -> dict[str, Any]:
    subscribers: list[Subscriber] = [InAppSubscriber(), EmailDraftSubscriber(settings)]
    if include_tray:
        subscribers.append(WindowsTraySubscriber())
    results = []
    for subscriber in subscribers:
        results.append(await run_subscriber_once(settings, subscriber))
    return {"subscribers": results}
