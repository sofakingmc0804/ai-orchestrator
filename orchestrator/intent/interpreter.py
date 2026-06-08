from __future__ import annotations

import uuid

from orchestrator.models import ConsequenceTier, Intent, Selection


def consequence_for_text(text: str) -> ConsequenceTier:
    lowered = text.lower()
    if any(word in lowered for word in ["send", "publish", "commit", "delete", "external", "pay", "invoice"]):
        return ConsequenceTier.HIGH
    if any(word in lowered for word in ["edit", "write", "create", "repair", "build"]):
        return ConsequenceTier.MEDIUM
    return ConsequenceTier.LOW


def capability_for_text(text: str) -> str:
    lowered = text.lower()
    if "embedding" in lowered or "embed " in lowered or "vector" in lowered:
        return "embed_text"
    if "ocr" in lowered or "image" in lowered:
        return "ocr_document"
    if "model gateway" in lowered or "openclaw gateway" in lowered or "gateway model" in lowered:
        return "model_gateway"
    if "tool dispatch" in lowered or "use tools" in lowered or "hermes tool" in lowered:
        return "tool_dispatch"
    if "agent" in lowered or "agentic" in lowered:
        return "agentic_work"
    if "chat" in lowered:
        return "local_chat"
    if "code" in lowered or "bug" in lowered or "repo" in lowered:
        return "code_repair"
    if "summar" in lowered:
        return "summarize_text"
    return "classify_text"


def parse_intent(raw_text: str, selections: list[Selection] | None = None, source: str = "chat") -> Intent:
    capability = capability_for_text(raw_text)
    return Intent(
        id=f"int_{uuid.uuid4().hex[:16]}",
        source=source,
        raw_text=raw_text,
        parsed_payload={
            "verb": raw_text.strip().split(" ", 1)[0].lower() if raw_text.strip() else "classify",
            "required_capability": capability,
            "constraints": [],
            "deadline": None,
            "ambiguous": not raw_text.strip(),
        },
        selections=selections or [],
        consequence_tier=consequence_for_text(raw_text),
    )
