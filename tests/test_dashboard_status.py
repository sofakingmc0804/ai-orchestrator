from __future__ import annotations

import json
from pathlib import Path

from orchestrator.config import Settings
from orchestrator.ui import dashboard_status


def _settings(tmp_path: Path) -> Settings:
    home = tmp_path / "runtime"
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=tmp_path,
    )


def test_supervisor_receipt_scan_is_bounded(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    supervisor = settings.home / "supervisor"
    supervisor.mkdir(parents=True)
    newest = supervisor / "20260712T200000Z-sup_newest.json"
    newest.write_text(json.dumps({"event": "supervisor_tick", "state": "produced"}), encoding="utf-8")

    for index in range(dashboard_status.SUPERVISOR_RECEIPT_SCAN_LIMIT + 25):
        (supervisor / f"20260701T{index:06d}Z-sup_{index:04d}.json").write_text(
            json.dumps({"event": "supervisor_tick", "state": "produced"}), encoding="utf-8"
        )

    reads: list[Path] = []
    original = dashboard_status._read_json

    def observed_read(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(dashboard_status, "_read_json", observed_read)
    payload = dashboard_status._latest_supervisor_receipts(settings)

    assert payload["latest_tick"] is not None
    assert len(reads) <= dashboard_status.SUPERVISOR_RECEIPT_SCAN_LIMIT
