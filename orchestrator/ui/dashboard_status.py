from __future__ import annotations

import json
import os
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil

from orchestrator.config import Settings


REQUESTED_DASHBOARD_LOGS = [
    "dashboard_stdout.log",
    "dashboard_stderr.log",
    "dashboard_launcher.log",
    "dashboard_watchdog.log",
]
ROUTE_PANEL_IDS = ["routeText", "routeJobClass", "routeSubmit", "routeResult", "routeLadderList"]
FAILURE_STATES = {"blocked_after_repair_attempt", "failed", "error", "down"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _path_payload(path: Path) -> dict[str, object]:
    payload: dict[str, object] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        stat = path.stat()
        payload["modified_at"] = _iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc))
        payload["bytes"] = stat.st_size
    return payload


def _requested_surface(settings: Settings, name: str) -> dict[str, object]:
    for base in [settings.log_dir, settings.home, settings.repo_root]:
        path = base / name
        if path.exists():
            return {"name": name, **_path_payload(path)}
    return {"name": name, "exists": False, "path": str(settings.log_dir / name)}


def _latest_file(directory: Path, pattern: str) -> dict[str, object] | None:
    if not directory.exists():
        return None
    paths = [path for path in directory.glob(pattern) if path.is_file()]
    if not paths:
        return None
    return _path_payload(max(paths, key=lambda path: path.stat().st_mtime))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _receipt_time(payload: dict[str, Any], path: Path) -> str:
    for key in ["completed_at", "started_at", "checked_at"]:
        value = payload.get(key)
        if value:
            return str(value)
    return _iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def _receipt_summary(path: Path, payload: dict[str, Any]) -> dict[str, object]:
    return {
        "path": str(path),
        "event": payload.get("event"),
        "state": payload.get("state"),
        "healthy": payload.get("healthy"),
        "completed_at": _receipt_time(payload, path),
        "error": payload.get("error") or None,
    }


def _latest_supervisor_receipts(settings: Settings) -> dict[str, object]:
    supervisor_dir = settings.home / "supervisor"
    result: dict[str, object] = {"directory": str(supervisor_dir), "latest_start": None, "latest_watchdog": None, "latest_tick": None}
    if not supervisor_dir.exists():
        return result

    receipts: list[tuple[Path, dict[str, Any]]] = []
    for path in supervisor_dir.glob("*.json"):
        payload = _read_json(path)
        if payload is not None:
            receipts.append((path, payload))
    receipts.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)

    for path, payload in receipts:
        event = str(payload.get("event") or "")
        name = path.name
        if result["latest_start"] is None and "-start-" in name:
            result["latest_start"] = _receipt_summary(path, payload)
        if result["latest_watchdog"] is None and ("watchdog" in event or "watchdog" in name):
            result["latest_watchdog"] = _receipt_summary(path, payload)
        if result["latest_tick"] is None and event == "supervisor_tick":
            result["latest_tick"] = _receipt_summary(path, payload)
        if all(result[key] is not None for key in ["latest_start", "latest_watchdog", "latest_tick"]):
            break
    return result


