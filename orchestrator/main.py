from __future__ import annotations

import argparse
import os

import uvicorn

from orchestrator.config import Settings
from orchestrator.ui.server import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the AI Orchestrator brain service.")
    parser.add_argument("--host", default=os.getenv("ORCHESTRATOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ORCHESTRATOR_PORT", "8765")))
    args, _unknown = parser.parse_known_args(argv)
    settings = Settings.load()
    app = create_app(settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", lifespan="on")


if __name__ == "__main__":
    main()
