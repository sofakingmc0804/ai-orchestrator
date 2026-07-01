from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

import orchestrator.cli.main as cli_main
from orchestrator.config import Settings
from orchestrator.ui.dashboard_status import build_dashboard_status


class _Addr:
    def __init__(self, ip: str, port: int) -> None:
        self.ip = ip
        self.port = port


class _Conn:
    status = "LISTEN"

    def __init__(self, port: int, pid: int) -> None:
        self.laddr = _Addr("127.0.0.1", port)
        self.pid = pid


class _Proc:
    def __init__(self, pid: int, cmdline: list[str], created: float) -> None:
        self.info = {
            "pid": pid,
            "name": Path(cmdline[0]).name if cmdline else "process.exe",
            "cmdline": cmdline,
            "create_time": created,
        }


class _Process:
    def __init__(self, pid: int, created: float, cmdline: list[str]) -> None:
        self.pid = pid
        self._created = created
        self._cmdline = cmdline

    def create_time(self) -> float:
        return self._created

    def name(self) -> str:
        return "python.exe"

    def cmdline(self) -> list[str]:
        return self._cmdline


def _settings(tmp_path: Path) -> Settings:
    home = tmp_path / "runtime"
    repo_root = tmp_path / "repo"
    static = repo_root / "orchestrator" / "ui" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text(
        '<textarea id="routeText"></textarea><select id="routeJobClass"></select>'
        '<button id="routeSubmit"></button><pre id="routeResult"></pre>'
        '<ul id="routeLadderList"></ul>',
        encoding="utf-8",
    )
    (static / "app.js").write_text('postJson("/api/route", payload);', encoding="utf-8")
    return Settings(
        home=home,
        state_path=home / "state.sqlite",
        notifications_path=home / "notifications.jsonl",
        log_dir=home / "logs",
        repo_root=repo_root,
    )


