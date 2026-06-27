from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


SALVAGE_RECEIPT_RELATIVE_PATH = Path(
    ".runtime/orchestrator/receipts/consolidation-example-codex-agent-salvage-20260617T023043Z.json"
)
APPROVED_PRIMITIVES = {"connector_management_ui", "mcp_config_generation"}
SECRET_VALUE_KEYS = {"env", "secrets", "credentials", "credential_values", "tokens", "auth"}
SECRET_MARKERS = ("authorization", "bearer", "token", "secret", "password", "credential", "cookie", "api-key", "apikey")


def _string(value: Any) -> str:
    return str(value or "").strip()


def _toml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _toml_array(values: list[Any]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _toml_inline_table(values: dict[str, Any]) -> str:
    entries = [(str(key), value) for key, value in values.items() if value not in (None, "")]
    if not entries:
        return "{}"
    return "{ " + ", ".join(f"{_toml_string(key)} = {_toml_string(value)}" for key, value in entries) + " }"


def _toml_server_table(name: str) -> str:
    normalized = re.sub(r"\s+", "-", name.strip()).strip(".")
    return f'[mcp_servers.{_toml_string(normalized)}]'


def _list_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _dict_strings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key).strip() and str(item).strip()}


def read_salvage_authority(repo_root: Path) -> dict[str, Any]:
    path = repo_root / SALVAGE_RECEIPT_RELATIVE_PATH
    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "receipt_type": None,
            "terminal_state": "missing",
            "decision": "salvage_receipt_missing",
            "approved_primitives": [],
            "forbidden_substitutes": [],
        }
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "exists": True,
            "path": str(path),
            "receipt_type": "unreadable",
            "terminal_state": "invalid",
            "decision": f"invalid_salvage_receipt: {exc}",
            "approved_primitives": [],
            "forbidden_substitutes": [],
        }

    primitives = []
    for item in receipt.get("salvage_primitives") or []:
        if not isinstance(item, dict):
            continue
        primitive = str(item.get("primitive") or "")
        if primitive in APPROVED_PRIMITIVES:
            primitives.append(item)

    return {
        "exists": True,
        "path": str(path),
        "receipt_type": receipt.get("receipt_type"),
        "terminal_state": receipt.get("terminal_state"),
        "decision": receipt.get("decision"),
        "source_path": receipt.get("source_path"),
        "target_consumer": receipt.get("target_consumer"),
        "approved_primitives": primitives,
        "forbidden_substitutes": list(receipt.get("forbidden_substitutes") or []),
    }


def connector_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "stdio-mcp",
            "name": "STDIO MCP",
            "transport": "stdio",
            "credential_policy": "env_vars_only",
            "required_fields": ["name", "command"],
        },
        {
            "id": "streamable-http-mcp",
            "name": "Streamable HTTP MCP",
            "transport": "http",
            "credential_policy": "bearer_token_env_var_only",
            "required_fields": ["name", "url"],
        },
        {
            "id": "authority-service",
            "name": "Existing Orchestrator Service",
            "transport": "authority_api",
            "credential_policy": "managed_by_existing_service",
            "required_fields": ["adapter_name"],
        },
    ]


def _is_mcp_like(service: dict[str, Any]) -> bool:
    protocol = _string(service.get("protocol")).lower()
    adapter = _string(service.get("adapter_name") or service.get("id")).lower()
    group = _string(service.get("service_group")).lower()
    return "mcp" in protocol or "mcp" in adapter or group == "mcp"


