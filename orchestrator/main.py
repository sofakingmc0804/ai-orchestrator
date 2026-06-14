from __future__ import annotations

import uvicorn

from orchestrator.config import Settings
from orchestrator.ui.server import create_app


def main() -> None:
    settings = Settings.load()
    app = create_app(settings)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
