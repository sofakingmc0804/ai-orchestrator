from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psutil


class WindowsPlatformAdapter:
    def discover_processes(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "memory_info", "create_time"]):
            try:
                rows.append(
                    {
                        "pid": proc.info["pid"],
                        "name": proc.info.get("name"),
                        "exe": proc.info.get("exe"),
                        "memory_mb": proc.info["memory_info"].rss / (1024 * 1024) if proc.info.get("memory_info") else None,
                        "create_time": proc.info.get("create_time"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rows

    def register_context_menu(self, actions: list[dict[str, str]]) -> None:
        raise NotImplementedError("Windows Explorer context menu registration is gated until selection queue tests pass.")

    def show_tray_notification(self, notification: dict[str, object]) -> None:
        # File-feed remains the source of truth. Native toast subscriber is intentionally separate.
        return None

    def install_startup_entry(self, command: str) -> None:
        raise NotImplementedError("Startup entry installation requires explicit owner approval.")

    def known_install_paths(self, service: str) -> list[Path]:
        candidates = [Path(os.getenv("LOCALAPPDATA", "")), Path(os.getenv("APPDATA", "")), Path("C:/Program Files")]
        return [p for p in candidates if p.exists()]

    def open_in_default_app(self, path: Path) -> None:
        subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)

