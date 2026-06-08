# Complete AI Provider & Model Roster — CANONICAL
## Machine: DESKTOP-LOOCRQ2 (Dell Inspiron 7706 2n1, Serial JMCH3B3)
## Date: 2026-06-06
## Scope: Every AI provider and model reachable from this PC through any channel (maximal interpretation)
## Status: CANONICAL. Supersedes DELL_AI_MODEL_ROSTER_2026-06-04.md and MSI_AI_ROSTER_2026-06-04.md (both archived this session).

---

## HOW THIS WAS BUILT (verification method)

Every "installed / configured" claim below was verified by **live PowerShell on the Windows host** on 2026-06-06 (Desktop Commander bridge): machine identity, CLI presence + versions, npm globals, VS Code extension list, registry uninstall keys, `ollama list`, AppData program folders, home dot-config folders, and individual tool config files. Every "current model lineup" claim was **web-verified on 2026-06-06** against official provider docs and current secondary sources, because this assistant's training predates these releases. Items that could not be runtime-verified are explicitly flagged LOW confidence with a refinement path in PART J.

**Machine-identity correction:** Prior project memory described an "MSI Vector GP66." Runtime confirms this PC is a **Dell Inspiron 7706 2n1** (`DESKTOP-LOOCRQ2`, serial `JMCH3B3`, i7-1165G7, 4c/8t, 63.7 GB RAM, NVIDIA GeForce MX350 2 GB + Intel Iris Xe, Windows 11 Pro 26200). The MSI naming is stale and should not be reused.

---

# PART A — CLOUD API PROVIDERS & FRONTIER MODELS (web-verified 2026-06-06)

## A.1 Anthropic Claude  (HIGH confidence — installed + web-verified)
Access on this PC: Claude Desktop/Cowork (this session), Claude Code CLI 2.1.71, VS Code ext anthropic.claude-code 2.1.165, Chrome extension (paired), Cline 3.88.0, AI Toolkit (configured key), Python `anthropic` SDK 0.74.0. Also reachable through Antigravity and (via Bedrock) Kiro.

| Model | API string | Context | $/MTok in | $/MTok out | Notes |
|-------|-----------|---------|-----------|-----------|-------|
| Claude Opus 4.8 | claude-opus-4-8 | 1M | 5.00 | 25.00 | Current flagship (rel. 2026-05-28). 1M at standard price. |
| Claude Opus 4.7 | claude-opus-4-7 | 1M | 5.00 | 25.00 | Prior Opus (2026-04-16). |
| Claude Opus 4.6 | claude-opus-4-6 | 1M | 5.00 | 25.00 | Adaptive thinking, 128K max output. |
| Claude Sonnet 4.6 | claude-sonnet-4-6 | 1M | 3.00 | 15.00 | Best speed/intelligence balance. |
| Claude Haiku 4.5 | claude-haiku-4-5-20251001 | 200K | 1.00 | 5.00 | Fastest / cheapest. |

Also distributed via Amazon Bedrock, Google Vertex AI, and Microsoft Foundry (relevant because Kiro→Bedrock and AI Foundry channels exist on this PC).

## A.2 OpenAI  (HIGH confidence — installed + web-verified)
Access: Codex CLI 0.128.0, VS Code ext openai.chatgpt 26.602.40724, Microsoft Copilot app, GitHub Models (AI Toolkit), Cline, Python `openai` SDK 1.65.5.

