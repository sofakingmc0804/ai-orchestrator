# Dell AI Ecosystem Roster (PRELIMINARY DRAFT - SUPERSEDED)
## Date: 2026-06-04
## Machine: DESKTOP-LOOCRQ2 (Dell Inspiron 7706 2n1, Serial: JMCH3B3)
## Status: SUPERSEDED - See comprehensive roster for complete version
## NOTE: "MSI" naming was incorrect. This machine is a Dell Inspiron 7706 2-in-1.

---

# HARDWARE CONSTRAINTS

| Resource | Value | Impact |
|----------|-------|--------|
| CPU | Intel i7-1165G7, 4 cores / 8 threads, 2.80 GHz | Adequate for inference routing, limited parallelism |
| RAM | 64 GB total, ~37 GB free | Ample for multi-tool operation and CPU-based model inference |
| GPU | NVIDIA GeForce MX350, 2048 MiB VRAM (1905 MiB free) | Severely limited. Only sub-2B parameter models fit in VRAM. Most inference falls back to CPU. |
| Driver | NVIDIA 576.83 | Current |
| OS | Windows (PowerShell 5.1 + 7.x) | Full compatibility |

**GPU constraint is the dominant limitation.** The MX350 is a mobile GPU with 2 GB VRAM. Models above ~2B parameters will run on CPU (64 GB RAM makes this viable but slow). Cloud inference is the primary path for complex work.

---

# SECTION 1: CLOUD AI SERVICES

These are API-accessible services available from MSI via network. Ratings are on a 1-10 scale based on published benchmarks (SWE-bench Verified, GPQA Diamond, AIME 2025, Creative Writing v3, Chatbot Arena ELO) cross-referenced across multiple sources.

## 1.1 Anthropic Claude

| Model | API String | Context | Input $/MTok | Output $/MTok | Coding | Reasoning | Writing | Agentic | Speed | Overall |
|-------|-----------|---------|-------------|--------------|--------|-----------|---------|---------|-------|---------|
| Claude Opus 4.8 | claude-opus-4-8 | 1M | $5.00 | $25.00 | 10 | 9 | 10 | 10 | 5 | 9.5 |
| Claude Opus 4.7 | claude-opus-4-7 | 1M | $5.00 | $25.00 | 10 | 9 | 10 | 10 | 5 | 9.3 |
| Claude Sonnet 4.6 | claude-sonnet-4-6 | 1M | $3.00 | $15.00 | 9 | 8 | 9 | 9 | 7 | 9.0 |
| Claude Haiku 4.5 | claude-haiku-4-5 | 200K | $1.00 | $5.00 | 7 | 6 | 7 | 7 | 9 | 7.0 |

**Benchmark anchors:** Opus 4.8 (released May 28, 2026) achieves 88.6% SWE-bench Verified and 69.2% SWE-bench Pro (frontier). Opus 4.7 (April 16, 2026) scored 87.6%. Sonnet 4.6 at ~79.6% SWE-bench, within 1.2 points of Opus on many tasks at 40% lower cost. All support extended thinking, computer use, and MCP tool use.

**Strengths:** Best agentic coding model (Opus), best natural prose quality, strongest safety alignment, deep tool-use integration via MCP. Sonnet is the best price-to-performance in the mid-tier.

**Weaknesses:** Opus is expensive for bulk work. Shared usage pool across all Claude surfaces means heavy CLI + Cowork + Excel use competes for tokens.

**MSI access:** Claude Desktop/Cowork (v2.1.71 CLI), Chrome extension, Excel add-in, VS Code extension, scheduled tasks. All surfaces active.

## 1.2 OpenAI

