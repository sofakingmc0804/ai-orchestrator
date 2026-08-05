from __future__ import annotations

import asyncio
import os
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import psutil

from orchestrator.adapters.base import safe_cost
from orchestrator.models import BillingClass, Capability, ConsequenceTier, HealthState, ServiceInfo
from orchestrator.hermes.claude_code import (
    CLAUDE_AUTH_OVERRIDE_ENV_VARS,
    build_headless_args,
    parse_headless_output,
)
from orchestrator.registry.contracts import capabilities_from_contract


REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNOR_HOME = Path(os.getenv("AI_RESOURCE_GOVERNOR_HOME", REPO_ROOT / ".runtime" / "ai-resource-governor")).expanduser()
GOVERNOR_BIN = GOVERNOR_HOME / "bin"
GOVERNED_COMMANDS = {"hermes", "openclaw"}
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
HERMES_DESKTOP_HOME = Path(
    os.getenv("HERMES_DESKTOP_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))
).expanduser()
HERMES_DESKTOP_EXE = HERMES_DESKTOP_HOME / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
HERMES_USAGE_DIR = Path(
    os.getenv("ORCHESTRATOR_HERMES_USAGE_DIR", str(GOVERNOR_HOME / "receipts" / "hermes-desktop"))
).expanduser()


def _resolve_command(command: str) -> str | None:
    if command.lower() in GOVERNED_COMMANDS:
        for suffix in (".ps1", ".cmd", ".exe"):
            candidate = GOVERNOR_BIN / f"{command}{suffix}"
            if candidate.exists():
                return str(candidate)
    if command.lower() == "lms":
        for candidate in (
            Path.home() / ".lmstudio" / "bin" / "lms.exe",
            Path("C:/Program Files/LM Studio/resources/app/.webpack/lms.exe"),
            Path.home() / "AppData" / "Local" / "LM Studio" / "resources" / "app" / ".webpack" / "lms.exe",
        ):
            if candidate.exists():
                return str(candidate)
    return shutil.which(command)


async def _run_bounded(
    command: str,
    args: list[str],
    timeout: float = 30,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    unset_env: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    resolved = _resolve_command(command)
    if not resolved:
        return {"ok": False, "error": f"{command} not found"}
    if resolved.lower().endswith(".ps1"):
        exec_args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved, *args]
    else:
        exec_args = [resolved, *args]
    try:
        child_env = dict(os.environ)
        for key in unset_env or ():
            child_env.pop(key, None)
        if env:
            child_env.update(env)
        proc = await asyncio.create_subprocess_exec(
            *exec_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env,
            cwd=str(cwd) if cwd else None,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            proc.kill()
        except (ProcessLookupError, psutil.NoSuchProcess):
            pass
        await proc.wait()
        return {"ok": False, "error": f"{command} timed out after {timeout:g}s", "timeout": True}
    return {
        "ok": proc.returncode == 0,
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "returncode": proc.returncode,
    }


def _process_snapshot(names: list[str]) -> tuple[int | None, float | None, float | None]:
    lowered = {n.lower() for n in names}
    for proc in psutil.process_iter(["pid", "name", "create_time", "memory_info"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            if name in lowered:
                mem = proc.info["memory_info"].rss / (1024 * 1024) if proc.info.get("memory_info") else None
                uptime = time.time() - float(proc.info.get("create_time") or time.time())
                return int(proc.info["pid"]), mem, uptime
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None, None, None


def _clean_terminal_text(value: str) -> str:
    return ANSI_PATTERN.sub("", value).strip()


class StaticCliAdapter:
    def __init__(
        self,
        name: str,
        service_id: str,
        label: str,
        service_group: str,
        protocol: str,
        command: str,
        process_names: list[str],
        billing_class: BillingClass,
        capabilities: list[str],
        consequence_max: ConsequenceTier = ConsequenceTier.MEDIUM,
        latency_band: str = "medium",
        optional: bool = False,
    ):
        self.name = name
        self.service_id = service_id
        self.label = label
        self.service_group = service_group
        self.protocol = protocol
        self.command = command
        self.process_names = process_names
        self.billing_class = billing_class
        self.capability_ids = capabilities
        self.consequence_max = consequence_max
        self.latency_band = latency_band
        self.optional = optional

    async def health_probe(self) -> ServiceInfo:
        exe = _resolve_command(self.command)
        pid, memory_mb, uptime_seconds = _process_snapshot(self.process_names)
        state = HealthState.HEALTHY if exe or pid else HealthState.STOPPED
        detail: str | None = None
        repair_action: str | None = None
        if state is HealthState.STOPPED:
            detail = f"Neither the '{self.command}' executable nor any of its processes ({', '.join(self.process_names)}) were found."
            repair_action = f"Install or start {self.label} so that '{self.command}' is on PATH or its process is running."
        return ServiceInfo(
            id=self.service_id,
            name=self.label,
            service_group=self.service_group,
            adapter_name=self.name,
            protocol=self.protocol,
            install_path=exe,
            health_state=state,
            detail=detail,
            repair_action=repair_action,
            optional=self.optional,
            pid=pid,
            memory_mb=memory_mb,
            uptime_seconds=uptime_seconds,
        )

    async def capabilities(self) -> list[Capability]:
        contract_capabilities = capabilities_from_contract(self.name)
        if contract_capabilities is not None:
            return contract_capabilities
        return [
            Capability(
                id=f"{self.name}:{cap}",
                adapter_name=self.name,
                capability_id=cap,
                rating_instruction=3,
                rating_quality=3,
                latency_band=self.latency_band,
                consequence_max=self.consequence_max,
                billing_class=self.billing_class,
            )
            for cap in self.capability_ids
        ]

    async def cost_estimate(self, capability_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return safe_cost(self.billing_class)

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"{self.name} dispatch is registered but not enabled for this intent yet.",
            "repair_action": "Use local Ollama dispatch for v1 round trips or implement this adapter's native dispatch method.",
        }


class OllamaHttpAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="ollama-http",
            service_id="ollama-http",
            label="Ollama HTTP",
            service_group="local_models",
            protocol="http",
            command="ollama",
            process_names=["ollama.exe"],
            billing_class=BillingClass.LOCAL_RESOURCE,
            capabilities=["classify_text", "summarize_text", "ocr_document", "embed_text", "local_chat"],
            consequence_max=ConsequenceTier.MEDIUM,
        )
        self.base_url = "http://localhost:11434"

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                tags = await client.get(f"{self.base_url}/api/tags")
                chat = await client.get(f"{self.base_url}/api/chat")
                if tags.status_code == 200 and chat.status_code != 404:
                    info.health_state = HealthState.HEALTHY
                elif tags.status_code == 200:
                    info.health_state = HealthState.DEGRADED
                    info.detail = "Ollama answers /api/tags but does not expose the /api/chat dispatch route."
                    info.repair_action = "Restart Ollama so the running server matches the installed client, then refresh service discovery."
                else:
                    info.health_state = HealthState.DEGRADED
                    info.detail = f"Ollama model inventory returned HTTP {tags.status_code}."
                    info.repair_action = "Repair the local Ollama HTTP server and refresh service discovery."
        except httpx.HTTPError:
            info.health_state = HealthState.DEGRADED if info.health_state == HealthState.HEALTHY else HealthState.STOPPED
            info.detail = "Ollama executable or process is present, but its HTTP service did not answer the dispatch health probe."
            info.repair_action = "Start or restart Ollama, then refresh service discovery."
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        model = str(envelope.get("model") or "qwen2.5:0.5b")
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Ollama adapter."}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return concise, useful output. Do not call external services."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"num_predict": 256},
        }
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(f"{self.base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        return {
            "ok": True,
            "model": model,
            "text": data.get("message", {}).get("content", ""),
            "raw": data,
        }


class OllamaCloudAdapter(OllamaHttpAdapter):
    def __init__(self) -> None:
        StaticCliAdapter.__init__(
            self,
            name="ollama-cloud",
            service_id="ollama-cloud",
            label="Ollama Cloud",
            service_group="cloud_clients",
            protocol="ollama-cloud-http",
            command="ollama",
            process_names=["ollama.exe"],
            billing_class=BillingClass.SUBSCRIPTION_USAGE,
            capabilities=["coding_chat", "code_repair", "general_reasoning", "local_chat"],
            consequence_max=ConsequenceTier.MEDIUM,
        )
        self.base_url = "http://127.0.0.1:11434"


class OllamaCliAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="ollama-cli",
            service_id="ollama-cli",
            label="Ollama CLI",
            service_group="local_models",
            protocol="subprocess",
            command="ollama",
            process_names=["ollama.exe"],
            billing_class=BillingClass.LOCAL_RESOURCE,
            capabilities=["local_chat", "model_inventory"],
        )

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        model = str(envelope.get("model") or "qwen2.5:0.5b")
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Ollama CLI adapter."}
        proc = await asyncio.create_subprocess_exec(
            "ollama",
            "run",
            model,
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            return {"ok": False, "error": stderr.decode(errors="replace")}
        return {"ok": True, "model": model, "text": _clean_terminal_text(stdout.decode(errors="replace"))}


class HermesAgentAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="hermes-agent",
            service_id="hermes-agent",
            label="Hermes Agent",
            service_group="agent_hosts",
            protocol="subprocess-gateway",
            command="hermes",
            process_names=["hermes.exe", "python.exe"],
            billing_class=BillingClass.LOCAL_RESOURCE,
            capabilities=["agentic_work", "tool_dispatch", "local_chat"],
            consequence_max=ConsequenceTier.MEDIUM,
        )

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        info.install_path = str(HERMES_DESKTOP_EXE) if HERMES_DESKTOP_EXE.is_file() else None
        status = await _run_bounded(
            "hermes",
            ["status"],
            timeout=20,
            env={"HERMES_HOME": str(HERMES_DESKTOP_HOME)},
        )
        stdout = str(status.get("stdout", "") or "")
        stderr = str(status.get("stderr", "") or "")
        text = _clean_terminal_text(f"{stdout}\n{stderr}")
        model_match = re.search(r"(?mi)^\s*Model:\s*(?P<value>[^\r\n]+)", text)
        provider_match = re.search(r"(?mi)^\s*Provider:\s*(?P<value>[^\r\n]+)", text)
        model = model_match.group("value").strip() if model_match else ""
        provider = provider_match.group("value").strip() if provider_match else ""
        if status.get("ok") and model and provider:
            info.health_state = HealthState.HEALTHY
            info.detail = None
            info.repair_action = None
        elif status.get("ok"):
            info.health_state = HealthState.DEGRADED
            info.detail = (
                "Desktop Hermes status did not report both a configured Model and Provider."
                + (f" Output: {text[:200]}" if text else "")
            )
            info.repair_action = "Use the Desktop Hermes model settings to configure a model/provider pair, then re-probe."
        elif status.get("timeout"):
            info.health_state = HealthState.DEGRADED
            info.detail = "Desktop Hermes status did not respond within 20s."
            info.repair_action = "Restart the Desktop Hermes backend, then re-probe."
        elif any(marker in str(status.get("error", "")).lower() for marker in ("missing", "not found")):
            info.health_state = HealthState.STOPPED
            info.detail = str(status.get("error"))
            info.repair_action = "Restore the Desktop Hermes runtime at its configured Windows home, then re-probe."
        else:
            info.health_state = HealthState.DEGRADED
            rc = status.get("returncode")
            info.detail = (
                "Desktop Hermes status exited with an error"
                + (f" (rc={rc})" if rc is not None else "")
                + (f": {text[:200]}" if text else ".")
            )
            info.repair_action = "Inspect the Desktop Hermes error above and repair its configured runtime."
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Hermes adapter."}
        project_root_text = str(envelope.get("project_root") or envelope.get("intent", {}).get("project_root") or "")
        project_root = Path(project_root_text) if project_root_text else None
        HERMES_USAGE_DIR.mkdir(parents=True, exist_ok=True)
        usage_path = HERMES_USAGE_DIR / f"hermes-{uuid.uuid4().hex[:16]}.json"
        response = await _run_bounded(
            "hermes",
            ["-z", prompt, "--usage-file", str(usage_path), "--orchestrator-job-class", "repo_coding"],
            timeout=900,
            env={"HERMES_HOME": str(HERMES_DESKTOP_HOME)},
            cwd=project_root if project_root and project_root.is_dir() else None,
        )
        text = _clean_terminal_text(str(response.get("stdout", "") or ""))
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.is_file() else {}
        except json.JSONDecodeError:
            usage = {}
        raw = {"usage_path": str(usage_path), "usage": usage, "stderr": response.get("stderr")}
        if not response.get("ok"):
            return {
                "ok": False,
                "error": str(response.get("error") or response.get("stderr") or "Desktop Hermes dispatch failed."),
                "raw": raw,
            }
        return {"ok": True, "model": "desktop-hermes", "text": text, "raw": raw}


class OpenClawGatewayAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openclaw-gateway",
            service_id="openclaw-gateway",
            label="OpenClaw Gateway",
            service_group="agent_hosts",
            protocol="http-gateway",
            command="openclaw",
            process_names=["node.exe"],
            billing_class=BillingClass.LOCAL_RESOURCE,
            capabilities=["agentic_work", "model_gateway", "local_chat"],
            consequence_max=ConsequenceTier.MEDIUM,
            latency_band="slow",
            optional=True,
        )

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        info.health_state = HealthState.STOPPED
        info.version = "retired-2026-06-16"
        info.optional = True
        info.detail = "OpenClaw was retired during the 2026-06-16 consolidation and is intentionally not running."
        info.repair_action = (
            "No action needed; this service is retired. Route work with "
            "`python -m orchestrator.cli.main route` or POST /api/route. "
            "Archived state: C:\\Users\\Couch\\Archive\\openclaw-retired-2026-06-16."
        )
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        model = str(envelope.get("model") or "")
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to OpenClaw adapter."}
        return {
            "ok": False,
            "terminal_state": "continuation_required",
            "error": "OpenClaw was retired during the 2026-06-16 consolidation and is no longer a downstream dispatch target.",
            "repair_action": "Use `python -m orchestrator.cli.main route` or POST /api/route; archived OpenClaw state is under C:\\Users\\Couch\\Archive\\openclaw-retired-2026-06-16.",
            "model": model or "openclaw-retired",
        }