| Model | API string | Context | Notes |
|-------|-----------|---------|-------|
| GPT-5.5 / GPT-5.5 Pro | gpt-5.5 / gpt-5.5-pro | 1M | Current frontier (API 2026-04-24). Pro = extended compute. |
| GPT-5.4 / mini / nano | gpt-5.4 / -mini / -nano | 1M | Production workhorse + budget tiers. |
| GPT-5.3-Codex / GPT-5.2-Codex | gpt-5.3-codex / gpt-5.2-codex | — | Agentic coding model family (powers Codex). |
| GPT-5.2 / GPT-5 | gpt-5.2 / gpt-5 | 1M | Prior gen. |
| GPT-4.1 / 4.1-mini | gpt-4.1 / gpt-4.1-mini | 1M | Best instruction-following + tool calling. Retired from ChatGPT, live on API. |
| GPT-4o | gpt-4o | 128K | Live multimodal. API-only now. |
| o3 / o3-mini / o4-mini | o3 / o3-mini / o4-mini | 200K | Reasoning ("proof-heavy") tier. |

## A.3 Google Gemini  (HIGH confidence — installed + web-verified)
Access: Gemini CLI 0.4.1, VS Code google.geminicodeassist 2.85.0 + gemini-cli-vscode-ide-companion 0.20.0, Antigravity (native), AI Toolkit (configured key), `@google/clasp`.

| Model | API string | Context | Notes |
|-------|-----------|---------|-------|
| Gemini 3.5 Flash | gemini-3.5-flash | 1M | Latest GA (May 2026). Frontier agentic/coding at low cost. |
| Gemini 3.1 Pro | gemini-3.1-pro | 1M | Top reasoning (77.1% ARC-AGI-2). |
| Gemini 3.1 Flash / Flash-Lite | gemini-3.1-flash / -flash-lite | 1M | Fast multimodal / budget. |
| Gemini 3 Flash (preview) | gemini-3-flash-preview | 1M | Preview. |
| Gemini 2.5 Pro / Flash / Flash-Lite | gemini-2.5-* | 1M | Mature value tier (2.5 Flash ≈ best $/perf). |
| Gemini Nano | (on-device) | — | On-device tier. |
| Gemini 3.5 Pro | gemini-3.5-pro | 1M | LOW confidence — expected but not GA-confirmed 2026-06-06. |

Note: Gemini 2.0 / 2.0 Flash-Lite were shut down 2026-06-01.

## A.4 xAI Grok  (MEDIUM confidence — API-only, web-verified)
Access: HTTP API only (no dedicated CLI/extension installed). Reachable via OpenClaw/Hermes/Cline if a key is added; also via X.

| Model | API string | Context | Notes |
|-------|-----------|---------|-------|
| Grok 4.3 | grok-4.3 | 1M | Reasoning-first flagship (rel. 2026-04-30). Native video input. |
| Grok Build 0.1 | grok-build-0.1 | 256K | Dedicated software-engineering model (API beta 2026-05-28). |
| Grok Imagine (Image / Image-Quality / Video) | grok-imagine-* | — | Image + video generation with synced audio. |

(Eight legacy Grok models — grok-3, grok-4-fast, grok-code-fast-1, etc. — were retired 2026-05-15.)

## A.5 Other cloud providers reachable via installed SDKs / OpenAI-compatible clients  (MEDIUM confidence)
These have **no dedicated app** but are reachable from this PC the moment a key is set, through OpenClaw, Hermes, Cline, the `openai`/`langchain` SDKs (OpenAI-compatible base URL), Ollama Cloud, or aggregator gateways in PART B.

| Provider | Representative models | Channel on this PC |
|----------|----------------------|--------------------|
| DeepSeek | V4 Pro, R1 | GitHub Models (AI Toolkit), Ollama (distill), OpenAI-compatible SDK |
| Mistral AI | Large 3, Small 4 | OpenAI-compatible SDK, Ollama (ministral installed) |
| MiniMax | M2.5 / M2.7 | Ollama Cloud (installed), OpenAI-compatible SDK |
| Moonshot | Kimi K2.6 | OpenAI-compatible SDK |
| Zhipu AI | GLM-5.1 / GLM-5 | Ollama Cloud (installed) |
| Meta | Llama 4 Scout / Maverick | Ollama (pullable), HF transformers, Bedrock |
| Alibaba | Qwen 3.6 Plus, Qwen3-Coder-Next | Ollama Cloud (installed) + local Qwen family |
| Cohere, AI21, Together, Fireworks, Perplexity (API), OpenRouter, Groq | Various | OpenAI-compatible SDK / HTTP (no key verified — see PART J) |

