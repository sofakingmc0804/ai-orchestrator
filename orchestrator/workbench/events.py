from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping


GENESIS_CHECKSUM = hashlib.sha256(b"ai-orchestrator/workbench-ledger/genesis/v1").hexdigest()
EVENT_SCHEMA_VERSION = 1


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON numbers must be finite")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the sole canonical JSON representation used by the ledger."""
    _reject_non_finite(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical JSON: {exc}") from exc
    return encoded.encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def normalize_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            instant = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("timestamp must be ISO 8601") from exc
    elif isinstance(value, datetime):
        instant = value
    else:
        raise TypeError("timestamp must be a datetime or ISO 8601 string")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def event_checksum(envelope: Mapping[str, Any]) -> str:
    """Hash the canonical immutable envelope, which must not contain checksum."""
    if "checksum" in envelope:
        raise ValueError("checksum is not part of its own immutable envelope")
    return hashlib.sha256(canonical_json_bytes(dict(envelope))).hexdigest()