def build_connector_cards(services: list[dict[str, Any]], capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    caps_by_adapter: dict[str, list[str]] = defaultdict(list)
    for capability in capabilities:
        adapter_name = _string(capability.get("adapter_name"))
        capability_id = _string(capability.get("capability_id"))
        if adapter_name and capability_id:
            caps_by_adapter[adapter_name].append(capability_id)

    cards = []
    for service in services:
        adapter_name = _string(service.get("adapter_name") or service.get("id"))
        health_state = _string(service.get("health_state") or "unknown")
        mcp_like = _is_mcp_like(service)
        cards.append(
            {
                "id": _string(service.get("id") or adapter_name),
                "name": _string(service.get("name") or adapter_name),
                "adapter_name": adapter_name,
                "service_group": _string(service.get("service_group")),
                "protocol": _string(service.get("protocol")),
                "health_state": health_state,
                "enabled": health_state != "stopped",
                "authority": "state_store.services",
                "capabilities": sorted(set(caps_by_adapter.get(adapter_name, []))),
                "configuration": {
                    "status": "mcp_configurable" if mcp_like else "managed_by_orchestrator",
                    "transport": "mcp" if mcp_like else "authority_api",
                    "credential_policy": "existing_authority_only",
                },
            }
        )
    return cards


def build_connectors_payload(repo_root: Path, services: list[dict[str, Any]], capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    authority = read_salvage_authority(repo_root)
    return {
        "state": "produced",
        "source": "state_store.services",
        "runtime_boundary": "no_second_service_started",
        "credential_policy": "env_var_references_only",
        "authority": authority,
        "forbidden_substitutes": authority.get("forbidden_substitutes", []),
        "connectors": build_connector_cards(services, capabilities),
        "templates": connector_templates(),
    }


def validate_connector_config(config: dict[str, Any]) -> dict[str, Any]:
    for key in SECRET_VALUE_KEYS:
        value = config.get(key)
        if value not in (None, "", {}, []):
            return {
                "success": False,
                "state": "rejected",
                "reason": "credential_values_are_not_accepted",
                "message": f"{key} contains values. Use env_vars, bearer_token_env_var, or env_http_headers references.",
            }

    for header, value in _dict_strings(config.get("http_headers")).items():
        header_text = header.lower()
        value_text = value.lower()
        if any(marker in header_text or marker in value_text for marker in SECRET_MARKERS):
            return {
                "success": False,
                "state": "rejected",
                "reason": "credential_values_are_not_accepted",
                "message": "Static credential headers are not accepted. Use bearer_token_env_var or env_http_headers.",
            }

    name = _string(config.get("name"))
    transport = _string(config.get("transport") or "stdio").lower()
    if not name:
        return {"success": False, "state": "failed", "reason": "missing_name", "message": "Connector name is required."}
    if transport not in {"stdio", "http"}:
        return {"success": False, "state": "failed", "reason": "unsupported_transport", "message": "Transport must be stdio or http."}
    if transport == "stdio" and not _string(config.get("command")):
        return {"success": False, "state": "failed", "reason": "missing_command", "message": "STDIO connectors require command."}
    if transport == "http" and not _string(config.get("url")):
        return {"success": False, "state": "failed", "reason": "missing_url", "message": "HTTP connectors require url."}

    return {
        "success": True,
        "state": "validated",
        "reason": "shape_valid",
        "message": "Connector config is renderable without credential values.",
    }


def _render_connector_block(config: dict[str, Any]) -> str:
    transport = _string(config.get("transport") or "stdio").lower()
    lines = [_toml_server_table(_string(config.get("name")))]

    if transport == "stdio":
        lines.append(f"command = {_toml_string(config.get('command'))}")
        args = _list_strings(config.get("args"))
        if args:
            lines.append(f"args = {_toml_array(args)}")
        env_vars = _list_strings(config.get("env_vars"))
        if env_vars:
            lines.append(f"env_vars = {_toml_array(env_vars)}")
        cwd = _string(config.get("cwd"))
        if cwd:
            lines.append(f"cwd = {_toml_string(cwd)}")
    else:
        lines.append(f"url = {_toml_string(config.get('url'))}")
        bearer_token_env_var = _string(config.get("bearer_token_env_var"))
        if bearer_token_env_var:
            lines.append(f"bearer_token_env_var = {_toml_string(bearer_token_env_var)}")
        http_headers = _dict_strings(config.get("http_headers"))
        if http_headers:
            lines.append(f"http_headers = {_toml_inline_table(http_headers)}")
        env_http_headers = _dict_strings(config.get("env_http_headers"))
        if env_http_headers:
            lines.append(f"env_http_headers = {_toml_inline_table(env_http_headers)}")

    for key in ("startup_timeout_sec", "tool_timeout_sec"):
        value = config.get(key)
        if isinstance(value, int) and value > 0:
            lines.append(f"{key} = {value}")
    enabled = config.get("enabled")
    if isinstance(enabled, bool):
        lines.append(f"enabled = {str(enabled).lower()}")
    approval = _string(config.get("default_tools_approval_mode"))
    if approval:
        lines.append(f"default_tools_approval_mode = {_toml_string(approval)}")
    for key in ("enabled_tools", "disabled_tools"):
        values = _list_strings(config.get(key))
        if values:
            lines.append(f"{key} = {_toml_array(values)}")
    return "\n".join(lines)


def build_codex_mcp_config_text(configs: dict[str, Any] | list[dict[str, Any]]) -> str:
    items = [configs] if isinstance(configs, dict) else list(configs)
    rendered = [_render_connector_block(item) for item in items]
    return "# Generated by ai-orchestrator connector management\n" + "\n\n".join(rendered).rstrip() + "\n"


def build_config_preview(config: dict[str, Any]) -> dict[str, Any]:
    validation = validate_connector_config(config)
    if not validation.get("success"):
        return {"state": "rejected", "validation": validation, "config_text": ""}
    return {
        "state": "produced",
        "credential_policy": "env_var_references_only",
        "validation": validation,
        "config_text": build_codex_mcp_config_text(config),
    }
