from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import orchestrator.adapters.builtins as builtins
import orchestrator.process.recovery as recovery
from orchestrator.adapters.builtins import CodexExecAdapter, HermesAgentAdapter, LmStudioAdapter, OllamaHttpAdapter, OpenClawGatewayAdapter
from orchestrator.config import Settings
from orchestrator.models import HealthState
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_hermes_health_uses_governed_desktop_runtime_and_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str], dict[str, str] | None]] = []

    async def fake_run(
        command: str,
        args: list[str],
        timeout: float = 30,
        env: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((command, args, env))
        return {
            "ok": True,
            "stdout": "Model: deepseek-v4-pro\r\nProvider: Ollama Cloud\r\n",
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(builtins, "_run_bounded", fake_run)

    info = await HermesAgentAdapter().health_probe()

    assert calls[0][0:2] == ("hermes", ["status"])
    assert calls[0][2] is not None
    assert calls[0][2]["HERMES_HOME"] == str(builtins.HERMES_DESKTOP_HOME)
    assert info.health_state == HealthState.HEALTHY
    assert info.detail is None


@pytest.mark.asyncio
async def test_hermes_dispatch_uses_desktop_oneshot_and_usage_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, list[str], dict[str, str] | None]] = []
    timeouts: list[float] = []

    async def fake_run(
        command: str,
        args: list[str],
        timeout: float = 30,
        env: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((command, args, env))
        timeouts.append(timeout)
        usage_path = Path(args[args.index("--usage-file") + 1])
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text('{"tokens_in": 11, "tokens_out": 7}', encoding="utf-8")
        return {"ok": True, "stdout": "desktop work result", "stderr": "", "returncode": 0}

    monkeypatch.setattr(builtins, "_run_bounded", fake_run)
    monkeypatch.setattr(builtins, "HERMES_USAGE_DIR", tmp_path)

    result = await HermesAgentAdapter().dispatch({"intent": {"raw_text": "Inspect the source task."}})

    assert result["ok"] is True
    assert result["text"] == "desktop work result"
    assert calls[0][0] == "hermes"
    assert calls[0][1][0] == "-z"
    assert "--usage-file" in calls[0][1]
    assert calls[0][2] is not None
    assert calls[0][2]["HERMES_HOME"] == str(builtins.HERMES_DESKTOP_HOME)
    assert timeouts == [900]
    assert result["raw"]["usage"]["tokens_out"] == 7


def test_governed_hermes_shim_sets_desktop_home() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    shim = repo_root / ".runtime" / "ai-resource-governor" / "bin" / "hermes.ps1"

    text = shim.read_text(encoding="utf-8")

    assert '$env:HERMES_HOME = "C:\\Users\\Couch\\AppData\\Local\\hermes"' in text


def test_governed_command_resolution_prefers_resource_governor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / ".ai-resource-governor" / "bin"
    fake_bin.mkdir(parents=True)
    shim = fake_bin / "openclaw.ps1"
    shim.write_text("exit 0", encoding="utf-8")
    monkeypatch.setattr(builtins, "GOVERNOR_BIN", fake_bin)
    monkeypatch.setattr(builtins.shutil, "which", lambda command: f"C:/unsafe/{command}.cmd")
    assert builtins._resolve_command("openclaw") == str(shim)


def test_governed_hermes_resolution_prefers_powershell_over_cmd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_bin = tmp_path / ".ai-resource-governor" / "bin"
    fake_bin.mkdir(parents=True)
    cmd = fake_bin / "hermes.cmd"
    ps1 = fake_bin / "hermes.ps1"
    cmd.write_text("@echo off", encoding="utf-8")
    ps1.write_text("exit 0", encoding="utf-8")
    monkeypatch.setattr(builtins, "GOVERNOR_BIN", fake_bin)

    assert builtins._resolve_command("hermes") == str(ps1)


def test_lms_command_resolution_prefers_headless_user_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    headless_lms = home / ".lmstudio" / "bin" / "lms.exe"
    headless_lms.parent.mkdir(parents=True)
    headless_lms.write_text("", encoding="utf-8")
    monkeypatch.setattr(builtins.Path, "home", lambda: home)
    monkeypatch.setattr(builtins.shutil, "which", lambda _command: None)

    assert builtins._resolve_command("lms") == str(headless_lms)


def test_terminal_output_cleaner_removes_ansi_sequences() -> None:
    assert builtins._clean_terminal_text("assi\x1b[4D\x1b[K\nassistance") == "assi\nassistance"


def test_ollama_http_targets_current_localhost_server() -> None:
    assert OllamaHttpAdapter().base_url == "http://localhost:11434"


@pytest.mark.asyncio
async def test_codex_adapter_ignores_user_config_service_tier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, list[str], float]] = []

    async def fake_run(command: str, args: list[str], timeout: float = 30) -> dict[str, Any]:
        calls.append((command, args, timeout))
        output = Path(args[args.index("-o") + 1])
        output.write_text("CODEX_OK", encoding="utf-8")
        return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(builtins, "_run_bounded", fake_run)

    result = await CodexExecAdapter("codex-desktop", "codex-desktop", "Codex Desktop", "subprocess").dispatch({"intent": {"raw_text": "smoke"}})

    args = calls[0][1]
    assert result["ok"] is True
    assert "--ignore-user-config" in args
    assert args[args.index("-m") + 1] == "gpt-5.5"
    assert "service_tier='fast'" in args


