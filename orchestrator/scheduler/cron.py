from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from threading import Event, Thread

from orchestrator.config import Settings
from orchestrator.scheduler.tasks import run_due_scheduler_once


class AsyncScheduler:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def add_interval(self, seconds: float, fn: Callable[[], Awaitable[None]]) -> None:
        async def runner() -> None:
            while True:
                await asyncio.sleep(seconds)
                await fn()

        self._tasks.append(asyncio.create_task(runner()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def start_due_scheduler_thread(settings: Settings, seconds: float = 60.0) -> Thread:
    stop_event = Event()

    async def runner() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(seconds)
            await run_due_scheduler_once(settings)

    def thread_main() -> None:
        asyncio.run(runner())

    thread = Thread(target=thread_main, name="ai-orchestrator-scheduler", daemon=True)
    thread.stop_event = stop_event  # type: ignore[attr-defined]
    thread.start()
    return thread
