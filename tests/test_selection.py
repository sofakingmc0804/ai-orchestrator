from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.cli.main import _add_selection
from orchestrator.config import Settings
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_cli_add_selection_persists_payload(tmp_path: Path) -> None:
    selected = tmp_path / "input.txt"
    selected.write_text("source", encoding="utf-8")
    settings = Settings(
        home=tmp_path / ".orchestrator",
        state_path=tmp_path / ".orchestrator" / "state.sqlite",
        notifications_path=tmp_path / ".orchestrator" / "notifications.jsonl",
        log_dir=tmp_path / ".orchestrator" / "logs",
        repo_root=Path.cwd(),
    )

    result = await _add_selection(settings, str(selected), "file", "windows-context-menu")

    assert result["kind"] == "file"
    assert result["payload"]["path"] == str(selected)
    assert result["payload"]["exists"] is True
    assert result["payload"]["source"] == "windows-context-menu"
    store = StateStore(settings)
    await store.initialize()
    queued = await store.list_selections()
    assert queued[0]["payload"]["path"] == str(selected)
