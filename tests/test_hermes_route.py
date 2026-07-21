from __future__ import annotations

import base64
from pathlib import Path

import pytest

from orchestrator.cli import main as cli_main
from orchestrator.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_hermes_route_decodes_base64_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_route(_settings: Settings, *, text: str, job_class: str | None, argv: list[str]) -> dict[str, object]:
        captured.update({"text": text, "job_class": job_class, "argv": argv})
        return {"state": "produced"}

    monkeypatch.setattr(cli_main, "route_for_hermes_prompt", fake_route)
    prompt = '{"terminal_state":"produced","summary":"desktop Hermes runtime"}'

    result = await cli_main._hermes_route(
        _settings(tmp_path),
        text=None,
        text_b64=base64.b64encode(prompt.encode("utf-8")).decode("ascii"),
        job_class="repo_coding",
        argv_json='["-z", "placeholder"]',
    )

    assert result["state"] == "produced"
    assert captured["text"] == prompt
    assert captured["job_class"] == "repo_coding"


def test_governed_hermes_shim_base64_encodes_prompt() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    shim = repo_root / ".runtime" / "ai-resource-governor" / "bin" / "hermes.ps1"

    text = shim.read_text(encoding="utf-8")

    assert "$promptB64" in text
    assert '"--text-b64", $promptB64' in text