def _latest_failure(settings: Settings) -> dict[str, object] | None:
    supervisor_dir = settings.home / "supervisor"
    if supervisor_dir.exists():
        receipts: list[Path] = sorted(
            [path for path in supervisor_dir.glob("*.json") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in receipts:
            payload = _read_json(path)
            if payload is None:
                continue
            state = str(payload.get("state") or "").lower()
            healthy = payload.get("healthy")
            error = str(payload.get("error") or "").strip()
            if state in FAILURE_STATES or healthy is False or error:
                return {
                    "source": str(path),
                    "event": payload.get("event"),
                    "state": payload.get("state"),
                    "message": error or f"{payload.get('event') or path.name} reported {payload.get('state') or 'unhealthy'}",
                    "at": _receipt_time(payload, path),
                }

    for path_payload in [
        _latest_file(settings.log_dir, "*.err.log"),
        _latest_file(settings.log_dir, "*.out.log"),
    ]:
        if not path_payload:
            continue
        path = Path(str(path_payload["path"]))
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
        except Exception:
            continue
        for line in reversed(lines):
            lower = line.lower()
            if any(term in lower for term in ["error", "exception", "traceback", "failed", "blocked_after_repair_attempt"]):
                return {
                    "source": str(path),
                    "message": line.strip()[:500],
                    "at": path_payload.get("modified_at"),
                }
    return None


def _addr_port(addr: object) -> int | None:
    if hasattr(addr, "port"):
        return int(getattr(addr, "port"))
    if isinstance(addr, tuple) and len(addr) >= 2:
        return int(addr[1])
    return None


def _find_listener(port: int) -> dict[str, object] | None:
    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception:
        return None
    listen_state = getattr(psutil, "CONN_LISTEN", "LISTEN")
    for conn in connections:
        if str(getattr(conn, "status", "")).upper() != str(listen_state).upper():
            continue
        if _addr_port(getattr(conn, "laddr", None)) == port:
            pid = getattr(conn, "pid", None)
            return {"pid": int(pid) if pid else None}
    return None


def _process_payload(pid: int | None, now: datetime) -> dict[str, object]:
    if not pid:
        return {"pid": None}
    payload: dict[str, object] = {"pid": pid}
    try:
        proc = psutil.Process(pid)
        started = datetime.fromtimestamp(proc.create_time(), timezone.utc)
        payload.update(
            {
                "name": proc.name(),
                "cmdline": proc.cmdline(),
                "started_at": _iso(started),
                "uptime_seconds": max(0, int((now - started).total_seconds())),
            }
        )
    except Exception as exc:
        payload["process_error"] = str(exc)
    return payload


def _find_watchdog(settings: Settings, now: datetime) -> dict[str, object]:
    repo_root = str(settings.repo_root).lower()
    try:
        processes = psutil.process_iter(["pid", "name", "cmdline", "create_time"])
    except Exception as exc:
        return {"state": "unknown", "error": str(exc)}
    for proc in processes:
        info = getattr(proc, "info", {}) or {}
        cmdline = [str(part) for part in info.get("cmdline") or []]
        command_text = " ".join(cmdline).lower()
        if "start-orchestrator.ps1" not in command_text or "-watchdog" not in command_text:
            continue
        if repo_root not in command_text and "ai-orchestrator" not in command_text:
            continue
        created_raw = info.get("create_time")
        started = datetime.fromtimestamp(float(created_raw), timezone.utc) if created_raw else None
        return {
            "state": "running",
            "pid": info.get("pid"),
            "name": info.get("name"),
            "cmdline": cmdline,
            "started_at": _iso(started) if started else None,
            "uptime_seconds": max(0, int((now - started).total_seconds())) if started else None,
        }
    return {"state": "down", "pid": None, "error": "watchdog process not found"}


def _probe_json(url: str, timeout: float = 10.0) -> dict[str, object]:
    try:
        response = httpx.get(url, timeout=timeout)
        body: object | None
        try:
            body = response.json()
        except Exception:
            body = None
        return {
            "ok": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "json": body,
        }
    except Exception as exc:
        return {"ok": False, "status_code": None, "error": str(exc)}


def _route_panel_static(settings: Settings) -> dict[str, object]:
    static_dir = settings.repo_root / "orchestrator" / "ui" / "static"
    index_path = static_dir / "index.html"
    app_path = static_dir / "app.js"
    index_text = index_path.read_text(encoding="utf-8", errors="ignore") if index_path.exists() else ""
    app_text = app_path.read_text(encoding="utf-8", errors="ignore") if app_path.exists() else ""
    controls = {control_id: f'id="{control_id}"' in index_text for control_id in ROUTE_PANEL_IDS}
    return {
        "static_dir": str(static_dir),
        "index_path": str(index_path),
        "app_js_path": str(app_path),
        "controls": controls,
        "app_posts_route": "/api/route" in app_text,
        "ready": all(controls.values()) and "/api/route" in app_text,
    }


def _route_panel_status(
    settings: Settings,
    host: str,
    port: int,
    server_listening: bool,
    route_api_ready: bool | None = None,
) -> dict[str, object]:
    static = _route_panel_static(settings)
    api_probe: dict[str, object] = {"ok": False, "skipped": not server_listening}
    api_ready = False
    if route_api_ready is not None:
        api_ready = route_api_ready
        api_probe = {"ok": api_ready, "source": "in_process_route_table"}
    elif server_listening:
        api_probe = _probe_json(f"http://{host}:{port}/openapi.json")
        body = api_probe.get("json")
        paths = body.get("paths", {}) if isinstance(body, dict) else {}
        route_path = paths.get("/api/route") if isinstance(paths, dict) else None
        api_ready = isinstance(route_path, dict) and "post" in route_path
    static_ready = bool(static["ready"])
    if static_ready and api_ready:
        state = "ready"
    elif static_ready and not server_listening:
        state = "static_only"
    else:
        state = "down" if not static_ready else "degraded"
    return {
        "state": state,
        "static_ready": static_ready,
        "api_ready": api_ready,
        "static": static,
        "api_probe": {key: value for key, value in api_probe.items() if key != "json"},
    }


def _log_status(settings: Settings) -> dict[str, object]:
    return {
        "requested_surfaces": [_requested_surface(settings, name) for name in REQUESTED_DASHBOARD_LOGS],
        "active_stdout": _latest_file(settings.log_dir, "server-*.out.log"),
        "active_stderr": _latest_file(settings.log_dir, "server-*.err.log"),
        "supervisor": _latest_supervisor_receipts(settings),
    }


def _active_failure(state: str, fastapi: dict[str, object], watchdog: dict[str, object], route_panel: dict[str, object], latest: dict[str, object] | None) -> dict[str, object] | None:
    if state == "healthy":
        return None
    if latest is not None:
        return latest
    if fastapi.get("state") != "up":
        return {"source": fastapi.get("health_url"), "message": fastapi.get("error") or "FastAPI is not healthy"}
    if watchdog.get("state") != "running":
        return {"source": "process_table", "message": watchdog.get("error") or "watchdog is not running"}
    if route_panel.get("state") != "ready":
        return {"source": "route_panel", "message": "route panel is not ready"}
    return None


def write_dashboard_status_receipt(settings: Settings, payload: dict[str, object], now: datetime | None = None) -> Path:
    active_now = now or _now_utc()
    receipt_dir = settings.home / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    path = receipt_dir / f"{_stamp(active_now)}-dashboard-status.json"
    payload["receipt_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


# Freshness budgets (seconds) for data sources the dashboard claims are "live".
FRESHNESS_LIMITS = {
    "business_snapshot": 6 * 3600,
    "subscriptions": 12 * 3600,
}
LARGE_STATE_DB_BYTES = 200 * 1024 * 1024
WORK_PROOF_STALE_SECONDS = 3 * 3600


def _age_seconds(value: object, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


def _fleet_component(services: list[dict[str, object]] | None) -> dict[str, object]:
    """Honest fleet roll-up. Optional/retired services never force a downgrade."""
    if services is None:
        return {"state": "not_checked", "reason": "service probe results were not supplied"}
    problems: list[dict[str, object]] = []
    optional_down = 0
    healthy = 0
    for svc in services:
        health = str(svc.get("health_state") or "unknown").lower()
        if health == "healthy":
            healthy += 1
            continue
        if health in {"degraded", "stopped"}:
            if bool(svc.get("optional")):
                optional_down += 1
                continue
            problems.append(
                {
                    "service": svc.get("name") or svc.get("id"),
                    "health_state": health,
                    "detail": svc.get("detail"),
                    "repair_action": svc.get("repair_action"),
                }
            )
    return {
        "state": "degraded" if problems else "healthy",
        "total": len(services),
        "healthy": healthy,
        "problems": problems,
        "optional_down": optional_down,
    }


def _freshness_component(data_sources: dict[str, object] | None, now: datetime) -> dict[str, object]:
    if not data_sources:
        return {"state": "not_checked", "reason": "no data-source timestamps supplied"}
    stale: list[dict[str, object]] = []
    checked: list[dict[str, object]] = []
    for name, value in data_sources.items():
        limit = FRESHNESS_LIMITS.get(name, 12 * 3600)
        age = _age_seconds(value, now)
        entry: dict[str, object] = {"source": name, "age_seconds": age, "limit_seconds": limit, "checked_at": value}
        checked.append(entry)
        if age is None:
            stale.append({**entry, "reason": "no timestamp / never refreshed"})
        elif age > limit:
            stale.append(entry)
    return {"state": "stale" if stale else "fresh", "sources": checked, "stale": stale}


def _db_integrity_component(settings: Settings) -> dict[str, object]:
    """Cheap, fast integrity signal: can we open the DB read-only and read its schema?

    A full PRAGMA integrity_check would scan the whole file and is too slow for a
    per-poll status endpoint, so this only proves the database is openable and its
    schema is readable, and flags an oversized file for a maintenance pass.
    """
    path = settings.state_path
    if not path.exists():
        return {"state": "not_checked", "reason": "state database not present yet", "path": str(path)}
    size = path.stat().st_size
    payload: dict[str, object] = {"path": str(path), "bytes": size}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        payload.update({"state": "corrupt", "error": str(exc)})
        return payload
    payload["state"] = "ok"
    if size > LARGE_STATE_DB_BYTES:
        payload["advisory"] = f"state database is {size // (1024 * 1024)} MB; schedule a VACUUM/prune maintenance pass"
    return payload


def _work_proof_component(supervisor: dict[str, object], now: datetime) -> dict[str, object]:
    """Proof the system is actually doing work, derived from the latest supervisor tick."""
    tick = supervisor.get("latest_tick") if isinstance(supervisor, dict) else None
    if not isinstance(tick, dict):
        return {"state": "unknown", "reason": "no supervisor tick receipt found"}
    age = _age_seconds(tick.get("completed_at"), now)
    entry: dict[str, object] = {"latest_tick_at": tick.get("completed_at"), "age_seconds": age}
    if age is not None and age > WORK_PROOF_STALE_SECONDS:
        entry["state"] = "stale"
        entry["reason"] = f"last supervisor tick was {int(age // 60)} min ago"
    else:
        entry["state"] = "ok"
    return entry


def _aggregate_health(control_plane: str, components: dict[str, dict[str, object]]) -> tuple[str, list[str]]:
    """Combine measured component signals into one honest verdict plus reasons.

    Components that were not measured (state 'not_checked'/'unknown') never downgrade
    the verdict, so callers that omit services/data_sources keep the control-plane-only
    behaviour. Only positively-measured faults flip the badge away from 'healthy'.
    """
    reasons: list[str] = []
    if control_plane == "down":
        return "down", ["control plane (FastAPI) is down"]
    downgraded = control_plane != "healthy"
    if downgraded:
        reasons.append("control plane is not fully healthy")
    for problem in components.get("fleet", {}).get("problems") or []:
        downgraded = True
        reasons.append(f"service '{problem.get('service')}' is {problem.get('health_state')}")
    if components.get("data_freshness", {}).get("state") == "stale":
        downgraded = True
        for item in components["data_freshness"].get("stale") or []:
            reasons.append(f"data source '{item.get('source')}' is stale")
    if components.get("db_integrity", {}).get("state") == "corrupt":
        downgraded = True
        reasons.append("state database failed an integrity read")
    if components.get("work_proof", {}).get("state") == "stale":
        downgraded = True
        reasons.append(str(components["work_proof"].get("reason") or "no recent proof of work"))
    return ("degraded" if downgraded else "healthy"), reasons


def build_dashboard_status(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    write_receipt: bool = False,
    now: datetime | None = None,
    in_process: bool = False,
    route_api_ready: bool | None = None,
    services: list[dict[str, object]] | None = None,
    data_sources: dict[str, object] | None = None,
) -> dict[str, object]:
    active_now = now or _now_utc()
    active_port = port or int(os.getenv("ORCHESTRATOR_PORT", "8765"))
    listener = _find_listener(active_port)
    process_pid = listener.get("pid") if listener else (os.getpid() if in_process else None)
    process = _process_payload(process_pid, active_now)
    health_url = f"http://{host}:{active_port}/api/status"
    if in_process:
        health_probe = {"ok": True, "source": "in_process_fastapi_route"}
    else:
        health_probe = _probe_json(health_url) if listener else {"ok": False, "error": f"no listener on {host}:{active_port}"}
    fastapi_up = bool(in_process or (listener and health_probe.get("ok")))
    fastapi: dict[str, object] = {
        "state": "up" if fastapi_up else ("degraded" if listener else "down"),
        "host": host,
        "port": active_port,
        "health_url": health_url,
        "health_probe": {key: value for key, value in health_probe.items() if key != "json"},
        **process,
    }
    if not health_probe.get("ok"):
        fastapi["error"] = health_probe.get("error") or f"health probe returned {health_probe.get('status_code')}"

    watchdog = _find_watchdog(settings, active_now)
    route_panel = _route_panel_status(settings, host, active_port, listener is not None or in_process, route_api_ready)
    logs = _log_status(settings)
    latest_failure = _latest_failure(settings)

    if fastapi["state"] == "up" and watchdog.get("state") == "running" and route_panel.get("state") == "ready":
        control_plane_state = "healthy"
    elif fastapi["state"] == "down":
        control_plane_state = "down"
    else:
        control_plane_state = "degraded"

    components: dict[str, dict[str, object]] = {
        "control_plane": {
            "state": control_plane_state,
            "fastapi": fastapi["state"],
            "watchdog": watchdog.get("state"),
            "route_panel": route_panel.get("state"),
        },
        "fleet": _fleet_component(services),
        "data_freshness": _freshness_component(data_sources, active_now),
        "db_integrity": _db_integrity_component(settings),
        "work_proof": _work_proof_component(logs.get("supervisor", {}) if isinstance(logs, dict) else {}, active_now),
    }
    # The aggregate verdict reflects real signals (fleet health, data freshness, DB
    # integrity, proof of work), not just "the FastAPI endpoint answered its own request".
    state, health_reasons = _aggregate_health(control_plane_state, components)

    payload: dict[str, object] = {
        "state": state,
        "control_plane_state": control_plane_state,
        "health_reasons": health_reasons,
        "components": components,
        "checked_at": _iso(active_now),
        "machine": platform.node(),
        "repo_root": str(settings.repo_root),
        "state_path": str(settings.state_path),
        "fastapi": fastapi,
        "watchdog": watchdog,
        "route_panel": route_panel,
        "logs": logs,
        "latest_failure": latest_failure,
        "active_failure": _active_failure(control_plane_state, fastapi, watchdog, route_panel, latest_failure),
        "receipt_path": None,
    }
    if write_receipt:
        write_dashboard_status_receipt(settings, payload, active_now)
    return payload