| Model | API String | Context | Input $/MTok | Output $/MTok | Coding | Reasoning | Writing | Agentic | Speed | Overall |
|-------|-----------|---------|-------------|--------------|--------|-----------|---------|---------|-------|---------|
| GPT-5.5 | gpt-5.5 | 1M | $5.00 | $30.00 | 8 | 9 | 10 | 8 | 6 | 8.5 |
| GPT-5.4 | gpt-5.4 | 1M | $2.50 | $15.00 | 8 | 8 | 9 | 8 | 7 | 8.0 |
| GPT-5.4 mini | gpt-5.4-mini | 1M | $0.40 | $2.50 | 6 | 6 | 7 | 6 | 9 | 6.5 |
| GPT-5.4 nano | gpt-5.4-nano | 1M | $0.20 | $1.25 | 4 | 4 | 5 | 4 | 10 | 4.5 |
| o3 | o3 | 200K | $2.00 | $8.00 | 8 | 10 | 6 | 7 | 4 | 8.0 |
| o4-mini | o4-mini | 200K | $1.10 | $4.40 | 7 | 9 | 5 | 6 | 6 | 7.0 |

**Benchmark anchors:** GPT-5.5 leads creative writing (Creative Writing v3 benchmark). o3 replaced o1 with 87% price cut and improved reasoning. GPT-5.4 is the recommended production workhorse.

**Strengths:** Best creative writing (GPT-5.5), strongest reasoning chain (o3), widest model tier spread (nano to flagship). Codex CLI provides strong agentic coding.

