from __future__ import annotations

from orchestrator.provider_aliases import canonical_provider, lookup_by_provider, provider_keys


def test_provider_aliases_cover_display_and_quota_keys() -> None:
    assert canonical_provider("ollama_cloud") == "ollama-cloud"
    assert set(provider_keys("ollama_cloud")) >= {"ollama-cloud", "ollama_cloud", "ollama"}
    assert lookup_by_provider("ollama_cloud", {"ollama-cloud": {"remaining": 70}}) == {"remaining": 70}
