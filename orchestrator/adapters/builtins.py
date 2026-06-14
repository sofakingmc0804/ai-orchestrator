from __future__ import annotations

import asyncio
import os
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

from orchestrator.adapters.base import safe_cost
from orchestrator.models import BillingClass, Capability, ConsequenceTier, HealthState, ServiceInfo
from orchestrator.registry.contracts import capabilities_from_contract


REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNOR_HOME = Path(os.getenv("AI_RESOURCE_GOVERNOR_HOME", REPO_ROOT / ".runtime" / "ai-resource-governor")).expanduser()
GOVERNOR_BIN = GOVERNOR_HOME / "bin"
GOVERNED_COMMANDS = {"hermes", "openclaw"}
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _resolve_command(command: str) -> str | None:
    if command.lower() in GOVERNED_COMMANDS:
        for suffix in (".cmd", ".ps1", ".exe"):
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


async def _run_bounded(command: str, args: list[str], timeout: float = 30, env: dict[str, str] | None = None) -> dict[str, Any]:
    resolved = _resolve_command(command)
    if not resolved:
        return {"ok": False, "error": f"{command} not found"}
    if resolved.lower().endswith(".ps1"):
        exec_args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved, *args]
    else:
        exec_args = [resolved, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *exec_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env} if env else None,
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

    async def health_probe(self) -> ServiceInfo:
        exe = _resolve_command(self.command)
        pid, memory_mb, uptime_seconds = _process_snapshot(self.process_names)
        state = HealthState.HEALTHY if exe or pid else HealthState.STOPPED
        return ServiceInfo(
            id=self.service_id,
            name=self.label,
            service_group=self.service_group,
            adapter_name=self.name,
            protocol=self.protocol,
            install_path=exe,
            health_state=state,
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
        self.base_url = "http://127.0.0.1:11434"

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                if r.status_code == 200:
                    info.health_state = HealthState.HEALTHY
        except httpx.HTTPError:
            if info.health_state != HealthState.HEALTHY:
                info.health_state = HealthState.STOPPED
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
        status = await _run_bounded("hermes", ["status"], timeout=20)
        text = f"{status.get('stdout', '')}\n{status.get('stderr', '')}"
        if status.get("ok") and "Provider:" in text and "Custom endpoint" in text:
            info.health_state = HealthState.HEALTHY
        elif status.get("ok"):
            info.health_state = HealthState.DEGRADED
        elif status.get("timeout"):
            info.health_state = HealthState.DEGRADED
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Hermes adapter."}
        return {
            "ok": False,
            "terminal_state": "blocked_after_repair_attempt",
            "error": "Hermes is upstream of the orchestrator brain and is retired as a downstream dispatch target.",
            "repair_action": "Call `python -m orchestrator.cli.main route` or POST /api/route, then let the Hermes shim execute the selected worker.",
        }


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
        )

    async def health_probe(self) -> ServiceInfo:
        info = await super().health_probe()
        health = await _run_bounded("openclaw", ["gateway", "health"], timeout=20)
        text = f"{health.get('stdout', '')}\n{health.get('stderr', '')}"
        if health.get("ok") and "OK" in text:
            info.health_state = HealthState.HEALTHY
        elif health.get("timeout"):
            info.health_state = HealthState.DEGRADED
        return info

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        model = str(envelope.get("model") or "qwen2.5:0.5b")
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to OpenClaw adapter."}
        result = await _run_bounded(
            "openclaw",
            ["infer", "model", "run", "--json", "--gateway", "--model", model, "--prompt", prompt],
            timeout=180,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "error": str(result.get("error") or result.get("stderr") or "OpenClaw dispatch failed."),
                "repair_action": "Verify Ollama local generation, restart the loopback/token OpenClaw gateway, then run one bounded `openclaw infer model run --gateway --model ollama/qwen2.5:0.5b` proof.",
                "raw": result,
            }
        stdout = _clean_terminal_text(str(result.get("stdout") or ""))
        text = stdout
        try:
            payload = json.loads(stdout)
            if isinstance(payload, dict):
                text = str(payload.get("text") or payload.get("output") or payload.get("message") or stdout)
        except json.JSONDecodeError:
            pass
        return {"ok": True, "model": model, "text": text, "raw": result}


class ClaudePrintAdapter(StaticCliAdapter):
    def __init__(self, name: str, service_id: str, label: str, protocol: str) -> None:
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

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        prompt = str(envelope.get("intent", {}).get("raw_text") or envelope.get("prompt") or "").strip()
        model = str(envelope.get("model") or "sonnet")
        if not prompt:
            return {"ok": False, "error": "No prompt supplied to Claude adapter."}
        result = await _run_bounded(
            "claude",
            [
                "-p",
                "--output-format",
                "text",
                "--permission-mode",
                "dontAsk",
                "--model",
                model,
                "--max-budget-usd",
                "0.05",
                prompt,
            ],
            timeout=120,
        )
        stdout = _clean_terminal_text(str(result.get("stdout") or ""))
        stderr = _clean_terminal_text(str(result.get("stderr") or ""))
        if not result.get("ok") or "Exceeded USD budget" in stdout or "Exceeded USD budget" in stderr:
            return {
                "ok": False,
                "error": str(result.get("error") or stderr or stdout or "Claude dispatch failed."),
                "repair_action": "Verify Claude Max first-party auth and set a task-appropriate budget cap before retrying; do not fallback to Anthropic API.",
                "raw": result,
            }
        return {"ok": True, "model": model, "text": stdout, "raw": result}


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


def build_adapters() -> dict[str, StaticCliAdapter]:
    adapters: list[StaticCliAdapter] = [
        ClaudePrintAdapter("claude-desktop-mcp", "claude-desktop", "Claude Desktop", "mcp"),
        ClaudePrintAdapter("claude-code-cli", "claude-code-cli", "Claude Code CLI", "subprocess"),
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
        StaticCliAdapter("synthetic-test-service", "synthetic-test-service", "Synthetic Test Service", "test", "in-memory", "python", ["python.exe"], BillingClass.LOCAL_RESOURCE, ["synthetic_echo"], ConsequenceTier.LOW),
    ]
    return {a.name: a for a in adapters}
