from __future__ import annotations

import pytest

import orchestrator.adapters.builtins as builtins
from orchestrator.hermes.brain_bridge import _selected_payload
from orchestrator.adapters.builtins import HermesAgentAdapter
from orchestrator.adapters.builtins import _resolve_command


@pytest.mark.parametrize(
    ("provider_id", "surface", "model_id", "hermes_provider"),
    [
        ("openrouter", "openrouter", "z-ai/glm-5.2:free", "openrouter"),
        ("opencode-zen", "opencode-zen", "glm-5.2", "opencode-zen"),
        ("nous", "nous", "z-ai/glm-5.2", "nous"),
        ("openai-codex", "openai-codex", "gpt-5.3-codex", "openai-codex"),
    ],
)
def test_supported_hermes_capacity_lanes_are_executable(
    provider_id: str,
    surface: str,
    model_id: str,
    hermes_provider: str,
) -> None:
    selected = _selected_payload(
        {
            "worker_id": f"{model_id}@{provider_id}",
            "provider_id": provider_id,
            "surface": surface,
            "model_id": model_id,
            "contract_type": "subscription_quota",
        }
    )

    assert selected["adapter_name"] == "hermes-agent"
    assert selected["hermes_provider"] == hermes_provider
    assert selected["hermes_model"] == model_id
    assert selected["lane_state"] == "supported"


def test_metered_hermes_provider_remains_blocked() -> None:
    selected = _selected_payload(
        {
            "worker_id": "claude-sonnet-4-6@anthropic-api",
            "provider_id": "anthropic-api",
            "surface": "anthropic-api",
            "model_id": "claude-sonnet-4-6",
            "contract_type": "metered_extra_cost",
        }
    )

    assert selected["hermes_provider"] == "anthropic"
    assert selected["lane_state"] == "blocked_metered"
    assert selected["adapter_name"] is None


def test_openrouter_paid_model_is_blocked_even_when_worker_contract_is_quota() -> None:
    selected = _selected_payload(
        {
            "worker_id": "paid-model@openrouter",
            "provider_id": "openrouter",
            "surface": "openrouter",
            "model_id": "anthropic/claude-sonnet-4.6",
            "contract_type": "subscription_quota",
        }
    )

    assert selected["hermes_provider"] == "openrouter"
    assert selected["lane_state"] == "blocked_metered"
    assert selected["adapter_name"] is None


def test_direct_codex_desktop_worker_is_not_claimed_as_hermes_execution() -> None:
    selected = _selected_payload(
        {
            "worker_id": "codex-account@codex-chatgpt",
            "provider_id": "codex-chatgpt",
            "surface": "codex-chatgpt",
            "adapter_name": "codex-desktop",
            "model_id": "codex-account",
            "contract_type": "subscription_unlimited",
        }
    )

    assert selected["hermes_provider"] == "openai-codex"
    assert selected["lane_state"] == "dispatch_only"
    assert selected["adapter_name"] == "codex-desktop"


@pytest.mark.asyncio
async def test_hermes_agent_dispatch_carries_selected_provider_and_model(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(builtins, "HERMES_USAGE_DIR", tmp_path)

    async def fake_run(command, args, **kwargs):
        captured["command"] = command
        captured["args"] = list(args)
        usage_path = next(
            (str(args[index + 1]) for index, value in enumerate(args[:-1]) if value == "--usage-file"),
            "",
        )
        if usage_path:
            from pathlib import Path

            Path(usage_path).write_text("{}", encoding="utf-8")
        return {"ok": True, "stdout": "OK", "stderr": ""}

    monkeypatch.setattr(builtins, "_run_bounded", fake_run)

    result = await HermesAgentAdapter().dispatch(
        {
            "provider": "opencode-zen",
            "model": "glm-5.2",
            "intent": {"raw_text": "Return exactly OK."},
        }
    )

    assert result["ok"] is True
    assert captured["args"] == [
        "-z",
        "Return exactly OK.",
        "--usage-file",
        captured["args"][3],
        "--orchestrator-job-class",
        "repo_coding",
        "--provider",
        "opencode-zen",
        "-m",
        "glm-5.2",
    ]


def test_hermes_commands_use_current_orchestrator_router() -> None:
    from pathlib import Path

    router = Path(__file__).resolve().parents[1] / "scripts" / "hermes-router.ps1"

    assert router.is_file()
    assert _resolve_command("hermes") == str(router)
    script = router.read_text(encoding="utf-8").lower()
    assert "ai-resource-governor" not in script
    assert "openrouter" in script
    assert "opencode-zen" in script
    assert "if ($applyrouteselection)" in script
    assert "dispatch_only" in script