class ClaudePrintAdapter(StaticCliAdapter):
    def __init__(self, name: str, service_id: str, label: str, protocol: str, *, oauth_only: bool = False) -> None:
        super().__init__(
            name=name,
            service_id=service_id,
            label=label,
            service_group="cloud_clients",
            protocol=protocol,
            command="claude",
            process_names=["Claude.exe", "node.exe"],
            billing_class=BillingClass.SUBSCRIPTION_QUOTA,
            capabilities=["long_context_reasoning", "document_review", "agentic_work", "code_repair"],
            consequence_max=ConsequenceTier.HIGH,
            latency_band="medium",
        )
        self.oauth_only = oauth_only

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        if not self.oauth_only or info.health_state is HealthState.STOPPED:
            return info
        result = await _run_bounded(
            "claude",
            ["auth", "status"],
            timeout=20,
            unset_env=CLAUDE_AUTH_OVERRIDE_ENV_VARS,
        )
        stdout = _clean_terminal_text(str(result.get("stdout") or ""))
        try:
            status = json.loads(stdout)
        except json.JSONDecodeError:
            status = {}
        if result.get("ok") and isinstance(status, dict) and status.get("loggedIn") and status.get("authMethod") == "claude.ai":
            info.health_state = HealthState.HEALTHY
            info.detail = "Claude Code CLI is authenticated through the provider-owned Claude.ai OAuth session."
        else:
            info.health_state = HealthState.DEGRADED
            info.detail = "Claude Code is installed, but its Claude.ai OAuth session was not verified in an OAuth-only child environment."
            info.repair_action = "Run `claude auth login` and keep ANTHROPIC_API_KEY unset for subscription dispatch."
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        model = str(envelope.get("model") or "sonnet")
        fallback_model = str(envelope.get("fallback_model") or "").strip() or None
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Claude adapter."}
        args = build_headless_args(prompt, model, fallback_model)
        result = await _run_bounded(
            "claude",
            args,
            timeout=180,
            unset_env=CLAUDE_AUTH_OVERRIDE_ENV_VARS if self.oauth_only else None,
        )
        stdout = str(result.get("stdout") or "")
        stderr = _clean_terminal_text(str(result.get("stderr") or ""))
        text, payload = parse_headless_output(stdout)
        if not result.get("ok") or not text:
            return {
                "ok": False,
                "error": str(result.get("error") or stderr or stdout or "Claude dispatch failed."),
                "repair_action": "Verify `claude auth status` in an OAuth-only environment; do not set ANTHROPIC_API_KEY for Claude subscription dispatch.",
                "raw": {"returncode": result.get("returncode"), "stderr": stderr, "payload": payload},
            }
        usage = payload.get("usage") if isinstance(payload, dict) and isinstance(payload.get("usage"), dict) else None
        return {
            "ok": True,
            "model": model,
            "text": text,
            "usage": usage,
            "raw": {"returncode": result.get("returncode"), "payload": payload},
        }


