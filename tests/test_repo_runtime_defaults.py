from __future__ import annotations

from pathlib import Path

import orchestrator.adapters.builtins as builtins
from orchestrator.config import Settings, default_home


def test_default_home_is_repo_owned_when_not_overridden(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

    assert default_home() == Path(__file__).resolve().parents[1] / ".runtime" / "orchestrator"
    assert Settings.load().home == default_home()


def test_governor_home_is_repo_owned_by_default() -> None:
    assert builtins.GOVERNOR_BIN == Path(__file__).resolve().parents[1] / ".runtime" / "ai-resource-governor" / "bin"


def test_worker_roster_builder_uses_repo_runtime_state_by_default() -> None:
    import orchestrator.governance.worker_roster_builder as builder

    runtime = Path(__file__).resolve().parents[1] / ".runtime" / "orchestrator"

    assert builder.ROOT == runtime
    assert builder.DB_PATH == runtime / "state.sqlite"
    assert builder.RECEIPTS == runtime / "receipts"
    assert builder.REPAIR_QUEUE == runtime / "repair-queue.jsonl"