---

# PART B — AGGREGATOR / GATEWAY CHANNELS (each fans out to many providers)

| Channel | Status on PC | Providers/models it exposes |
|---------|--------------|-----------------------------|
| **GitHub Models** (via Microsoft AI Toolkit `.aitk`) | CONFIGURED | Catalog incl. gpt-5, gpt-4.1, DeepSeek-R1 (configured), plus Llama, Mistral, Phi, etc. |
| **Amazon Bedrock** (via AWS Kiro IDE) | INSTALLED | Claude Sonnet 4.5 (Kiro primary) + Claude family + open-weight models; all Kiro inference routes through Bedrock. |
| **Microsoft AI Foundry / Azure AI** (VS Code ext teamsdevapp.vscode-ai-foundry 1.2.4, ms-azuretools.vscode-azure-mcp-server) | INSTALLED | Azure OpenAI + Foundry model catalog (OpenAI, Llama, Mistral, Phi, etc.). `.azure` present, no creds verified. |
| **Ollama Cloud** (relay) | CONFIGURED | glm-5.1, glm-5 (Zhipu), minimax-m2.7 (MiniMax), qwen3-coder-next (Alibaba) — see PART C.2. |
| **Hugging Face Hub** (Python `transformers` 4.51.3, `sentence-transformers` 5.2.2) | AVAILABLE | 400k+ open models, hardware-limited. No hub cache populated yet. |
| **OpenClaw / Hermes** (any OpenAI-compatible endpoint) | INSTALLED | Bridge to any provider in PART A once keyed. |

---

# PART C — LOCAL MODELS (on-disk, runtime-verified via `ollama list`)

## C.1 Ollama local models — 24 with weights (server v0.30.5, localhost:11434)
General/chat: qwen3.5:9b, qwen3.5:4b, qwen3.5:2b, qwen3.5:0.8b, qwen3:8b, qwen3:4b, gemma4:e4b, gemma4:e2b, deepseek-r1:8b, lfm2.5-thinking:1.2b, ministral-3:8b, ministral-3:3b, gpt-oss:20b, qwen2.5:0.5b, nuextract.
Coding: qwen2.5-coder:7b, qwen2.5-coder:3b, deepseek-coder-v2.
Vision/OCR: qwen3-vl:2b, qwen2.5vl:3b, glm-ocr.
Embeddings: qwen3-embedding:4b, qwen3-embedding:0.6b, nomic-embed-text.

## C.2 Ollama Cloud models — 4 (remote inference, billed via Ollama Cloud)
glm-5.1:cloud (Zhipu), glm-5:cloud (Zhipu), minimax-m2.7:cloud (MiniMax), qwen3-coder-next:cloud (Alibaba).

## C.3 Other local-model surfaces
| Surface | Status | Notes |
|---------|--------|-------|
| LM Studio 0.3.32 | INSTALLED, no models downloaded | OpenAI-compatible server on :1234 when running. Redundant with Ollama. |
| Microsoft Foundry Local (`.aitk/models/foundry.modelinfo.json`) | CATALOG PRESENT | Local ONNX/GGUF model catalog via AI Toolkit. |
| EasyOCR (`.EasyOCR`) | INSTALLED, no models pulled | Downloads its own detection/recognition models on first use. |
| HF `transformers` / `torch` 2.12.0 | AVAILABLE | Direct local inference of any compatible HF model. |

---

# PART D — INSTALLED AI SURFACES, AGENTS & IDEs (runtime-verified versions)

