from __future__ import annotations

from orchestrator.config import Settings
from orchestrator.ui.simple_server import run_simple_server


def main() -> None:
    run_simple_server(Settings.load())


if __name__ == "__main__":
    main()
