from __future__ import annotations

from orchestrator.adapters.builtins import build_adapters
from orchestrator.models import BillingClass
from orchestrator.surface_adapters import dispatch_adapter_for_worker


def test_omniroute_is_registered_without_being_cost_admitted() -> None:
    adapters = build_adapters()

    assert "omniroute" in adapters
    assert adapters["omniroute"].billing_class == BillingClass.UNKNOWN_COST


def test_omniroute_surface_resolves_to_its_native_adapter() -> None:
    assert dispatch_adapter_for_worker({"surface": "omniroute"}) == "omniroute"