## D.1 CLIs
| Tool | Version | Path | Models |
|------|---------|------|--------|
| Claude Code CLI | 2.1.71 | ~/.local/bin/claude.exe | Claude all |
| OpenAI Codex CLI | 0.128.0 | npm | GPT-5.x / Codex / o-series |
| Google Gemini CLI | 0.4.1 | npm | Gemini all |
| GitHub Copilot CLI | 1.0.12 | npm | GPT-4o/5, Claude via Copilot |
| OpenClaw | 2026.4.14 | npm | Any OpenAI-compatible |
| Hermes Agent | 0.15.2 | Python (OpenAI SDK 2.24.0) | Any OpenAI-compatible |
| Ollama | 0.30.5 | local | All local + cloud models |

## D.2 AI IDEs / desktop apps  (★ = newly found this scan, absent from prior roster)
| App | Status | Models |
|-----|--------|--------|
| Claude Desktop / Cowork | ACTIVE (this session) | Claude all; 100+ skills; connectors |
| ★ Google Antigravity | INSTALLED (`%LOCALAPPDATA%\Programs\Antigravity`, `.antigravity`) | Gemini 3.1 Pro (default), Gemini 3.5 Flash, Gemini 3 Flash, Claude Sonnet 4.6, Claude Opus 4.6, GPT-OSS-120B |
| ★ AWS Kiro | INSTALLED (`%LOCALAPPDATA%\Programs\Kiro`, `.kiro`) | Amazon Bedrock — Claude Sonnet 4.5 primary + Claude family + open-weight |
| ★ Microsoft Copilot (desktop app) | INSTALLED v148.0.3967.96 | OpenAI GPT (Microsoft Copilot stack) |
| LM Studio | INSTALLED 0.3.32 (not running) | Local GGUF models |

## D.3 VS Code AI extensions (71 ext total; AI-relevant verified)
anthropic.claude-code 2.1.165 · saoudrizwan.claude-dev (Cline) 3.88.0 · ★ continue.continue 1.2.22 · google.geminicodeassist 2.85.0 · google.gemini-cli-vscode-ide-companion 0.20.0 · openai.chatgpt 26.602.40724 · ms-azuretools.vscode-azure-github-copilot 1.0.209 · ms-azuretools.vscode-azure-mcp-server 2.0.43 · ms-vscode.vscode-copilot-vision 0.1.1 · ms-vscode.vscode-websearchforcopilot 0.1.4 · ms-windows-ai-studio.windows-ai-studio 1.4.2 (+ microsoft-ai-tools-pack 0.1.0) · teamsdevapp.vscode-ai-foundry 1.2.4.
Note: the **core github.copilot / github.copilot-chat extension is NOT installed** — Copilot is present via CLI + the Azure-Copilot, vision, and web-search helper extensions only.

## D.4 Python AI SDKs (all production)
anthropic 0.74.0 · openai 1.65.5 · ollama 0.6.1 · langchain 0.3.20 / langchain-core 0.3.45 · mcp 1.27.0 · torch 2.12.0 · transformers 4.51.3 · sentence-transformers 5.2.2 · safetensors 0.5.3. OpenAI SDK 2.24.0 also bundled with Hermes.

---

# PART E — BROWSER / WEB AI (maximal scope; reachable via paired Chrome)
Claude in Chrome extension is paired, and `allowAllBrowserActions` is enabled, so any web AI is reachable: **ChatGPT (chatgpt.com), Claude.ai, Google Gemini (gemini.google.com / AI Studio), Microsoft Copilot (copilot.microsoft.com), Perplexity, Grok (grok.com / X), DeepSeek, Mistral Le Chat, Poe**, and any other browser-based model UI. These are usage-gated by each site's own login, not by anything installed locally.

---