class CodexExecAdapter(StaticCliAdapter):
    def __init__(self, name: str, service_id: str, label: str, protocol: str) -> None:
        super().__init__(
            name=name,
            service_id=service_id,
            label=label,
            service_group="cloud_clients",
            protocol=protocol,
            command="codex",
            process_names=["Codex.exe", "codex.exe"],
            billing_class=BillingClass.SUBSCRIPTION_QUOTA,
            capabilities=["code_repair", "repo_analysis", "agentic_work"],
            consequence_max=ConsequenceTier.HIGH,
            latency_band="medium",
        )

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Codex adapter."}
        out_file = Path(os.getenv("TEMP", str(Path.home()))) / f"codex-proof-{int(time.time() * 1000)}.txt"
        result = await _run_bounded(
            "codex",
            [
                "exec",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "-m",
                "gpt-5.5",
                "--sandbox",
                "read-only",
                "--ignore-rules",
                "--ephemeral",
                "-c",
                "approval_policy='never'",
                "-c",
                "model_reasoning_effort='low'",
                "-c",
                "service_tier='fast'",
                "-o",
                str(out_file),
                prompt,
            ],
            timeout=180,
        )
        text = ""
        try:
            if out_file.exists():
                text = _clean_terminal_text(out_file.read_text(encoding="utf-8"))
        except OSError:
            text = ""
        if not text:
            text = _clean_terminal_text(str(result.get("stdout") or ""))
        if not result.get("ok") or not text:
            return {
                "ok": False,
                "error": str(result.get("error") or result.get("stderr") or "Codex dispatch failed."),
                "repair_action": "Verify Codex ChatGPT login and keep routine proofs on low-effort/read-only settings; do not fallback to OpenAI API.",
                "raw": result,
            }
        return {"ok": True, "model": "codex-account-low-effort", "text": text, "raw": result}


