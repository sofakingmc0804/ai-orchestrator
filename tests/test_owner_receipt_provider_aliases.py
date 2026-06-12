from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.models import BillingClass, Capability, ConsequenceTier, HealthState, ServiceInfo
from orchestrator.reports.owner_receipt import export_owner_receipt
from orchestrator.state.store import StateStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        state_path=tmp_path / "state.sqlite",
        notifications_path=tmp_path / "notifications.jsonl",
        log_dir=tmp_path / "logs",
        repo_root=tmp_path,
    )


async def _seed_ollama_cloud(settings: Settings) -> StateStore:
    store = StateStore(settings)
    await store.initialize()
    await store.upsert_services(
        [
            ServiceInfo(
                id="ollama-cloud",
                name="Ollama Cloud",
                service_group="cloud_clients",
                adapter_name="ollama-cloud",
                protocol="ollama-cloud-http",
                health_state=HealthState.HEALTHY,
            )
        ]
    )
    await store.upsert_capabilities(
        [
            Capability(
                id="ollama-cloud:coding_chat",
                adapter_name="ollama-cloud",
                capability_id="coding_chat",
                rating_instruction=3,
                rating_quality=3,
                latency_band="medium",
                consequence_max=ConsequenceTier.MEDIUM,
                billing_class=BillingClass.UNKNOWN_COST,
            )
        ]
    )
    return store


@pytest.mark.asyncio
async def test_owner_receipt_uses_canonical_provider_alias_for_subscription_usage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = await _seed_ollama_cloud(settings)
    await store.record_quota_snapshots(
        [
            {
                "provider": "ollama-cloud",
                "units_consumed": 30,
                "units_limit": 100,
                "reset_at": "2026-06-30T00:00:00Z",
            }
        ]
    )

    result = await export_owner_receipt(settings)

    text = Path(str(result["receipt_path"])).read_text(encoding="utf-8")
    assert "| ollama-cloud | `ollama-cloud` | `subscription_usage`" in text
    assert "70/100 remaining" in text
    assert "unknown cost; disabled until classified" not in text


@pytest.mark.asyncio
async def test_owner_receipt_keeps_subscription_usage_classified_without_live_quota(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await _seed_ollama_cloud(settings)

    result = await export_owner_receipt(settings)

    text = Path(str(result["receipt_path"])).read_text(encoding="utf-8")
    assert "| ollama-cloud | `ollama-cloud` | `subscription_usage`" in text
    assert "subscription usage; no live usage probe recorded" in text
    assert "unknown cost; disabled until classified" not in text


@pytest.mark.asyncio
async def test_owner_receipt_uses_subscription_usage_snapshot_window(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = await _seed_ollama_cloud(settings)
    await store.upsert_subscription_usage_snapshots(
        [
            {
                "id": "ollama_cloud:owner:default",
                "service_id": "ollama_cloud",
                "account_id": "owner",
                "profile_id": "default",
                "subscription_name": "Ollama Cloud",
                "plan_name": "Pro",
                "source_type": "provider_endpoint",
                "source_command": "GET ollama cloud usage",
                "tokens_limit": None,
                "tokens_used_total": None,
                "tokens_remaining": None,
                "tokens_used_by_app": 125,
                "tokens_used_elsewhere": None,
                "reset_at": "2026-06-30T00:00:00Z",
                "checked_at": "2026-06-12T12:00:00Z",
                "ok": True,
                "confidence": "provider_usage_window",
                "status": "ok",
                "error": None,
                "usage_windows_json": json.dumps(
                    [
                        {
                            "label": "provider GPU-time",
                            "remaining_percent": 82,
                            "used_percent": 18,
                            "reset_at": "2026-06-30T00:00:00Z",
                        }
                    ]
                ),
                "raw_json": "{}",
            }
        ]
    )

    result = await export_owner_receipt(settings)

    text = Path(str(result["receipt_path"])).read_text(encoding="utf-8")
    assert "subscription usage: provider GPU-time 82.0% remaining" in text
    assert "checked 2026-06-12T12:00:00Z" in text
    assert "subscription usage; no live usage probe recorded" not in text
