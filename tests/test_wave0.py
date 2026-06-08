from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.discovery.services import discover_services_and_capabilities
from orchestrator.models import BillingClass, Capability, ConsequenceTier
from orchestrator.registry.contracts import capabilities_from_contract, existing_contract_adapters
from orchestrator.state.store import StateStore


@pytest.mark.asyncio
async def test_wave0_discovers_all_named_adapters(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    services, capabilities = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(capabilities)
    names = {s.adapter_name for s in services}
    assert {
        "claude-desktop-mcp",
        "claude-code-cli",
        "codex-desktop",
        "codex-cli",
        "ollama-http",
        "ollama-cli",
        "copilot-gh",
        "copilot-vscode",
        "gemini-cli",
        "hermes-agent",
        "openclaw-gateway",
        "lm-studio",
    }.issubset(names)
    assert any(c.capability_id == "classify_text" and c.billing_class.value == "local_resource" for c in capabilities)


def test_named_adapters_have_yaml_contracts() -> None:
    assert {
        "claude-desktop-mcp",
        "claude-code-cli",
        "codex-desktop",
        "codex-cli",
        "ollama-http",
        "ollama-cli",
        "copilot-gh",
        "copilot-vscode",
        "gemini-cli",
        "hermes-agent",
        "openclaw-gateway",
        "lm-studio",
        "synthetic-test-service",
    }.issubset(existing_contract_adapters())


def test_capabilities_load_from_yaml_contract() -> None:
    capabilities = capabilities_from_contract("ollama-http")
    assert capabilities is not None
    rows = {(row.capability_id, row.billing_class.value) for row in capabilities}
    assert ("classify_text", "local_resource") in rows
    assert ("local_chat", "local_resource") in rows


@pytest.mark.asyncio
async def test_schema_contains_spec_tables(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    import aiosqlite

    async with aiosqlite.connect(settings.state_path) as db:
        rows = await (await db.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    tables = {r[0] for r in rows}
    assert {"services", "capabilities", "intents", "dispatches", "receipts", "notifications", "audit_log", "selections"}.issubset(tables)


@pytest.mark.asyncio
async def test_capability_upsert_removes_stale_rows_for_adapter(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path, state_path=tmp_path / "state.sqlite", notifications_path=tmp_path / "notifications.jsonl", log_dir=tmp_path / "logs", repo_root=tmp_path)
    store = StateStore(settings)
    await store.initialize()
    first = [
        Capability(id="fake:old", adapter_name="fake", capability_id="old", rating_instruction=3, rating_quality=3, latency_band="medium", consequence_max=ConsequenceTier.LOW, billing_class=BillingClass.LOCAL_RESOURCE),
        Capability(id="fake:new", adapter_name="fake", capability_id="new", rating_instruction=3, rating_quality=3, latency_band="medium", consequence_max=ConsequenceTier.LOW, billing_class=BillingClass.LOCAL_RESOURCE),
    ]
    second = [
        Capability(id="fake:new", adapter_name="fake", capability_id="new", rating_instruction=3, rating_quality=3, latency_band="medium", consequence_max=ConsequenceTier.LOW, billing_class=BillingClass.LOCAL_RESOURCE),
    ]

    await store.upsert_capabilities(first)
    await store.upsert_capabilities(second)

    rows = await store.list_capabilities()
    assert {row["id"] for row in rows} == {"fake:new"}