def test_dashboard_status_reports_live_server_watchdog_route_panel_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.log_dir.mkdir(parents=True)
    (settings.log_dir / "server-20260617-084016.out.log").write_text(
        'INFO: 127.0.0.1:64249 - "GET /api/spec-status HTTP/1.1" 200 OK\n',
        encoding="utf-8",
    )
    (settings.log_dir / "server-20260617-084016.err.log").write_text(
        "INFO: Uvicorn running on http://127.0.0.1:8765\n",
        encoding="utf-8",
    )
    supervisor = settings.home / "supervisor"
    supervisor.mkdir(parents=True)
    (supervisor / "20260618T014443Z-start-watchdog_error.json").write_text(
        json.dumps(
            {
                "event": "watchdog_error",
                "state": "blocked_after_repair_attempt",
                "healthy": False,
                "error": "orchestrator listener exists on port 8765 but failed the health check",
                "completed_at": "2026-06-18T01:44:43Z",
            }
        ),
        encoding="utf-8",
    )

    import orchestrator.ui.dashboard_status as subject

    created = datetime(2026, 6, 17, 13, 40, 16, tzinfo=timezone.utc).timestamp()
    now = datetime(2026, 6, 18, 1, 48, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(subject.psutil, "net_connections", lambda kind="tcp": [_Conn(8765, 8936)])
    monkeypatch.setattr(
        subject.psutil,
        "process_iter",
        lambda attrs=None: [
            _Proc(
                8624,
                [
                    "powershell.exe",
                    "-File",
                    str(settings.repo_root / "scripts" / "start-orchestrator.ps1"),
                    "-Watchdog",
                ],
                created - 31,
            )
        ],
    )
    monkeypatch.setattr(
        subject.psutil,
        "Process",
        lambda pid: _Process(pid, created, ["python.exe", "-m", "orchestrator.main", "--port", "8765"]),
    )

    def fake_get(url: str, timeout: float) -> httpx.Response:
        if url.endswith("/api/status"):
            return httpx.Response(200, json={"dispatches": []})
        if url.endswith("/openapi.json"):
            return httpx.Response(200, json={"paths": {"/api/route": {"post": {}}, "/api/status": {"get": {}}}})
        raise AssertionError(url)

    monkeypatch.setattr(subject.httpx, "get", fake_get)

    payload = build_dashboard_status(settings, host="127.0.0.1", port=8765, write_receipt=True, now=now)

    assert payload["state"] == "healthy"
    assert payload["fastapi"]["state"] == "up"
    assert payload["fastapi"]["pid"] == 8936
    assert payload["fastapi"]["uptime_seconds"] == 43664
    assert payload["watchdog"]["state"] == "running"
    assert payload["route_panel"]["state"] == "ready"
    assert payload["logs"]["requested_surfaces"][0] == {
        "name": "dashboard_stdout.log",
        "exists": False,
        "path": str(settings.log_dir / "dashboard_stdout.log"),
    }
    assert payload["logs"]["active_stdout"]["path"].endswith("server-20260617-084016.out.log")
    assert payload["latest_failure"]["source"].endswith("start-watchdog_error.json")
    receipt_path = Path(str(payload["receipt_path"]))
    assert receipt_path.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["state"] == "healthy"


def test_dashboard_status_reports_latest_failure_when_fastapi_is_down(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    supervisor = settings.home / "supervisor"
    supervisor.mkdir(parents=True)
    failure = supervisor / "20260618T014443Z-start-watchdog_error.json"
    failure.write_text(
        json.dumps(
            {
                "event": "watchdog_error",
                "state": "blocked_after_repair_attempt",
                "healthy": False,
                "error": "orchestrator failed to bind port 8765",
                "completed_at": "2026-06-18T01:44:43Z",
            }
        ),
        encoding="utf-8-sig",
    )

    import orchestrator.ui.dashboard_status as subject

    monkeypatch.setattr(subject.psutil, "net_connections", lambda kind="tcp": [])
    monkeypatch.setattr(subject.psutil, "process_iter", lambda attrs=None: [])

    payload = build_dashboard_status(
        settings,
        host="127.0.0.1",
        port=8765,
        write_receipt=False,
        now=datetime(2026, 6, 18, 1, 48, 0, tzinfo=timezone.utc),
    )

    assert payload["state"] == "down"
    assert payload["fastapi"]["state"] == "down"
    assert payload["watchdog"]["state"] == "down"
    assert payload["route_panel"]["state"] == "static_only"
    assert payload["active_failure"]["message"] == "orchestrator failed to bind port 8765"
    assert payload["active_failure"]["source"] == str(failure)


def test_dashboard_status_in_process_api_does_not_probe_its_own_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    import orchestrator.ui.dashboard_status as subject

    created = datetime(2026, 6, 18, 1, 59, 1, tzinfo=timezone.utc).timestamp()
    now = datetime(2026, 6, 18, 2, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(subject.psutil, "net_connections", lambda kind="tcp": [_Conn(8765, 49072)])
    monkeypatch.setattr(
        subject.psutil,
        "process_iter",
        lambda attrs=None: [
            _Proc(
                8624,
                [
                    "powershell.exe",
                    "-File",
                    str(settings.repo_root / "scripts" / "start-orchestrator.ps1"),
                    "-Watchdog",
                ],
                created - 3600,
            )
        ],
    )
    monkeypatch.setattr(
        subject.psutil,
        "Process",
        lambda pid: _Process(pid, created, ["python.exe", "-m", "orchestrator.main", "--port", "8765"]),
    )
    monkeypatch.setattr(subject.httpx, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("self-probe")))

    payload = build_dashboard_status(
        settings,
        host="127.0.0.1",
        port=8765,
        write_receipt=False,
        now=now,
        in_process=True,
        route_api_ready=True,
    )

    assert payload["state"] == "healthy"
    assert payload["fastapi"]["health_probe"] == {"ok": True, "source": "in_process_fastapi_route"}
    assert payload["route_panel"]["api_probe"] == {"ok": True, "source": "in_process_route_table"}


def test_cli_dashboard_status_command_uses_shared_builder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_build_dashboard_status(
        active_settings: Settings,
        *,
        host: str,
        port: int,
        write_receipt: bool,
    ) -> dict[str, object]:
        calls.append(
            {
                "settings": active_settings,
                "host": host,
                "port": port,
                "write_receipt": write_receipt,
            }
        )
        return {"state": "healthy", "receipt_path": None}

    monkeypatch.setattr(cli_main.Settings, "load", lambda: settings)
    monkeypatch.setattr(cli_main, "build_dashboard_status", fake_build_dashboard_status)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "dashboard-status", "--no-receipt"])

    cli_main.main()

    assert json.loads(capsys.readouterr().out) == {"state": "healthy", "receipt_path": None}
    assert calls == [{"settings": settings, "host": "127.0.0.1", "port": 8765, "write_receipt": False}]
