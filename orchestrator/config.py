from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_home() -> Path:
    repo_runtime = Path(__file__).resolve().parents[1] / ".runtime" / "orchestrator"
    return Path(os.getenv("ORCHESTRATOR_HOME", repo_runtime)).expanduser()


@dataclass(frozen=True)
class Settings:
    home: Path
    state_path: Path
    notifications_path: Path
    log_dir: Path
    repo_root: Path
    output_dirname: str = "ORCHESTRATOR_OUTPUT"
    autopilot_enabled: bool = False

    @classmethod
    def load(cls, repo_root: Path | None = None) -> "Settings":
        home = default_home()
        root = repo_root or Path(__file__).resolve().parents[1]
        return cls(
            home=home,
            state_path=home / "state.sqlite",
            notifications_path=home / "notifications.jsonl",
            log_dir=home / "logs",
            repo_root=root,
        )


def ensure_runtime_dirs(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    (settings.log_dir / "adapters").mkdir(parents=True, exist_ok=True)
    if not settings.notifications_path.exists():
        settings.notifications_path.write_text("", encoding="utf-8")