@pytest.mark.asyncio
async def test_lm_studio_health_requires_http_server(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> object:
            raise builtins.httpx.ConnectError("refused")

    monkeypatch.setattr(builtins, "_resolve_command", lambda command: "C:/Program Files/LM Studio/resources/app/.webpack/lms.exe" if command == "lms" else None)
    monkeypatch.setattr(builtins.httpx, "AsyncClient", FakeClient)

    info = await LmStudioAdapter().health_probe()

    assert info.install_path
    assert info.health_state == HealthState.STOPPED


@pytest.mark.asyncio
async def test_ollama_health_requires_the_dispatch_route(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            return FakeResponse(200 if url.endswith("/api/tags") else 404)

    monkeypatch.setattr(builtins.httpx, "AsyncClient", FakeClient)

    info = await OllamaHttpAdapter().health_probe()

    assert info.health_state == HealthState.DEGRADED
    assert info.detail is not None
    assert "/api/chat" in info.detail


@pytest.mark.asyncio
async def test_lm_studio_dispatch_uses_embeddings_endpoint_for_embedding_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any] | None]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self.payload

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            calls.append((url, None))
            return FakeResponse({"data": [{"id": "text-embedding-nomic-embed-text-v1.5"}]})

        async def post(self, url: str, json: dict[str, Any]) -> FakeResponse:
            calls.append((url, json))
            return FakeResponse({"object": "list", "model": json["model"], "data": [{"embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(builtins.httpx, "AsyncClient", FakeClient)
    envelope = {
        "intent": {
            "raw_text": "embed this",
            "parsed_payload": {"required_capability": "embeddings"},
        }
    }

    result = await LmStudioAdapter().dispatch(envelope)

    assert result["ok"] is True
    assert result["text"] == "embedding_dimensions=3"
    assert calls[1][0].endswith("/v1/embeddings")


@pytest.mark.asyncio
async def test_openclaw_adapter_timeout_returns_repair_action(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(command: str, args: list[str], timeout: float = 30) -> dict[str, Any]:
        return {"ok": False, "error": "openclaw timed out after 45s", "timeout": True}

    monkeypatch.setattr(builtins, "_run_bounded", fake_run)
    result = await OpenClawGatewayAdapter().dispatch({"intent": {"raw_text": "smoke"}})
    assert result["ok"] is False
    assert "repair_action" in result


@pytest.mark.asyncio
async def test_recovery_does_not_restart_retired_openclaw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_run(command: str, args: list[str], timeout: float = 30) -> dict[str, Any]:
        raise AssertionError("retired OpenClaw must not be probed or restarted")

    monkeypatch.setattr(recovery, "_run_bounded", fake_run)
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    result = await recovery.repair_openclaw_gateway(store)
    assert result["state"] == "retired"
    repairs = await store.list_repair_queue()
    assert repairs == []


@pytest.mark.asyncio
async def test_recovery_records_lm_studio_first_run_blocker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_models_visible() -> tuple[bool, dict[str, Any]]:
        return False, {"ok": False, "error": "connect ECONNREFUSED 127.0.0.1:1234"}

    async def fake_run(command: str, args: list[str], timeout: float = 30) -> dict[str, Any]:
        if args == ["status"]:
            return {"ok": True, "stdout": "Server: OFF", "stderr": "", "returncode": 0}
        return {
            "ok": False,
            "stdout": "",
            "stderr": "Cannot find LM Studio installation. If you have just installed LM Studio, please run it at least once.",
            "returncode": 1,
        }

    monkeypatch.setattr(recovery, "_lm_studio_models_visible", fake_models_visible)
    monkeypatch.setattr(recovery, "_run_bounded", fake_run)
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()

    result = await recovery.repair_lm_studio_local_server(store)

    assert result["state"] == "continuation_required"
    assert Path(str(result["packet"])).exists()
    repairs = await store.list_repair_queue()
    assert repairs
    assert repairs[0]["failure_source"] == "lm-studio"
    assert "first-run" in repairs[0]["suggested_action"]

    second = await recovery.repair_lm_studio_local_server(store)
    repairs_after_second_run = await store.list_repair_queue()
    assert second["repair_id"] == repairs[0]["id"]
    assert len(repairs_after_second_run) == 1
