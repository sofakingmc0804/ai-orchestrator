"""Regression tests for the Command Center honesty fixes (2026-06-29).

Covers three reported defects:
  1. Subscriptions/quota showed app-token counts + "resets 0s" and surfaced numbers
     for providers where nothing was proven  -> staleness + freshness annotation.
  2. "Hermes Agent: degraded" with no reason and no repair, and a fall-through that
     could report a missing hermes as HEALTHY -> ServiceInfo.detail/repair_action and
     a corrected probe decision tree.
  3. The dashboard claimed the system was "healthy" with no proof -> an aggregate that
     reflects fleet health, data freshness, DB integrity and proof of work.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator.adapters.builtins import (
    HermesAgentAdapter,
    OpenClawGatewayAdapter,
    SyntheticTestServiceAdapter,
)
import orchestrator.adapters.builtins as builtins_mod
from orchestrator.config import Settings
from orchestrator.discovery.subscription_usage import (
    SUBSCRIPTION_STALE_AFTER_SECONDS,
    _annotate_freshness,
    build_subscription_usage_payload,
)
from orchestrator.models import HealthState, ServiceInfo
from orchestrator.state.store import StateStore
from orchestrator.ui.dashboard_status import (
    _aggregate_health,
    _fleet_component,
    _freshness_component,
    _work_proof_component,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _settings(tmp: Path, *, state_name: str = "state.sqlite") -> Settings:
    return Settings(
        home=tmp,
        state_path=tmp / state_name,
        notifications_path=tmp / "notifications.jsonl",
        log_dir=tmp / "logs",
        repo_root=tmp,
    )


# --------------------------------------------------------------------------- #
# 3. Honest aggregate health
# --------------------------------------------------------------------------- #
def test_fleet_component_excludes_optional_services() -> None:
    services = [
        {"name": "Hermes Agent", "health_state": "degraded", "optional": False, "detail": "d", "repair_action": "r"},
        {"name": "OpenClaw Gateway", "health_state": "stopped", "optional": True},
        {"name": "Claude", "health_state": "healthy"},
    ]
    fleet = _fleet_component(services)
    assert fleet["state"] == "degraded"
    assert len(fleet["problems"]) == 1
    assert fleet["optional_down"] == 1
    assert fleet["problems"][0]["detail"] == "d"
    assert fleet["problems"][0]["repair_action"] == "r"


def test_aggregate_degrades_on_degraded_service() -> None:
    fleet = _fleet_component([{"name": "Hermes Agent", "health_state": "degraded"}])
    state, reasons = _aggregate_health(
        "healthy",
        {"fleet": fleet, "data_freshness": {"state": "not_checked"}, "db_integrity": {"state": "ok"}, "work_proof": {"state": "ok"}},
    )
    assert state == "degraded"
    assert any("Hermes" in r for r in reasons)


def test_aggregate_backward_compatible_when_not_measured() -> None:
    # Mirrors callers that omit services/data_sources: nothing measured -> stays healthy.
    components = {
        "fleet": _fleet_component(None),
        "data_freshness": _freshness_component(None, datetime.now(timezone.utc)),
        "db_integrity": {"state": "not_checked"},
        "work_proof": {"state": "unknown"},
    }
    state, reasons = _aggregate_health("healthy", components)
    assert state == "healthy"
    assert reasons == []


def test_aggregate_control_plane_down_dominates() -> None:
    state, _ = _aggregate_health("down", {"fleet": _fleet_component([])})
    assert state == "down"


def test_freshness_flags_stale_sources() -> None:
    now = datetime(2026, 6, 29, 18, 0, 0, tzinfo=timezone.utc)
    fresh = _freshness_component(
        {"business_snapshot": _iso(now - timedelta(hours=1)), "subscriptions": _iso(now - timedelta(hours=2))}, now
    )
    assert fresh["state"] == "fresh"
    stale = _freshness_component({"business_snapshot": _iso(now - timedelta(hours=20)), "subscriptions": None}, now)
    assert stale["state"] == "stale"
    assert len(stale["stale"]) == 2


def test_work_proof_stale_and_unknown() -> None:
    now = datetime(2026, 6, 29, 18, 0, 0, tzinfo=timezone.utc)
    stale = _work_proof_component({"latest_tick": {"completed_at": _iso(now - timedelta(hours=5))}}, now)
    assert stale["state"] == "stale"
    assert _work_proof_component({}, now)["state"] == "unknown"  # no tick -> no false downgrade


# --------------------------------------------------------------------------- #
# 2. Service reason/repair + persistence + Hermes probe
# --------------------------------------------------------------------------- #
def test_service_detail_repair_optional_roundtrip() -> None:
    async def _run() -> None:
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(_settings(Path(d)))
            await store.initialize()
            await store.upsert_services(
                [
                    ServiceInfo(
                        id="hermes-agent",
                        name="Hermes Agent",
                        service_group="agent_hosts",
                        adapter_name="hermes-agent",
                        protocol="subprocess-gateway",
                        health_state=HealthState.DEGRADED,
                        detail="not wired to upstream",
                        repair_action="reconfigure hermes",
                    ),
                    ServiceInfo(
                        id="openclaw",
                        name="OpenClaw Gateway",
                        service_group="agent_hosts",
                        adapter_name="openclaw",
                        protocol="http-gateway",
                        health_state=HealthState.STOPPED,
                        optional=True,
                    ),
                ]
            )
            rows = {r["id"]: r for r in await store.list_services()}
            assert rows["hermes-agent"]["detail"] == "not wired to upstream"
            assert rows["hermes-agent"]["repair_action"] == "reconfigure hermes"
            assert rows["openclaw"]["optional"] == 1

    asyncio.run(_run())


def test_service_columns_migration_on_legacy_db() -> None:
    """The live 278 MB DB predates these columns; initialize() must ALTER it in place."""

    async def _run() -> None:
        with tempfile.TemporaryDirectory() as d:
            legacy = Path(d) / "legacy.sqlite"
            con = sqlite3.connect(legacy)
            con.executescript(
                "CREATE TABLE services (id TEXT PRIMARY KEY, name TEXT, service_group TEXT, "
                "adapter_name TEXT UNIQUE, protocol TEXT, install_path TEXT, version TEXT, "
                "health_state TEXT, last_probe_at TEXT, created_at TEXT, updated_at TEXT);"
            )
            con.commit()
            con.close()

            store = StateStore(_settings(Path(d), state_name="legacy.sqlite"))
            await store.initialize()

            con = sqlite3.connect(legacy)
            cols = {row[1] for row in con.execute("PRAGMA table_info(services)")}
            con.close()
            assert {"detail", "repair_action", "optional"} <= cols

            await store.upsert_services(
                [
                    ServiceInfo(
                        id="y",
                        name="Y",
                        service_group="g",
                        adapter_name="y",
                        protocol="x",
                        health_state=HealthState.DEGRADED,
                        detail="d",
                        repair_action="r",
                    )
                ]
            )
            rows = {r["id"]: r for r in await store.list_services()}
            assert rows["y"]["detail"] == "d"

    asyncio.run(_run())


def test_hermes_probe_sets_reason_and_repair_on_every_unhealthy_branch(monkeypatch) -> None:
    async def _run() -> None:
        adapter = HermesAgentAdapter()

        # In a clean env hermes is not installed: must be STOPPED with reason+repair,
        # never the false HEALTHY the old fall-through allowed via a matching python.exe.
        info = await adapter.health_probe()
        assert info.health_state == HealthState.STOPPED
        assert info.detail and info.repair_action

        cases = [
            ({"ok": True, "stdout": "Provider: nous\nCustom endpoint: x", "returncode": 0}, HealthState.HEALTHY, False),
            ({"ok": True, "stdout": "hello", "returncode": 0}, HealthState.DEGRADED, True),
            ({"ok": False, "error": "timed out", "timeout": True}, HealthState.DEGRADED, True),
            ({"ok": False, "error": "hermes not found"}, HealthState.STOPPED, True),
            ({"ok": False, "stderr": "boom", "returncode": 3}, HealthState.DEGRADED, True),
        ]
        for result, expected_state, expects_reason in cases:
            async def _fake(*_a, **_k):
                return result

            monkeypatch.setattr(builtins_mod, "_run_bounded", _fake)
            info = await adapter.health_probe()
            assert info.health_state == expected_state, result
            if expects_reason:
                assert info.detail and info.repair_action, result
            else:
                assert info.detail is None and info.repair_action is None

    asyncio.run(_run())


def test_retired_and_synthetic_services_are_optional() -> None:
    async def _run() -> None:
        assert (await OpenClawGatewayAdapter().health_probe()).optional is True
        assert (await SyntheticTestServiceAdapter().health_probe()).optional is True

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# 1. Subscription staleness
# --------------------------------------------------------------------------- #
def test_annotate_freshness_marks_stale_rows() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {"service_id": "fresh", "checked_at": _iso(now - timedelta(hours=1))},
        {"service_id": "old", "checked_at": _iso(now - timedelta(seconds=SUBSCRIPTION_STALE_AFTER_SECONDS + 60))},
        {"service_id": "missing", "checked_at": ""},
    ]
    annotated, newest, stale_count = _annotate_freshness(rows)
    by_id = {r["service_id"]: r for r in annotated}
    assert by_id["fresh"]["stale"] is False
    assert by_id["old"]["stale"] is True
    assert by_id["missing"]["stale"] is True
    assert stale_count == 2
    assert newest == rows[0]["checked_at"]


def test_subscription_payload_exposes_freshness() -> None:
    now = datetime.now(timezone.utc)

    class _StubStore:
        settings = None

        async def list_subscription_usage_snapshots(self):
            return [
                {"service_id": "openai_chatgpt", "checked_at": _iso(now - timedelta(hours=1))},
                {"service_id": "nous_hermes", "checked_at": _iso(now - timedelta(hours=30)), "status": "auth_required"},
            ]

    async def _run() -> None:
        payload = await build_subscription_usage_payload(_StubStore(), refresh=False)
        assert payload["newest_checked_at"] is not None
        assert payload["stale_count"] == 1
        assert payload["stale_after_seconds"] == SUBSCRIPTION_STALE_AFTER_SECONDS
        rows = {r["service_id"]: r for r in payload["subscriptions"]}
        assert rows["openai_chatgpt"]["stale"] is False
        assert rows["nous_hermes"]["stale"] is True

    asyncio.run(_run())
