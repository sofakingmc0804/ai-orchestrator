from __future__ import annotations

import uvicorn

from orchestrator.config import Settings
from orchestrator.ui.server import create_app
from orchestrator.ui.simple_server import run_simple_server


def main() -> None:
    settings = Settings.load()
    try:
        app = create_app(settings)
        uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
    except TypeError as exc:
        if "on_startup" not in str(exc):
            raise
        run_simple_server(settings, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
