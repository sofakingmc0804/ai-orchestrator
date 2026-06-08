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