# PART F — PRODUCTIVITY / CREATIVE-SUITE AI (maximal scope; installed apps)
| App | AI feature | Status |
|-----|-----------|--------|
| Microsoft Copilot (Windows + M365 + Edge) | OpenAI-backed assistant | Desktop app installed v148; M365/Edge Copilot reachable |
| Adobe Acrobat 26.001 | AI Assistant (PDF Q&A/summarize) | Installed |
| Adobe Creative Cloud 6.9 + Audition 2025 | Firefly generative AI / generative audio | Installed |
| Canva (desktop `%LOCALAPPDATA%\Programs\Canva` + MCP) | Magic Studio generative AI | Installed + live MCP connector |

---

# PART G — LIVE COWORK MCP CONNECTORS WITH AI (this session)
Connected connectors that embed their own AI services: **Canva** (Magic Studio / generative design + structured generation), **WordPress.com / Jetpack** (Jetpack AI content authoring + Jetpack Search voice), **QuickBooks Online / Intuit** (Intuit Assist financial AI). Plus the Claude model serving this Cowork session itself (claude-opus-4-8 class, with auto-fallback enabled). Claude Desktop `mcpServers` block on the host is empty `{}` (no host-level MCP bridges configured).

---

# PART H — WHAT THE LIVE SCAN ADDED vs THE 2026-06-04 ROSTER
1. **Google Antigravity IDE** (Gemini + Claude + GPT-OSS) — entirely missing before.
2. **AWS Kiro IDE / Amazon Bedrock channel** (Claude + open-weight) — missing.
3. **Microsoft Copilot desktop app** v148 — missing.
4. **Continue.dev** VS Code extension — missing (roster only had Cline).
5. **GitHub Models** channel via Microsoft AI Toolkit (gpt-5, gpt-4.1, DeepSeek-R1 configured) — missing.
6. **Microsoft AI Foundry / Azure AI** + Azure-Copilot + Azure MCP server extensions — partial before.
7. **xAI Grok Build 0.1 + Grok Imagine** models — roster had only Grok 4.3.
8. **OpenAI GPT-5.x-Codex** model family + **Gemini Nano** on-device tier — newly noted.
9. **EasyOCR** and **Foundry Local** local-model surfaces — missing.
10. Version bumps: Cline 3.86→3.88, Gemini Code Assist 2.84→2.85, ChatGPT ext, Windows AI Studio 1.4.0→1.4.2, Node 24.5.0.
11. Correction: core github.copilot VS Code extension is **not** installed (roster implied it was).
12. Machine identity corrected to **Dell Inspiron 7706**, not MSI Vector GP66.

Note: home dot-folders `.dynamo-agentic-ide`, `.studio`, `.ami`, `.mla`, `.otk`, `.agents`, `.orchestrator`, `.ai-resource-governor`, `.example*`, `.inbox-doer` are **Matt's own custom orchestration/agentic infrastructure** (committee metrics, approval queues, a learning.db, autopilot state), not third-party AI providers, so they are recorded here but not counted as providers/models.

---

# PART J — AUTH & LIMITS (runtime-verified 2026-06-06)

Each CLI/tool was exercised or its auth state read live. Results:

| Tool | Auth method | Verified state | Default model | Limits / billing model |
|------|------------|----------------|---------------|------------------------|
| Claude Desktop/Cowork + Claude Code CLI | Anthropic account (`~/.claude/.credentials.json` + `~/.claude.json`) | **WORKING** (this session is live) | Opus/Sonnet/Haiku (auto) | Anthropic plan; **shared usage pool across all Claude surfaces** (CLI+Cowork+Excel+ext compete). |
| OpenAI Codex CLI | ChatGPT OAuth (`auth_mode=chatgpt`; `codex login status` → "Logged in using ChatGPT") | **WORKING** (auth confirmed) | gpt-5.5, reasoning=high | **ChatGPT plan rate limits** (not per-token API billing). No standalone OpenAI API key present. |
| GitHub Copilot CLI | GitHub account | **WORKING** (returned "OK") | **claude-haiku-4.5** (model-switchable via `--model`) | **GitHub Copilot "Premium requests"** monthly quota (that test call = 0.33 premium req). Allotment depends on plan tier (Free/Pro/Pro+/Business). |
| Google Gemini CLI | Google OAuth (`oauth-personal`) | **RE-AUTHED & WORKING** (2026-06-06): token valid w/ refresh token, creds updated 21:33, TLS handshake to googleapis now succeeds — prior root-CA error resolved | gemini (3.x) | Free Code Assist personal tier. Note: `gemini -p` non-interactive can hang under IDE-companion mode (`ideMode:true`); auth itself is confirmed good. |
| Ollama (local) | none needed | **RUNNING** (localhost:11434) | per call | Hardware-bound only; free. |
| Ollama Cloud | **API key set** as user env var `OLLAMA_API_KEY` (2026-06-06) + device key | **WORKING & TRACKED** — verified via `/api/chat` and `/v1/chat/completions` (glm-5.1:cloud) | glm-5.1/glm-5/minimax-m2.7/qwen3-coder-next:cloud | Ollama Cloud quota/billing; **per-call usage now logged locally** by the tracker proxy (see below). |
| OpenClaw | `openclaw.json` → only **local Ollama** provider, no cloud keys | Configured (local-only) | local Ollama | Local only until a cloud key is added. |
| Hermes Agent | `active_provider=openai-codex`; runtime model points to **local Ollama** (`127.0.0.1:11434`, qwen2.5:0.5b) | Configured | qwen2.5:0.5b (local) + openai-codex | Inherits Codex/ChatGPT for cloud, free for local. |
| Microsoft AI Toolkit / GitHub Models | GitHub + **encrypted** Anthropic & Google keys stored by the tool | Configured | gpt-5 / gpt-4.1 / DeepSeek-R1 (GitHub), claude-sonnet-4, gemini-2.5-flash | GitHub Models free quota + the stored provider keys. |
| AWS Kiro → Amazon Bedrock | Kiro's own sign-in (NO user `~/.aws/credentials` or `config` on disk) | Installed; account login not determinable from disk | Claude Sonnet 4.5 (Bedrock) | Kiro/Bedrock plan; not using local AWS creds. |
| Google Antigravity | shares Google auth (`~/.gemini/antigravity*`) | Installed; login not determinable from disk (may share Gemini's stale token) | Gemini 3.1 Pro | **Compute-budget rate limits refreshing ~every 5h** (free agent platform). |
| Microsoft AI Foundry / Azure AI | none — `~/.azure` empty, no creds | Installed, **not credentialed** | n/a | Unusable until Azure sign-in configured. |

**Key takeaways:** 3 of the cloud CLIs run on **subscription/plan quotas, not per-token API keys** — Codex (ChatGPT plan), Copilot (premium requests), Claude (Anthropic shared pool). Gemini CLI was re-authed and now works. **Ollama Cloud is now keyed and usage-tracked** (see below). No standalone third-party API keys (xAI/OpenRouter/Groq/Together/Cohere/Perplexity) are configured anywhere on disk; those providers remain reachable only if a key is added. AI Toolkit holds **encrypted** Anthropic + Google keys — **no secret values were read or recorded in this document.**

## Ollama Cloud tracking (set up 2026-06-06)
A local logging proxy records every Ollama Cloud call (tokens + duration) to CSV + SQLite.
- **Location:** `C:\Users\Couch\.ollama\cloud-tracker\` (`ollama_cloud_tracker.py`, `cloud-chat.py`, `usage-report.py`, `usage.sqlite`, `usage.csv`, `SETUP_README.md`).
- **Endpoints:** OpenAI-compatible `http://127.0.0.1:11435/v1`, native `http://127.0.0.1:11435/api`. Proxy injects the key, so downstream tools need none.
- **Autostart:** `Startup\OllamaCloudTracker.vbs` (runs hidden at logon).
- **Surfaces wired:** Python SDK (live-verified, logged), direct CLI via `cloud-chat.py` (live-verified, logged), OpenClaw and Hermes (config-verified, see below). Native `ollama run <m>:cloud` works but is tracked only on the ollama.com dashboard, not locally.
- **OpenClaw default = cloud (2026-06-06):** `openclaw models set glm-5.1:cloud` → `agents.defaults.model.primary = openai/glm-5.1:cloud`. `glm-5.1:cloud` is declared only by the `ollama-cloud` provider (baseUrl 11435), so it resolves through the tracker. Config-verified; a live OpenClaw turn could not be captured here because OpenClaw's CLI inference startup exceeds the harness timeout (>120s) — it will log when run normally.
- **Hermes fallback = cloud (2026-06-06):** `hermes fallback list` confirms chain entry `glm-5.1:cloud (via custom) [http://127.0.0.1:11435/v1]`; primary stays local `qwen2.5:0.5b`. Stored in `~/.hermes/config.yaml` `fallback_providers` (required keys: `provider` + `model`; `name` is ignored — that was the earlier failed attempt). Fires on primary rate-limit/5xx/connection failure; routes through the tracker when it does. `hermes doctor` = config v24 OK.
- **Report:** `python C:\Users\Couch\.ollama\cloud-tracker\usage-report.py`.

## Still LOW confidence (could not be resolved from disk)
| Item | How to confirm |
|------|----------------|
| Antigravity / Kiro signed-in account & remaining quota | Launch each app; check account panel (auth not stored in readable dotfiles). |
| Exact GitHub Copilot plan tier & remaining premium requests | github.com/settings/copilot or `gh` API. |
| Gemini 3.5 Pro GA status | Re-check ai.google.dev/models. |
| Ollama Cloud account quota | `ollama` account page / run a `:cloud` model. |

---

# CONFIDENCE SUMMARY
- **HIGH:** Machine identity; all installed apps/CLIs/extensions/versions; Ollama model list; Anthropic/OpenAI/Google current lineups (installed + web-verified); **auth state of Claude (working), Codex (working, ChatGPT plan), Copilot (working, premium-request billing), Gemini (configured but TLS-blocked), and that no third-party API keys are stored on disk** (all runtime-verified 2026-06-06, PART J).
- **MEDIUM:** xAI Grok lineup; secondary cloud providers reachable-but-not-keyed; aggregator catalogs (exact catalog contents vary).
- **LOW:** Antigravity/Kiro/Ollama-Cloud signed-in account + remaining quota (not stored in readable dotfiles); exact Copilot plan tier; Gemini 3.5 Pro GA.

# SOURCES
- Live PowerShell runtime scan of DESKTOP-LOOCRQ2, 2026-06-06 (Desktop Commander).
- [Anthropic Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [OpenAI API models](https://developers.openai.com/api/docs/models/all) · [GPT-5.5](https://openai.com/index/introducing-gpt-5-5/)
- [Gemini API models](https://ai.google.dev/gemini-api/docs/models) · [Gemini changelog](https://ai.google.dev/gemini-api/docs/changelog)
- [xAI Docs — Models](https://docs.x.ai/developers/models)
- [Google Antigravity (Wikipedia)](https://en.wikipedia.org/wiki/Google_Antigravity) · [Kiro vs Antigravity model support](https://www.augmentcode.com/tools/kiro-vs-antigravity)

# DOCUMENT METADATA
| Field | Value |
|-------|-------|
| Generated | 2026-06-06 (Cowork session, runtime-verified; incl. live auth/limits pass + Gemini re-auth verify + Ollama Cloud key/tracking setup — PART J) |
| Machine | Dell Inspiron 7706 2n1 (DESKTOP-LOOCRQ2) |
| Predecessors (retired this session) | DELL_AI_MODEL_ROSTER_2026-06-04.md → archive/ ; MSI_AI_ROSTER_2026-06-04.md → archive/ |
| Canonical location | Home of Claude - MSI Auto Project/AI_PROVIDER_MODEL_ROSTER_2026-06-06.md |