**Weaknesses:** Output pricing is 6x input on flagship (vs Anthropic's 5x). o-series reasoning models trade speed for accuracy.

**MSI access:** Codex CLI v0.128.0, VS Code ChatGPT extension v26.519.32039. OpenAI Python SDK v1.65.5.

## 1.3 Google Gemini

| Model | API String | Context | Input $/MTok | Output $/MTok | Coding | Reasoning | Writing | Agentic | Speed | Overall |
|-------|-----------|---------|-------------|--------------|--------|-----------|---------|---------|-------|---------|
| Gemini 3.1 Pro | gemini-3.1-pro | 1M | $2.00 | $12.00 | 9 | 10 | 7 | 8 | 6 | 8.5 |
| Gemini 3.5 Flash | gemini-3.5-flash | 1M | $1.50 | $9.00 | 9 | 8 | 7 | 8 | 8 | 8.5 |
| Gemini 2.5 Pro | gemini-2.5-pro | 1M | $1.25 | $10.00 | 8 | 9 | 7 | 7 | 7 | 8.0 |
| Gemini 2.5 Flash | gemini-2.5-flash | 1M | $0.15 | $0.60 | 7 | 7 | 6 | 6 | 9 | 7.0 |
| Gemini 3.1 Flash-Lite | gemini-3.1-flash-lite | 1M | $0.25 | $1.50 | 6 | 6 | 5 | 5 | 10 | 6.0 |

**Benchmark anchors:** Gemini 3.1 Pro leads GPQA Diamond at 94.3% and ARC-AGI-2 at 77.1%. Gemini 3.5 Flash beats 3.1 Pro on coding at 25% lower cost. Gemini 2.5 Flash is the best value in the entire market at $0.15/$0.60.

**Strengths:** Best reasoning benchmarks (3.1 Pro), best price-to-performance ratio (2.5 Flash), native multimodal (text/image/audio/video), free tier available, 1M context across all models.

**Weaknesses:** Prose quality trails Claude and GPT. Agentic tool-use ecosystem less mature than Anthropic's MCP.

**MSI access:** Gemini CLI v0.4.1, VS Code Gemini Code Assist v2.84.0, Gemini CLI IDE Companion v0.20.0.

## 1.4 xAI Grok

| Model | API String | Context | Input $/MTok | Output $/MTok | Coding | Reasoning | Writing | Agentic | Speed | Overall |
|-------|-----------|---------|-------------|--------------|--------|-----------|---------|---------|-------|---------|
| Grok 4.3 | grok-4.3 | 256K | ~$3.00 | ~$15.00 | 9 | 9 | 7 | 7 | 6 | 8.0 |

**Benchmark anchors:** Grok 4.3 leads alongside Opus on coding benchmarks. Strong reasoning.

**Strengths:** Competitive coding and reasoning. Real-time data access via X/Twitter integration.

**Weaknesses:** Smaller ecosystem, less mature tooling, no established agent framework on MSI.

**MSI access:** No dedicated CLI or extension installed. API-only via HTTP.

---

# SECTION 2: AGENT FRAMEWORKS ON MSI

Frameworks that provide agentic capabilities (autonomous task execution, tool use, multi-step reasoning).

## 2.1 Claude Code CLI

| Attribute | Detail |
|-----------|--------|
| Version | 2.1.71 |
| Provider | Anthropic |
| Models | Opus 4.7, Sonnet 4.6, Haiku 4.5 (auto-selects) |
| MCP Support | Client + Server (can expose itself as MCP server) |
| Agentic Rating | 10/10 |
| Tool Use | Bash, file R/W, Git, sub-agents, background agents, hooks |
| Scheduling | Via /schedule command (creates Cowork scheduled tasks) |
| Key Capability | Deepest integration with Anthropic ecosystem. Sub-agents, worktrees, isolation. Industry-leading agentic coding. |
| Limitation | Shares usage pool with all Claude surfaces |

## 2.2 Claude Desktop / Cowork

| Attribute | Detail |
|-----------|--------|
| Version | Current (Electron app, auto-updates) |
| Provider | Anthropic |
| Models | Opus 4.7, Sonnet 4.6, Haiku 4.5 |
| MCP Support | Client only |
| Agentic Rating | 9/10 |
| Connectors | Gmail, Calendar, Drive, QuickBooks, Canva, WordPress |
| Scheduling | Cron-based scheduled tasks, 3 concurrent max |
| Key Capability | Broadest connector ecosystem. Scheduled background automation. Plugin/skill system with 100+ skills. Chrome extension for browser control. |
| Limitation | VM is ephemeral per session. Machine must be awake for scheduled tasks. |

## 2.3 OpenAI Codex CLI

| Attribute | Detail |
|-----------|--------|
| Version | 0.128.0 |
| Provider | OpenAI |
| Models | GPT-5.5, GPT-5.4, o3, o4-mini |
| MCP Support | Client + Server (codex mcp-server) |
| Agentic Rating | 8/10 |
| Tool Use | Sandboxed execution, file R/W, approval modes |
| Key Capability | Can serve as MCP server for Claude Code to invoke. Independent AI backend for cross-provider delegation. |
| Limitation | Separate auth/config from Claude. No direct integration except via MCP bridge. |

## 2.4 OpenClaw

| Attribute | Detail |
|-----------|--------|
| Version | 2026.4.14 |
| Provider | OpenClaw (open source) |
| Models | Any OpenAI-compatible API (Ollama, OpenAI, Anthropic, local) |
| MCP Support | Yes (openclaw mcp command) |
| Agentic Rating | 8/10 |
| Tool Use | Browser (CDP), terminal, file system, messaging (30+ channels), cron, sessions, memory |
| Key Capability | Persistent headless daemon. Multi-channel messaging (WhatsApp, Telegram, Slack, Discord, etc.). Agent Control Protocol. Skills/plugins system. Live Canvas UI. |
| Limitation | Requires separate configuration. Gateway service must be running. |
| Status on MSI | INSTALLED, not configured (no gateway running) |

## 2.5 Hermes Agent

| Attribute | Detail |
|-----------|--------|
| Version | N/A |
| Provider | Nous Research (open source) |
| Models | Any OpenAI-compatible API (Ollama, OpenAI, Anthropic) |
| MCP Support | Unknown |
| Agentic Rating | 7/10 (based on published reviews) |
| Key Capability | Self-improving learning loop (converts solved workflows into reusable skills). Persistent memory. Multi-platform deployment (Telegram, Discord, WhatsApp). |
| Limitation | N/A |
| Status on MSI | **NOT INSTALLED** (not found in npm, pip, or filesystem) |

## 2.6 Google Gemini CLI

| Attribute | Detail |
|-----------|--------|
| Version | 0.4.1 |
| Provider | Google |
| Models | Gemini 3.1 Pro, 3.5 Flash, 2.5 Pro, 2.5 Flash |
| MCP Support | Client (uses MCP servers) |
| Agentic Rating | 7/10 |
| Tool Use | File system, shell, code execution |
| Key Capability | Free tier for experimentation. Strong reasoning models. Multimodal input. |
| Limitation | Newer/less mature agent framework than Claude Code or Codex |

## 2.7 GitHub Copilot

| Attribute | Detail |
|-----------|--------|
| Version | CLI v1.0.12, VS Code extensions installed |
| Provider | GitHub/Microsoft |
| Models | GPT-4o, Claude Sonnet (via Copilot), internal models |
| MCP Support | Yes (in VS Code Agent mode via mcp.json) |
| Agentic Rating | 6/10 |
| Key Capability | Inline code completion, chat, agent mode in VS Code. MCP server access. |
| Limitation | Separate auth (GitHub). Cannot be invoked by Claude. Parallel system, not subordinate. |

## 2.8 Cline (Claude Dev)

| Attribute | Detail |
|-----------|--------|
| Version | VS Code extension v3.86.0 |
| Provider | Community (open source) |
| Models | Configurable (Claude, OpenAI, local) |
| Agentic Rating | 7/10 |
| Key Capability | Autonomous coding agent in VS Code. Can use any model provider. Plan-and-execute workflow. |
| Limitation | VS Code only. Overlaps with Claude Code extension. |

---

# SECTION 3: LOCAL MODEL SERVERS

## 3.1 Ollama

| Attribute | Detail |
|-----------|--------|
| Version | 0.30.5 |
| Status | Running (server active on localhost:11434) |
| Models Installed | 28 (see Section 4) |
| Cloud Models | 4 (via Ollama Cloud relay) |
| Capabilities | Completion, embedding, vision, tool use, thinking (model-dependent) |
| Key Capability | Unified local + cloud model interface. OpenAI-compatible API. Can serve as backend for OpenClaw, Hermes, any OpenAI-compatible client. |
| Limitation | MX350 GPU limits to ~2B params on GPU. Larger models run on CPU (viable with 64GB RAM but 5-20x slower). |

## 3.2 LM Studio

| Attribute | Detail |
|-----------|--------|
| Version | 0.3.32 |
| Status | INSTALLED, NOT RUNNING |
| API | OpenAI-compatible on port 1234 (when running) |
| Key Capability | GUI for model management. Alternative to Ollama for local inference. |
| Limitation | Not currently active. Redundant with Ollama for most use cases. |

---

# SECTION 4: LOCAL MODELS (Ollama Inventory)

28 models installed. Rated for MSI-specific viability given the MX350 GPU constraint.

## 4.1 Completion / Chat Models

| Model | Params | Quant | Size | Context | Capabilities | GPU-viable | Coding | Reasoning | Speed (CPU) | Best Use |
|-------|--------|-------|------|---------|-------------|-----------|--------|-----------|-------------|----------|
| qwen3.5:0.8b | 873M | Q8_0 | 1.0 GB | 262K | vision, completion, tools, thinking | Yes | 5 | 4 | 10 | Fast triage, classification, routing |
| qwen3.5:2b | 2.3B | Q8_0 | 2.7 GB | 262K | vision, completion, tools, thinking | Marginal | 6 | 5 | 8 | Light reasoning with vision |
| qwen3.5:4b | 4.7B | Q4_K_M | 3.4 GB | 262K | vision, completion, tools, thinking | No (CPU) | 7 | 6 | 6 | Mid-tier local reasoning |
| qwen3.5:9b | 9.7B | Q4_K_M | 6.6 GB | 262K | vision, completion, tools, thinking | No (CPU) | 8 | 7 | 4 | Best local all-rounder |
| qwen3:4b | 4.0B | Q4_K_M | 2.5 GB | 262K | completion, tools, thinking | No (CPU) | 6 | 6 | 7 | Lightweight tool calling |
| qwen3:8b | 8.2B | Q4_K_M | 5.2 GB | 40K | completion, tools, thinking | No (CPU) | 7 | 7 | 5 | Prior-gen local workhorse |
| gemma4:e2b | 5.1B | Q4_K_M | 7.2 GB | - | completion, tools, thinking | No (CPU) | 7 | 7 | 4 | Google-quality local reasoning |
| gemma4:e4b | 8.0B | Q4_K_M | 9.6 GB | - | completion, tools, thinking | No (CPU) | 7 | 7 | 3 | Heavier local reasoning |
| deepseek-r1:8b | 8.2B | Q4_K_M | 5.2 GB | 131K | completion, thinking | No (CPU) | 7 | 8 | 4 | Chain-of-thought reasoning |
| lfm2.5-thinking:1.2b | 1.2B | Q4_K_M | 731 MB | 128K | completion, tools, thinking | Yes | 4 | 5 | 9 | Ultra-fast local thinking |
| ministral-3:3b | 3.8B | Q4_K_M | 3.0 GB | 262K | vision, completion, tools | No (CPU) | 5 | 5 | 6 | Mistral local with vision |
| ministral-3:8b | 8.9B | Q4_K_M | 6.0 GB | 262K | vision, completion, tools | No (CPU) | 6 | 6 | 4 | Larger Mistral local |
| gpt-oss:20b | 20.9B | MXFP4 | 13 GB | 131K | completion, tools, thinking | No (CPU) | 7 | 7 | 2 | Largest local model. Slow on CPU. |
| nuextract | 3.8B | Q4_0 | 2.2 GB | 4K | completion | No (CPU) | 3 | 3 | 6 | Structured data extraction only |
| qwen2.5:0.5b | 494M | Q4_K_M | 397 MB | 32K | completion, tools | Yes | 3 | 3 | 10 | Ultra-fast routing/classification |

## 4.2 Coding-Specific Models

| Model | Params | Quant | Size | Context | Capabilities | GPU-viable | Coding | Speed (CPU) | Best Use |
|-------|--------|-------|------|---------|-------------|-----------|--------|-------------|----------|
| qwen2.5-coder:3b | 3.1B | Q4_K_M | 1.9 GB | 32K | completion, tools, insert | Marginal | 6 | 7 | Fast code completion, fill-in-the-middle |
| qwen2.5-coder:7b | 7.6B | Q4_K_M | 4.7 GB | 32K | completion, tools, insert | No (CPU) | 7 | 5 | Local code generation |
| deepseek-coder-v2 | 15.7B | Q4_0 | 8.9 GB | 163K | completion, insert | No (CPU) | 7 | 3 | Heavy local code work |

## 4.3 Vision / OCR Models

| Model | Params | Quant | Size | Context | Capabilities | GPU-viable | Vision Quality | Speed (CPU) |
|-------|--------|-------|------|---------|-------------|-----------|---------------|-------------|
| qwen3-vl:2b | 2.1B | Q4_K_M | 1.9 GB | 262K | vision, completion, tools, thinking | Marginal | 6 | 7 |
| qwen2.5vl:3b | 3.8B | Q4_K_M | 3.2 GB | 128K | vision, completion | No (CPU) | 6 | 5 |
| glm-ocr | 1.1B | F16 | 2.2 GB | 131K | vision, completion, tools | Yes | 7 (OCR) | 8 |

## 4.4 Embedding Models

| Model | Params | Quant | Size | Context | Dimensions | GPU-viable | Quality |
|-------|--------|-------|------|---------|-----------|-----------|---------|
| qwen3-embedding:4b | 4.0B | Q4_K_M | 2.5 GB | 40K | 2560 | No (CPU) | 8 |
| qwen3-embedding:0.6b | 596M | Q8_0 | 639 MB | 32K | 1024 | Yes | 6 |
| nomic-embed-text | 137M | F16 | 274 MB | 2K | 768 | Yes | 5 |

## 4.5 Ollama Cloud Models (remote inference via Ollama relay)

| Model | Provider | Context | Capabilities | Coding | Reasoning | Cost |
|-------|----------|---------|-------------|--------|-----------|------|
| glm-5.1:cloud | Zhipu AI | 202K | completion, tools, thinking | 8 | 8 | Ollama Cloud pricing |
| glm-5:cloud | Zhipu AI | 202K | completion, tools, thinking | 7 | 7 | Ollama Cloud pricing |
| minimax-m2.7:cloud | MiniMax | 204K | completion, tools, thinking | 8 | 8 | Ollama Cloud pricing |
| qwen3-coder-next:cloud | Alibaba | 262K | completion, tools | 9 | 7 | Ollama Cloud pricing |

---

# SECTION 5: VS CODE AI EXTENSIONS

| Extension | Version | Provider | Function | Agentic | Rating |
|-----------|---------|----------|----------|---------|--------|
| Claude Code | 2.1.156 | Anthropic | Full agent in VS Code, shares CLI state | Yes | 9 |
| Google Gemini Code Assist | 2.84.0 | Google | Code completion, chat, agent mode | Yes | 7 |
| Gemini CLI IDE Companion | 0.20.0 | Google | Gemini CLI integration | No | 5 |
| OpenAI ChatGPT | 26.519.32039 | OpenAI | Chat, code assistance | Limited | 6 |
| Copilot Vision | 0.1.1 | Microsoft | Visual code understanding | No | 5 |
| Copilot Web Search | 0.1.4 | Microsoft | Web-augmented answers | No | 4 |
| Cline (Claude Dev) | 3.86.0 | Community | Autonomous coding agent | Yes | 7 |
| Windows AI Studio | 1.4.0 | Microsoft | Local model management | No | 5 |
| Microsoft AI Foundry | 1.2.4 | Microsoft | Azure AI integration | No | 4 |
| Microsoft AI Tools Pack | 0.1.0 | Microsoft | AI tooling bundle | No | 3 |

---

# SECTION 6: PYTHON AI INFRASTRUCTURE

| Package | Version | Purpose | Maturity |
|---------|---------|---------|----------|
| anthropic | 0.74.0 | Anthropic Claude API SDK | Production |
| openai | 1.65.5 | OpenAI API SDK | Production |
| ollama | 0.6.1 | Ollama Python client | Production |
| langchain | 0.3.20 | LLM orchestration framework | Production |
| langchain-core | 0.3.45 | LangChain core abstractions | Production |
| mcp | 1.27.0 | Model Context Protocol SDK | Production |
| torch | 2.12.0 | PyTorch deep learning | Production |
| transformers | 4.51.3 | Hugging Face model library | Production |
| sentence-transformers | 5.2.2 | Embedding models | Production |
| safetensors | 0.5.3 | Safe model serialization | Production |

---

# SECTION 7: CAPABILITY MATRIX (Best tool for each task)

| Task Category | Primary Tool | Backup Tool | Local Option | Notes |
|--------------|-------------|-------------|--------------|-------|
| Complex coding | Claude Opus 4.7 (via CLI) | Codex CLI (GPT-5.5) | qwen3.5:9b (slow) | Opus leads SWE-bench at ~88% |
| Fast code completion | Claude Sonnet 4.6 | Gemini 3.5 Flash | qwen2.5-coder:7b | Sonnet best price/perf ratio |
| Reasoning / math | Gemini 3.1 Pro | o3 | deepseek-r1:8b | Gemini leads GPQA at 94.3% |
| Creative writing | GPT-5.5 | Claude Opus 4.7 | None competitive | GPT-5.5 leads Creative Writing v3 |
| Document analysis | Claude Opus 4.7 | Gemini 2.5 Pro | nuextract (extraction only) | Opus best for nuanced analysis |
| Image understanding | Claude Sonnet 4.6 | Gemini 3.5 Flash | qwen3-vl:2b, glm-ocr | Cloud models far ahead of local |
| Bulk classification | Gemini 2.5 Flash ($0.15/MTok) | GPT-5.4 nano ($0.20/MTok) | qwen2.5:0.5b, qwen3.5:0.8b | Local models are free but slower |
| Embeddings | OpenAI text-embedding-3 | Gemini embedding | qwen3-embedding:4b, nomic-embed-text | Local embeddings viable for RAG |
| Web browsing agent | Claude Chrome extension | OpenClaw (CDP) | None | Chrome MCP is most mature |
| Email/calendar ops | Claude Cowork (connectors) | None | None | Native Gmail/Calendar connectors |
| Chat channel agents | OpenClaw | Hermes (NOT INSTALLED) | None | OpenClaw supports 30+ channels |
| Scheduled automation | Claude Cowork tasks | OpenClaw cron | None | Cowork has native scheduler |
| Local bulk inference | Ollama + qwen3.5:4b | LM Studio (not running) | All local models | 64GB RAM handles CPU inference |
| Data extraction | nuextract (local) | Claude Haiku 4.5 | nuextract | Specialized extraction model |
| OCR | glm-ocr (local) | Claude Sonnet (vision) | glm-ocr | Good local OCR at 1.1B params |

---

# SECTION 8: INSTALLATION STATUS SUMMARY

| Component | Status | Version | Action Needed |
|-----------|--------|---------|---------------|
| Claude Code CLI | ACTIVE | 2.1.71 | None |
| Claude Desktop/Cowork | ACTIVE | Current | None |
| Claude Chrome Extension | ACTIVE | Paired | None |
| OpenAI Codex CLI | INSTALLED | 0.128.0 | Verify auth |
| OpenClaw | INSTALLED, NOT CONFIGURED | 2026.4.14 | Run `openclaw configure` to set up gateway |
| Hermes Agent | **NOT INSTALLED** | N/A | Requires installation if desired |
| Gemini CLI | INSTALLED | 0.4.1 | Verify auth |
| GitHub Copilot CLI | INSTALLED | 1.0.12 | Verify auth |
| Ollama | RUNNING | 0.30.5 | None (server active) |
| LM Studio | INSTALLED, NOT RUNNING | 0.3.32 | Start if needed (redundant with Ollama) |
| Cline (Claude Dev) | INSTALLED (VS Code) | 3.86.0 | None |

---

# SECTION 9: FRONTIER MODEL COMPARISON (June 2026 Snapshot)

Top-tier models ranked by composite score across published benchmarks. Scores are normalized 1-10.

| Rank | Model | Provider | SWE-bench | GPQA | AIME | Writing | Agentic | Cost ($/MTok out) | Composite |
|------|-------|----------|-----------|------|------|---------|---------|-------------------|-----------|
| 1 | Claude Opus 4.8 | Anthropic | 10 | 9 | 8 | 10 | 10 | $25.00 | 9.5 |
| 2 | Gemini 3.1 Pro | Google | 9 | 10 | 9 | 7 | 8 | $12.00 | 8.5 |
| 3 | GPT-5.5 | OpenAI | 8 | 9 | 8 | 10 | 8 | $30.00 | 8.5 |
| 4 | Grok 4.3 | xAI | 9 | 9 | 8 | 7 | 7 | ~$15.00 | 8.0 |
| 5 | Gemini 3.5 Flash | Google | 9 | 8 | 7 | 7 | 8 | $9.00 | 8.5 |
| 6 | Claude Sonnet 4.6 | Anthropic | 9 | 8 | 7 | 9 | 9 | $15.00 | 9.0 |
| 7 | GPT-5.4 | OpenAI | 8 | 8 | 7 | 9 | 8 | $15.00 | 8.0 |
| 8 | o3 | OpenAI | 8 | 10 | 10 | 6 | 7 | $8.00 | 8.0 |
| 9 | MiniMax M2.5 | MiniMax | 9 | 7 | 6 | 6 | 7 | $1.20 | 7.5 |
| 10 | Claude Haiku 4.5 | Anthropic | 7 | 6 | 5 | 7 | 7 | $5.00 | 7.0 |

**Value tier leaders (best performance per dollar):**
1. Gemini 2.5 Flash at $0.15/$0.60 -- unmatched value
2. GPT-5.4 nano at $0.20/$1.25 -- cheapest from OpenAI
3. Gemini 3.1 Flash-Lite at $0.25/$1.50 -- Google's budget option
4. o4-mini at $1.10/$4.40 -- cheapest reasoning model

---

# SECTION 10: OPEN-SOURCE MODEL LANDSCAPE (Not on MSI, Available via Ollama)

These are frontier open-source models that could be pulled into Ollama but are NOT currently installed. Included for awareness.

| Model | Provider | Params | Key Strength | MSI Viability |
|-------|----------|--------|-------------|---------------|
| Llama 4 Scout | Meta | 109B (17B active MoE) | 10M token context | CPU only. Slow but possible with 64GB RAM. |
| Llama 4 Maverick | Meta | 402B (17B active MoE) | 1M context, beats GPT-4o | Too large for MSI. Cloud only. |
| DeepSeek V4 Pro | DeepSeek | MoE | Best open-weight agentic coding | Cloud only (full model). Distilled versions via Ollama. |
| Qwen 3.6 Plus | Alibaba | 27B | 77.2% SWE-bench Verified | CPU feasible. Would need ollama pull. |
| Kimi K2.6 | Moonshot | MoE | #1 open-weight on Artificial Analysis Index | Cloud only. |
| Mistral Large 3 | Mistral | - | Apache 2.0 licensed | Check Ollama availability. |
| GLM-5 | Zhipu AI | - | 77.8% SWE-bench Verified | Available via Ollama Cloud (already configured). |

---

# SECTION 11: GAPS AND RECOMMENDATIONS

## Confirmed gaps:
1. **Hermes Agent not installed.** If you want it, it needs to be installed. It provides self-learning skill loops that no other framework on MSI currently offers.
2. **OpenClaw not configured.** Installed but gateway never set up. It provides multi-channel messaging agent capability (WhatsApp, Telegram, etc.) that nothing else on MSI does.
3. **No MCP bridges configured.** Claude Desktop `mcpServers` block is empty. No tool-to-tool integration via MCP exists despite having the infrastructure for it.
4. **Codex, Gemini CLI, Copilot auth unverified.** Installed but auth status unknown. May need re-login.
5. **LM Studio dormant.** Installed but not running. Redundant with Ollama unless you need its specific GUI features.
6. **No Ollama MCP server wrapper.** Ollama runs but is not bridged into Claude Code or Cowork via MCP. Local model inference is only accessible via direct API calls.

## Priority actions (if proceeding):
1. Verify auth on Codex, Gemini CLI, Copilot
2. Configure OpenClaw gateway if multi-channel agent capability is desired
3. Install Hermes Agent if self-learning skill loops are desired
4. Set up an Ollama MCP server wrapper so local models are accessible from Claude Code/Cowork
5. Consider pulling qwen3.6:27b via Ollama for a stronger local model (CPU inference, ~16GB RAM, viable on this machine)

---

# DOCUMENT METADATA

- **Generated:** 2026-06-04 by Claude Opus (Cowork session)
- **Verified against:** MSI runtime output (PowerShell commands executed this session)
- **Benchmark sources:** Vellum LLM Leaderboard, llm-stats.com, official provider documentation, AI Magicx, Admix benchmarks
- **Predecessor:** None (net-new document)
- **Canonical location:** Home of Claude - MSI Auto Project/MSI_AI_ROSTER_2026-06-04.md
