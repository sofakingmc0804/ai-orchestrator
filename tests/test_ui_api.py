from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from orchestrator.models import DispatchResult
import orchestrator.ui.simple_server as simple_server
from orchestrator.ui.simple_server import make_handler


def test_simple_server_prove_adapter_api_passes_governed_request() -> None:
    calls: list[dict[str, Any]] = []

    class FakeDispatcher:
        async def prove_adapter(self, adapter_name: str, prompt: str, capability_id: str | None = None, allow_subscription: bool = False) -> DispatchResult:
            calls.append(
                {
                    "adapter_name": adapter_name,
                    "prompt": prompt,
                    "capability_id": capability_id,
                    "allow_subscription": allow_subscription,
                }
            )
            return DispatchResult(dispatch_id="dsp_ui", intent_id="int_ui", adapter_name=adapter_name, state="completed", result_text="OK")

    class FakeRuntime:
        dispatcher = FakeDispatcher()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(
            f"http://127.0.0.1:{server.server_port}/api/prove-adapter",
            json={
                "adapter_name": "copilot-gh",
                "capability": "coding_chat",
                "prompt": "proof",
                "allow_subscription": True,
            },
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert calls == [
        {
            "adapter_name": "copilot-gh",
            "prompt": "proof",
            "capability_id": "coding_chat",
            "allow_subscription": True,
        }
    ]


def test_simple_server_retry_repairs_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_retry_open_repairs(settings: object, store: object) -> dict[str, object]:
        calls.append({"settings": settings, "store": store})
        return {"attempted": 1, "remaining_open_repairs": []}

    class FakeRuntime:
        settings = object()
        store = object()

    monkeypatch.setattr(simple_server, "retry_open_repairs", fake_retry_open_repairs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeRuntime()))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.post(f"http://127.0.0.1:{server.server_port}/api/retry-repairs", timeout=10)
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()

    assert response.status_code == 200
    assert response.json()["attempted"] == 1
    assert len(calls) == 1