class CopilotCliAdapter(StaticCliAdapter):
    def __init__(self, name: str, service_id: str, label: str, protocol: str) -> None:
        super().__init__(
            name=name,
            service_id=service_id,
            label=label,
            service_group="cloud_clients",
            protocol=protocol,
            command="copilot",
            process_names=["Code.exe", "node.exe"],
            billing_class=BillingClass.SUBSCRIPTION_QUOTA,
            capabilities=["code_completion", "code_review", "coding_chat", "code_repair"],
            consequence_max=ConsequenceTier.MEDIUM,
            latency_band="medium",
        )

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Copilot adapter."}
        result = await _run_bounded(
            "copilot",
            ["-p", prompt, "--available-tools=", "--no-custom-instructions", "--stream", "off", "-s"],
            timeout=120,
        )
        stdout = _clean_terminal_text(str(result.get("stdout") or ""))
        if not result.get("ok") or not stdout:
            return {
                "ok": False,
                "error": str(result.get("error") or result.get("stderr") or "Copilot CLI dispatch failed."),
                "repair_action": "Run `copilot login` or provide a safe GitHub Copilot token; do not use the deprecated `gh copilot` extension as the default lane.",
                "raw": result,
            }
        return {"ok": True, "model": "github-copilot-cli", "text": stdout, "raw": result}


class GeminiCliAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="gemini-cli",
            service_id="gemini-cli",
            label="Gemini CLI",
            service_group="cloud_clients",
            protocol="subprocess",
            command="gemini",
            process_names=["node.exe"],
            billing_class=BillingClass.SUBSCRIPTION_QUOTA,
            capabilities=["large_context", "general_reasoning", "coding_chat", "code_repair"],
            consequence_max=ConsequenceTier.MEDIUM,
            latency_band="medium",
        )

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Gemini adapter."}
        result = await _run_bounded("gemini", [prompt], timeout=90, env={"NODE_OPTIONS": "--use-system-ca"})
        stdout = _clean_terminal_text(str(result.get("stdout") or ""))
        stderr = _clean_terminal_text(str(result.get("stderr") or ""))
        if not result.get("ok") or not stdout:
            return {
                "ok": False,
                "error": str(result.get("error") or stderr or "Gemini CLI dispatch failed."),
                "repair_action": "Repair Gemini OAuth/Node CA with the no-touch operator login path; direct Gemini API remains disabled until reclassified as already-paid.",
                "raw": result,
            }
        return {"ok": True, "model": "gemini-cli-oauth", "text": stdout, "raw": result}


class LmStudioAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="lm-studio",
            service_id="lm-studio",
            label="LM Studio",
            service_group="local_models",
            protocol="openai-http",
            command="lms",
            process_names=["llmster.exe", "LM Studio.exe"],
            billing_class=BillingClass.LOCAL_RESOURCE,
            capabilities=["local_chat", "embeddings"],
            consequence_max=ConsequenceTier.MEDIUM,
            latency_band="medium",
        )
        self.base_url = "http://127.0.0.1:1234"

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/v1/models")
            if r.status_code == 200:
                data = r.json()
                info.health_state = HealthState.HEALTHY if data.get("data") else HealthState.DEGRADED
        except httpx.HTTPError:
            info.health_state = HealthState.STOPPED
        except ValueError:
            info.health_state = HealthState.DEGRADED
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to LM Studio adapter."}
        intent = envelope.get("intent") or {}
        parsed_payload = intent.get("parsed_payload") if isinstance(intent, dict) else {}
        required_capability = str((parsed_payload or {}).get("required_capability") or "")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                models = await client.get(f"{self.base_url}/v1/models")
                models.raise_for_status()
                model_rows = models.json().get("data") or []
                first_model = model_rows[0] if model_rows else {}
                model = str((first_model or {}).get("id") or envelope.get("model") or "")
                if not model:
                    return {"ok": False, "error": "LM Studio has no available model.", "repair_action": "Start LM Studio headlessly and register a local model on 127.0.0.1:1234."}
                if required_capability == "embeddings" or "embed" in model.lower():
                    embedding_payload: dict[str, Any] = {"model": model, "input": prompt}
                    r = await client.post(f"{self.base_url}/v1/embeddings", json=embedding_payload)
                    r.raise_for_status()
                    data = r.json()
                    vector = (((data.get("data") or [{}])[0] or {}).get("embedding") or [])
                    return {"ok": True, "model": model, "text": f"embedding_dimensions={len(vector)}", "raw": {"model": data.get("model"), "object": data.get("object"), "dimensions": len(vector)}}
                chat_payload: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "max_tokens": 128}
                r = await client.post(f"{self.base_url}/v1/chat/completions", json=chat_payload)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "repair_action": "Start LM Studio's local server on 127.0.0.1:1234 or remove it from available local adapters.",
            }
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        return {"ok": True, "model": model, "text": text, "raw": data}


class SyntheticTestServiceAdapter(StaticCliAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="synthetic-test-service",
            service_id="synthetic-test-service",
            label="Synthetic Test Service",
            service_group="test",
            protocol="in-memory",
            command="python",
            process_names=["python.exe"],
            billing_class=BillingClass.LOCAL_RESOURCE,
            capabilities=["synthetic_echo"],
            consequence_max=ConsequenceTier.LOW,
            latency_band="fast",
            optional=True,
        )

    async def health_probe(self) -> ServiceInfo:
        return ServiceInfo(
            id=self.service_id,
            name=self.label,
            service_group=self.service_group,
            adapter_name=self.name,
            protocol=self.protocol,
            health_state=HealthState.HEALTHY,
            optional=True,
        )

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        intent = envelope.get("intent") if isinstance(envelope.get("intent"), dict) else {}
        prompt = str(intent.get("raw_text") or envelope.get("prompt") or "").strip()
        return {
            "ok": True,
            "model": "synthetic-echo",
            "text": f"synthetic_echo: {prompt or 'OK'}",
            "raw": {
                "adapter": self.name,
                "proof": "in_memory_dispatch",
                "dispatch_id": envelope.get("dispatch_id"),
            },
        }


def build_adapters() -> dict[str, StaticCliAdapter]:
    adapters: list[StaticCliAdapter] = [
        ClaudePrintAdapter("claude-desktop-mcp", "claude-desktop", "Claude Desktop", "mcp"),
        ClaudePrintAdapter("claude-code-cli", "claude-code-cli", "Claude Code CLI", "subprocess", oauth_only=True),
        CodexExecAdapter("codex-desktop", "codex-desktop", "Codex Desktop", "mcp-subprocess"),
        CodexExecAdapter("codex-cli", "codex-cli", "Codex CLI", "subprocess"),
        OllamaHttpAdapter(),
        OllamaCloudAdapter(),
        OllamaCliAdapter(),
        CopilotCliAdapter("copilot-gh", "copilot-gh", "GitHub Copilot CLI", "copilot-cli"),
        CopilotCliAdapter("copilot-vscode", "copilot-vscode", "GitHub Copilot VS Code", "vscode-extension"),
        GeminiCliAdapter(),
        HermesAgentAdapter(),
        OpenClawGatewayAdapter(),
        LmStudioAdapter(),
        SyntheticTestServiceAdapter(),
    ]
    return {a.name: a for a in adapters}
