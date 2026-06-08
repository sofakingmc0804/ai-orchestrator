# Complete AI Model Roster
## Machine: DESKTOP-LOOCRQ2 (Dell Inspiron 7706 2n1, Serial: JMCH3B3)
## Date: 2026-06-04
## Scope: Every AI model accessible from this machine through any channel

---

# RATING METHODOLOGY

All models rated 1-10 on 12 standardized characteristics. Ratings are derived from published benchmarks (SWE-bench Verified, GPQA Diamond, AIME 2025, ARC-AGI-2, Creative Writing v3, Chatbot Arena ELO, Artificial Analysis Index) cross-referenced across Vellum LLM Leaderboard, llm-stats.com, Artificial Analysis, and official provider announcements. Where benchmark data is unavailable for a specific model, ratings are interpolated from the model family's known performance curve with a -1 confidence penalty.

**Rating scale:**
- 10: Best-in-class, frontier performance
- 8-9: Near-frontier, competitive with best
- 6-7: Strong, production-viable for the task
- 4-5: Adequate for non-critical use
- 2-3: Weak, use only when no alternative
- 1: Not capable of this task

**Characteristics:**
1. **COD** - Coding (SWE-bench Verified, HumanEval, Terminal-Bench)
2. **RSN** - Reasoning (GPQA Diamond, ARC-AGI-2)
3. **MTH** - Math (AIME 2025, MATH-500)
4. **WRT** - Writing quality (Creative Writing v3, Chatbot Arena)
5. **INS** - Instruction following (IFEval, MT-Bench)
6. **VIS** - Vision/multimodal (MMMU, MathVista)
7. **TUL** - Tool use / function calling (BFCL, MCP Atlas)
8. **CTX** - Context handling (RULER, needle-in-haystack at context length)
9. **SPD** - Speed / tokens per second
10. **CST** - Cost efficiency (quality per dollar, inverted: 10 = cheapest for quality)
11. **AGT** - Agentic capability (multi-step planning, self-correction, tool orchestration)
12. **LCL** - Local viability on this Dell (GPU 2GB, RAM 64GB)

---

# HARDWARE CONSTRAINTS (Dell Inspiron 7706 2n1)

| Resource | Value |
|----------|-------|
| CPU | Intel i7-1165G7, 4 cores / 8 threads, 2.80 GHz |
| RAM | 64 GB DDR4 |
| GPU | NVIDIA GeForce MX350, 2048 MiB VRAM, Compute 6.1 |
| GPU 2 | Intel Iris Xe (integrated, not used for inference) |
| Disk | Check available space before pulling large models |
| Network | Required for cloud API models |

**Local model viability tiers:**
- GPU-viable: Models under ~1.5B params quantized (fits in 2GB VRAM)
- CPU-viable-fast: Models under ~4B params (response in seconds)
- CPU-viable-medium: Models 4-10B params (response in 10-30 seconds)
- CPU-viable-slow: Models 10-20B params (response in 30-120 seconds)
- CPU-marginal: Models 20-30B params (response in minutes, 64GB RAM needed)
- Not viable: Models above 30B params (insufficient RAM or impractically slow)

---

# PART 1: CLOUD API MODELS

## 1.1 Anthropic Claude

| Model | API String | Context | $/MTok In | $/MTok Out | COD | RSN | MTH | WRT | INS | VIS | TUL | CTX | SPD | CST | AGT | LCL | Notes |
|-------|-----------|---------|-----------|-----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-------|
| Opus 4.8 | claude-opus-4-8 | 1M | 5.00 | 25.00 | 10 | 9 | 8 | 10 | 10 | 9 | 10 | 9 | 5 | 4 | 10 | - | Latest. 88.6% SWE-bench. Released 2026-05-28. |
| Opus 4.8 Fast | claude-opus-4-8 (fast) | 1M | 10.00 | 50.00 | 10 | 9 | 8 | 10 | 10 | 9 | 10 | 9 | 7 | 2 | 10 | - | 2.5x speed at 2x price. |
| Opus 4.7 | claude-opus-4-7 | 1M | 5.00 | 25.00 | 10 | 9 | 8 | 10 | 10 | 9 | 10 | 9 | 5 | 4 | 10 | - | 87.6% SWE-bench. Released 2026-04-16. |
| Opus 4.6 | claude-opus-4-6 | 1M | 5.00 | 25.00 | 9 | 9 | 8 | 10 | 9 | 8 | 10 | 9 | 5 | 4 | 10 | - | 80.8% SWE-bench. Extended thinking, computer use. |
| Sonnet 4.6 | claude-sonnet-4-6 | 1M | 3.00 | 15.00 | 9 | 8 | 7 | 9 | 9 | 8 | 9 | 9 | 7 | 6 | 9 | - | 79.6% SWE-bench. Best price/perf mid-tier. |
| Haiku 4.5 | claude-haiku-4-5-20251001 | 200K | 1.00 | 5.00 | 7 | 6 | 5 | 7 | 8 | 6 | 7 | 7 | 9 | 8 | 7 | - | Fast, cheap. 200K context. |

**Access paths on Dell:** Claude Desktop/Cowork, Claude Code CLI v2.1.71, VS Code Claude Code ext v2.1.156, Chrome extension, Cline v3.86.0, scheduled tasks. Python SDK v0.74.0.

## 1.2 OpenAI

| Model | API String | Context | $/MTok In | $/MTok Out | COD | RSN | MTH | WRT | INS | VIS | TUL | CTX | SPD | CST | AGT | LCL | Notes |
|-------|-----------|---------|-----------|-----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-------|
| GPT-5.5 | gpt-5.5 | 1M | 5.00 | 30.00 | 8 | 9 | 8 | 10 | 9 | 8 | 8 | 9 | 6 | 3 | 8 | - | Flagship. Best creative writing. |
| GPT-5.5 Pro | gpt-5.5-pro | 1M | 30.00 | 180.00 | 9 | 10 | 9 | 10 | 9 | 8 | 8 | 9 | 3 | 1 | 8 | - | Extended compute. Very expensive. |
| GPT-5.4 | gpt-5.4 | 1M | 2.50 | 15.00 | 8 | 8 | 7 | 9 | 9 | 8 | 8 | 9 | 7 | 6 | 8 | - | Recommended production workhorse. |
| GPT-5.4 mini | gpt-5.4-mini | 1M | 0.40 | 2.50 | 6 | 6 | 5 | 7 | 8 | 7 | 7 | 8 | 9 | 9 | 6 | - | Budget. Good for high-volume. |
| GPT-5.4 nano | gpt-5.4-nano | 1M | 0.20 | 1.25 | 4 | 4 | 3 | 5 | 7 | 5 | 5 | 7 | 10 | 10 | 4 | - | Cheapest OpenAI. Routing/extraction. |
| GPT-5.2 | gpt-5.2 | 1M | 3.00 | 18.00 | 8 | 8 | 7 | 9 | 9 | 8 | 8 | 9 | 6 | 5 | 8 | - | Prior frontier. |
| GPT-5 | gpt-5 | 1M | 3.00 | 15.00 | 7 | 8 | 7 | 8 | 8 | 7 | 7 | 8 | 6 | 5 | 7 | - | Previous gen. |
| GPT-4o | gpt-4o | 128K | 2.50 | 10.00 | 7 | 7 | 6 | 8 | 8 | 8 | 7 | 7 | 7 | 6 | 7 | - | Mature. Still good for vision. |
| GPT-4.1 | gpt-4.1 | 1M | 2.00 | 8.00 | 7 | 7 | 6 | 7 | 9 | 7 | 9 | 9 | 7 | 7 | 7 | - | Best instruction following + tool calling. |
| GPT-4.1 mini | gpt-4.1-mini | 1M | 0.40 | 1.60 | 6 | 6 | 5 | 6 | 8 | 6 | 8 | 8 | 9 | 9 | 6 | - | Budget tool-calling champion. |
| o3 | o3 | 200K | 2.00 | 8.00 | 8 | 10 | 10 | 6 | 7 | 7 | 7 | 7 | 4 | 6 | 7 | - | Reasoning flagship. 87% cheaper than o1. |
| o3-mini | o3-mini | 200K | 1.10 | 4.40 | 7 | 9 | 9 | 5 | 7 | 5 | 6 | 7 | 6 | 7 | 6 | - | Budget reasoning. |
| o4-mini | o4-mini | 200K | 1.10 | 4.40 | 7 | 9 | 9 | 5 | 7 | 7 | 7 | 7 | 6 | 7 | 6 | - | Latest mini reasoning. |

**Access paths on Dell:** Codex CLI v0.128.0, VS Code ChatGPT ext v26.519.32039, Cline v3.86.0, OpenClaw, Hermes Agent. Python SDK v1.65.5.

## 1.3 Google Gemini

| Model | API String | Context | $/MTok In | $/MTok Out | COD | RSN | MTH | WRT | INS | VIS | TUL | CTX | SPD | CST | AGT | LCL | Notes |
|-------|-----------|---------|-----------|-----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-------|
| Gemini 3.5 Pro | gemini-3.5-pro | 1M | TBD | TBD | 9 | 10 | 9 | 7 | 9 | 9 | 9 | 9 | 6 | TBD | 9 | - | Expected June 2026. |
| Gemini 3.5 Flash | gemini-3.5-flash | 1M | 1.50 | 9.00 | 9 | 8 | 7 | 7 | 9 | 9 | 9 | 9 | 8 | 7 | 8 | - | Released 2026-05-19. Beats 3.1 Pro on coding. |
| Gemini 3.1 Pro | gemini-3.1-pro | 1M | 2.00 | 12.00 | 9 | 10 | 9 | 7 | 8 | 9 | 8 | 9 | 6 | 6 | 8 | - | 94.3% GPQA Diamond. Best reasoning. |
| Gemini 3.1 Flash-Lite | gemini-3.1-flash-lite | 1M | 0.25 | 1.50 | 6 | 6 | 5 | 5 | 7 | 6 | 6 | 8 | 10 | 9 | 5 | - | Budget option. |
| Gemini 3 Flash Preview | gemini-3-flash-preview | 1M | 0.50 | 3.00 | 7 | 7 | 6 | 6 | 8 | 8 | 7 | 8 | 8 | 8 | 6 | - | Preview. |
| Gemini 2.5 Pro | gemini-2.5-pro | 1M | 1.25 | 10.00 | 8 | 9 | 8 | 7 | 8 | 8 | 7 | 9 | 7 | 7 | 7 | - | Mature. Good value. |
| Gemini 2.5 Flash | gemini-2.5-flash | 1M | 0.15 | 0.60 | 7 | 7 | 6 | 6 | 7 | 7 | 6 | 8 | 9 | 10 | 6 | - | BEST VALUE IN MARKET. $0.15/MTok input. |
| Gemini 2.5 Flash-Lite | gemini-2.5-flash-lite | 1M | 0.10 | 0.40 | 5 | 5 | 4 | 5 | 6 | 5 | 5 | 7 | 10 | 10 | 4 | - | Cheapest Gemini. |

**Access paths on Dell:** Gemini CLI v0.4.1, VS Code Gemini Code Assist v2.84.0, VS Code Gemini CLI Companion v0.20.0, OpenClaw, Hermes Agent.

## 1.4 xAI Grok

| Model | API String | Context | $/MTok In | $/MTok Out | COD | RSN | MTH | WRT | INS | VIS | TUL | CTX | SPD | CST | AGT | LCL | Notes |
|-------|-----------|---------|-----------|-----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-------|
| Grok 4.3 | grok-4.3 | 256K | ~3.00 | ~15.00 | 9 | 9 | 8 | 7 | 8 | 7 | 7 | 8 | 6 | 5 | 7 | - | Released 2026-04. Real-time X data. |

**Access paths on Dell:** API only (via HTTP, OpenClaw, Hermes).

## 1.5 Other Cloud Providers (accessible via API from Dell)

| Provider | Model | Context | $/MTok In | $/MTok Out | COD | RSN | MTH | WRT | INS | VIS | TUL | CTX | SPD | CST | AGT | Notes |
|----------|-------|---------|-----------|-----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-------|
| MiniMax | M2.5 | 204K | 0.30 | 1.20 | 9 | 7 | 6 | 6 | 7 | 6 | 7 | 8 | 8 | 10 | 7 | 80.2% SWE-bench at $1.20/MTok out. Extreme value. |
| DeepSeek | V4 Pro | 128K | ~1.00 | ~4.00 | 9 | 9 | 9 | 6 | 8 | 7 | 8 | 7 | 6 | 8 | 8 | Best open-weight agentic model. |
| DeepSeek | R1 | 128K | 0.55 | 2.19 | 7 | 9 | 10 | 5 | 7 | 5 | 6 | 7 | 4 | 8 | 6 | Reasoning specialist. |
| Mistral | Large 3 | 128K | 2.00 | 6.00 | 8 | 8 | 7 | 7 | 8 | 7 | 8 | 7 | 7 | 6 | 7 | Apache 2.0. |
| Mistral | Small 4 | 128K | 0.20 | 0.60 | 6 | 6 | 5 | 6 | 7 | 5 | 7 | 7 | 9 | 10 | 5 | Apache 2.0. Budget. |
| Moonshot | Kimi K2.6 | 128K | ~1.00 | ~4.00 | 9 | 9 | 8 | 7 | 8 | 7 | 8 | 8 | 6 | 7 | 8 | #1 open-weight on Artificial Analysis Index. |
| Zhipu | GLM-5.1 | 202K | via Ollama Cloud | | 8 | 8 | 7 | 6 | 7 | 7 | 7 | 8 | 6 | 8 | 7 | Available on Dell via Ollama Cloud. |
| Meta | Llama 4 Scout | 10M | Free (self-hosted) | | 7 | 7 | 6 | 7 | 7 | 7 | 7 | 10 | 4 | 10 | 6 | 10M context. CPU-marginal on Dell. |
| Meta | Llama 4 Maverick | 1M | Free (self-hosted) | | 8 | 8 | 7 | 8 | 8 | 8 | 8 | 9 | - | 10 | 7 | Too large for Dell. Cloud hosting needed. |
| Alibaba | Qwen 3.6 Plus | 1M | via API | | 9 | 8 | 8 | 7 | 8 | 7 | 8 | 9 | 5 | 8 | 8 | 77.2% SWE-bench. |

**Access paths:** OpenClaw (any OpenAI-compatible API), Hermes Agent (any OpenAI-compatible API), direct API calls via Python SDKs.

---

# PART 2: LOCAL MODELS (Installed on Dell via Ollama)

28 models installed. All verified running on Ollama v0.30.5, server active on localhost:11434.

## 2.1 General Purpose / Chat Models

| Model | Family | Params | Quant | Size | Context | COD | RSN | MTH | WRT | INS | VIS | TUL | CTX | SPD | CST | AGT | LCL | Capabilities |
|-------|--------|--------|-------|------|---------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-------------|
| qwen3.5:9b | Qwen 3.5 | 9.7B | Q4_K_M | 6.6 GB | 262K | 8 | 7 | 6 | 7 | 8 | 8 | 8 | 8 | 4 | 10 | 7 | 6 | vision, completion, tools, thinking |
| qwen3.5:4b | Qwen 3.5 | 4.7B | Q4_K_M | 3.4 GB | 262K | 7 | 6 | 5 | 6 | 7 | 7 | 7 | 8 | 6 | 10 | 6 | 7 | vision, completion, tools, thinking |
| qwen3.5:2b | Qwen 3.5 | 2.3B | Q8_0 | 2.7 GB | 262K | 6 | 5 | 4 | 5 | 6 | 6 | 6 | 7 | 8 | 10 | 5 | 8 | vision, completion, tools, thinking |
| qwen3.5:0.8b | Qwen 3.5 | 873M | Q8_0 | 1.0 GB | 262K | 5 | 4 | 3 | 4 | 5 | 5 | 5 | 6 | 10 | 10 | 4 | 10 | vision, completion, tools, thinking |
| qwen3:8b | Qwen 3 | 8.2B | Q4_K_M | 5.2 GB | 40K | 7 | 7 | 6 | 6 | 7 | - | 7 | 5 | 5 | 10 | 6 | 6 | completion, tools, thinking |
| qwen3:4b | Qwen 3 | 4.0B | Q4_K_M | 2.5 GB | 262K | 6 | 6 | 5 | 5 | 6 | - | 6 | 8 | 7 | 10 | 5 | 8 | completion, tools, thinking |
| gemma4:e4b | Gemma 4 | 8.0B | Q4_K_M | 9.6 GB | - | 7 | 7 | 6 | 6 | 7 | - | 7 | 5 | 3 | 10 | 6 | 5 | completion, tools, thinking |
| gemma4:e2b | Gemma 4 | 5.1B | Q4_K_M | 7.2 GB | - | 7 | 7 | 5 | 6 | 7 | - | 6 | 5 | 4 | 10 | 5 | 6 | completion, tools, thinking |
| deepseek-r1:8b | DeepSeek R1 | 8.2B | Q4_K_M | 5.2 GB | 131K | 7 | 8 | 7 | 5 | 6 | - | - | 7 | 4 | 10 | 5 | 6 | completion, thinking |
| lfm2.5-thinking:1.2b | LFM 2.5 | 1.2B | Q4_K_M | 731 MB | 128K | 4 | 5 | 4 | 4 | 5 | - | 5 | 7 | 9 | 10 | 4 | 10 | completion, tools, thinking |
| ministral-3:8b | Mistral 3 | 8.9B | Q4_K_M | 6.0 GB | 262K | 6 | 6 | 5 | 6 | 6 | 6 | 6 | 8 | 4 | 10 | 5 | 6 | vision, completion, tools |
| ministral-3:3b | Mistral 3 | 3.8B | Q4_K_M | 3.0 GB | 262K | 5 | 5 | 4 | 5 | 5 | 5 | 5 | 8 | 6 | 10 | 4 | 7 | vision, completion, tools |
| gpt-oss:20b | GPT-OSS | 20.9B | MXFP4 | 13 GB | 131K | 7 | 7 | 6 | 7 | 7 | - | 7 | 7 | 2 | 10 | 6 | 3 | completion, tools, thinking |
| qwen2.5:0.5b | Qwen 2.5 | 494M | Q4_K_M | 397 MB | 32K | 3 | 3 | 2 | 3 | 4 | - | 4 | 4 | 10 | 10 | 2 | 10 | completion, tools |
| nuextract | NuExtract | 3.8B | Q4_0 | 2.2 GB | 4K | 3 | 3 | 2 | 2 | 5 | - | - | 1 | 6 | 10 | 1 | 8 | completion (extraction only) |

## 2.2 Coding Models

| Model | Family | Params | Quant | Size | Context | COD | RSN | MTH | WRT | INS | TUL | SPD | LCL | Capabilities |
|-------|--------|--------|-------|------|---------|-----|-----|-----|-----|-----|-----|-----|-----|-------------|
| qwen2.5-coder:7b | Qwen 2.5 Coder | 7.6B | Q4_K_M | 4.7 GB | 32K | 7 | 5 | 5 | 4 | 7 | 6 | 5 | 6 | completion, tools, insert |
| qwen2.5-coder:3b | Qwen 2.5 Coder | 3.1B | Q4_K_M | 1.9 GB | 32K | 6 | 4 | 4 | 3 | 6 | 5 | 7 | 8 | completion, tools, insert |
| deepseek-coder-v2 | DeepSeek Coder V2 | 15.7B | Q4_0 | 8.9 GB | 163K | 7 | 5 | 5 | 3 | 6 | - | 3 | 5 | completion, insert |

## 2.3 Vision / OCR Models

| Model | Family | Params | Quant | Size | Context | VIS | COD | RSN | TUL | SPD | LCL | Capabilities |
|-------|--------|--------|-------|------|---------|-----|-----|-----|-----|-----|-----|-------------|
| qwen3-vl:2b | Qwen 3 VL | 2.1B | Q4_K_M | 1.9 GB | 262K | 6 | 5 | 5 | 6 | 7 | 8 | vision, completion, tools, thinking |
| qwen2.5vl:3b | Qwen 2.5 VL | 3.8B | Q4_K_M | 3.2 GB | 128K | 6 | 4 | 4 | - | 5 | 7 | vision, completion |
| glm-ocr | GLM OCR | 1.1B | F16 | 2.2 GB | 131K | 7 | - | - | 5 | 8 | 9 | vision, completion, tools (OCR specialist) |

## 2.4 Embedding Models

| Model | Family | Params | Quant | Size | Context | Dimensions | Quality | SPD | LCL |
|-------|--------|--------|-------|------|---------|-----------|---------|-----|-----|
| qwen3-embedding:4b | Qwen 3 | 4.0B | Q4_K_M | 2.5 GB | 40K | 2560 | 8 | 5 | 7 |
| qwen3-embedding:0.6b | Qwen 3 | 596M | Q8_0 | 639 MB | 32K | 1024 | 6 | 9 | 10 |
| nomic-embed-text | Nomic | 137M | F16 | 274 MB | 2K | 768 | 5 | 10 | 10 |

## 2.5 Ollama Cloud Models (remote inference, billed via Ollama Cloud)

| Model | Provider | Params | Context | COD | RSN | MTH | WRT | TUL | SPD | AGT | Capabilities |
|-------|----------|--------|---------|-----|-----|-----|-----|-----|-----|-----|-------------|
| glm-5.1:cloud | Zhipu AI | - | 202K | 8 | 8 | 7 | 6 | 7 | 6 | 7 | completion, tools, thinking |
| glm-5:cloud | Zhipu AI | - | 202K | 7 | 7 | 6 | 6 | 7 | 6 | 6 | completion, tools, thinking |
| minimax-m2.7:cloud | MiniMax | - | 204K | 8 | 8 | 6 | 6 | 7 | 7 | 7 | completion, tools, thinking |
| qwen3-coder-next:cloud | Alibaba | 80B | 262K | 9 | 7 | 6 | 5 | 7 | 5 | 6 | completion, tools (coding specialist) |

---

# PART 3: PULLABLE MODELS (Available via `ollama pull`, not currently installed)

Ollama library has 4,500+ models. These are the highest-value models for this Dell's hardware constraints (64GB RAM, 2GB VRAM). Sorted by estimated utility.

## 3.1 High-Priority Pulls (recommended for this hardware)

| Model | Params | Est. Size | Context | COD | RSN | MTH | Why Pull | LCL |
|-------|--------|-----------|---------|-----|-----|-----|----------|-----|
| qwen3.6:8b | ~8B | ~5 GB | 262K | 8 | 8 | 7 | Latest Qwen gen, SWE-bench competitive | 6 |
| llama4-scout:17b-moe | 17B active | ~12 GB | 10M | 7 | 7 | 6 | 10M context window, MoE efficient | 4 |
| deepseek-v4:8b-distill | ~8B | ~5 GB | 128K | 8 | 8 | 7 | Best open-weight agentic coding, distilled | 6 |
| kimi-k2.6:8b-distill | ~8B | ~5 GB | 128K | 8 | 8 | 7 | #1 Artificial Analysis open-weight, distilled | 6 |
| mistral-large-3:8b-distill | ~8B | ~5 GB | 128K | 7 | 7 | 6 | Apache 2.0, good all-rounder | 6 |
| phi-4:14b | 14B | ~9 GB | 16K | 7 | 8 | 8 | Microsoft, strong math/reasoning for size | 5 |
| glm-5.1:8b | ~8B | ~5 GB | 128K | 8 | 7 | 6 | 77.8% SWE-bench (full model) | 6 |
| starcoder2:7b | 7B | ~4 GB | 16K | 7 | 4 | 3 | Dedicated code completion | 6 |
| bge-m3:latest | 567M | ~1 GB | 8K | - | - | - | Best multilingual embedding | 10 |
| mxbai-embed-large | 335M | ~670 MB | 512 | - | - | - | High-quality English embedding | 10 |

## 3.2 Cloud-Only Pulls (available via Ollama Cloud relay, no local download)

| Model | Provider | Context | COD | RSN | Why Consider |
|-------|----------|---------|-----|-----|-------------|
| deepseek-v4:cloud | DeepSeek | 128K | 9 | 9 | Full-size frontier model, cloud inference |
| llama4-maverick:cloud | Meta | 1M | 8 | 8 | Large MoE, cloud only |
| qwen3.6-plus:cloud | Alibaba | 1M | 9 | 8 | 77.2% SWE-bench, cloud only |

---

# PART 4: AGENT FRAMEWORKS AND ACCESS PATHS

## 4.1 Framework Inventory

| Framework | Version | Status | Models Accessible | MCP | Agentic Rating | Primary Use |
|-----------|---------|--------|------------------|-----|---------------|-------------|
| Claude Code CLI | 2.1.71 | ACTIVE | Claude Opus/Sonnet/Haiku | Client + Server | 10 | Coding, agentic tasks, orchestration |
| Claude Desktop/Cowork | Current | ACTIVE | Claude Opus/Sonnet/Haiku | Client | 9 | Scheduled tasks, connectors, browser |
| OpenAI Codex CLI | 0.128.0 | INSTALLED | GPT-5.5/5.4, o3/o4-mini | Client + Server | 8 | OpenAI-model coding, cross-AI delegation |
| OpenClaw | 2026.4.14 | CONFIG VALID | Any OpenAI-compatible | Yes | 8 | Multi-channel agents, persistent daemon |
| Hermes Agent | 0.15.2 | INSTALLED | Any OpenAI-compatible | Unknown | 7 | Self-learning skills, persistent memory |
| Gemini CLI | 0.4.1 | INSTALLED | Gemini 3.x/2.5 | Client | 7 | Gemini-powered coding/reasoning |
| GitHub Copilot CLI | 1.0.12 | INSTALLED | GPT-4o, Claude (via Copilot) | VS Code Agent | 6 | Inline completion, chat |
| Cline (Claude Dev) | 3.86.0 | VS Code ext | Any provider (configurable) | No | 7 | Autonomous VS Code coding agent |

## 4.2 Model-to-Framework Access Matrix

| Model | Claude CLI | Cowork | Codex | OpenClaw | Hermes | Gemini CLI | Copilot | Cline | Direct API |
|-------|-----------|--------|-------|----------|--------|------------|---------|-------|-----------|
| Claude Opus 4.8 | YES | YES | - | YES* | YES* | - | - | YES | YES |
| Claude Sonnet 4.6 | YES | YES | - | YES* | YES* | - | YES** | YES | YES |
| Claude Haiku 4.5 | YES | YES | - | YES* | YES* | - | - | YES | YES |
| GPT-5.5 | - | - | YES | YES | YES | - | - | YES | YES |
| GPT-5.4 | - | - | YES | YES | YES | - | - | YES | YES |
| o3 | - | - | YES | YES | YES | - | - | YES | YES |
| Gemini 3.5 Flash | - | - | - | YES* | YES* | YES | - | YES | YES |
| Gemini 3.1 Pro | - | - | - | YES* | YES* | YES | - | YES | YES |
| Ollama local models | - | - | - | YES | YES | - | - | YES | YES |
| Ollama cloud models | - | - | - | YES | YES | - | - | - | YES |

*Requires API key configuration in the framework
**Via Copilot's model routing

## 4.3 VS Code AI Extensions

| Extension | Version | Provider | Agentic | Models | Rating |
|-----------|---------|----------|---------|--------|--------|
| Claude Code | 2.1.156 | Anthropic | YES | Claude all | 9 |
| Gemini Code Assist | 2.84.0 | Google | YES | Gemini all | 7 |
| Gemini CLI Companion | 0.20.0 | Google | NO | Gemini all | 5 |
| ChatGPT | 26.519.32039 | OpenAI | LIMITED | GPT all | 6 |
| Copilot Vision | 0.1.1 | Microsoft | NO | GPT-4o | 5 |
| Copilot Web Search | 0.1.4 | Microsoft | NO | GPT-4o | 4 |
| Cline | 3.86.0 | Community | YES | Any | 7 |
| Windows AI Studio | 1.4.0 | Microsoft | NO | Local | 5 |
| AI Foundry | 1.2.4 | Microsoft | NO | Azure | 4 |
| AI Tools Pack | 0.1.0 | Microsoft | NO | Various | 3 |

---

# PART 5: PYTHON AI INFRASTRUCTURE

| Package | Version | Purpose | Models/Providers Accessed |
|---------|---------|---------|--------------------------|
| anthropic | 0.74.0 | Claude API SDK | All Claude models |
| openai | 1.65.5 | OpenAI API SDK | All OpenAI models; any OpenAI-compatible (Ollama, etc.) |
| ollama | 0.6.1 | Ollama Python client | All Ollama local + cloud models |
| langchain | 0.3.20 | LLM orchestration | Any model via adapters |
| langchain-core | 0.3.45 | LangChain abstractions | Framework support |
| mcp | 1.27.0 | Model Context Protocol SDK | MCP server/client building |
| torch | 2.12.0 | PyTorch | Direct model inference, fine-tuning |
| transformers | 4.51.3 | Hugging Face models | 400K+ models on HF Hub |
| sentence-transformers | 5.2.2 | Embedding models | Specialized embedding inference |
| safetensors | 0.5.3 | Safe model serialization | Model loading |

**Note:** The `transformers` library provides access to 400,000+ models on Hugging Face Hub. Any model compatible with the Dell's hardware (see constraints) can be downloaded and run directly. This is an effectively unlimited model source, constrained only by hardware.

---

# PART 6: BEST-IN-CLASS BY USE CASE

| Use Case | Best Model | Backup | Local Option | Cost |
|----------|-----------|--------|--------------|------|
| Complex agentic coding | Claude Opus 4.8 | Gemini 3.5 Flash | qwen3.5:9b | $5/$25 |
| Production code generation | Claude Sonnet 4.6 | GPT-5.4 | qwen2.5-coder:7b | $3/$15 |
| Fast code completion | Gemini 3.5 Flash | GPT-4.1 mini | qwen2.5-coder:3b | $1.50/$9 |
| Graduate-level reasoning | Gemini 3.1 Pro | o3 | deepseek-r1:8b | $2/$12 |
| Math / competition | o3 | Gemini 3.1 Pro | deepseek-r1:8b | $2/$8 |
| Creative writing | GPT-5.5 | Claude Opus 4.8 | gpt-oss:20b (slow) | $5/$30 |
| Technical writing | Claude Opus 4.8 | Claude Sonnet 4.6 | qwen3.5:9b | $5/$25 |
| Document analysis | Claude Opus 4.8 | Gemini 2.5 Pro | qwen3.5:9b | $5/$25 |
| OCR | glm-ocr (local, free) | Claude Sonnet VIS | glm-ocr | Free |
| Image understanding | Claude Sonnet 4.6 | Gemini 3.5 Flash | qwen3-vl:2b | $3/$15 |
| Bulk classification | Gemini 2.5 Flash | GPT-5.4 nano | qwen3.5:0.8b | $0.15/$0.60 |
| Structured extraction | nuextract (local, free) | Claude Haiku | nuextract | Free |
| Embeddings (English) | OpenAI text-embedding-3 | qwen3-embedding:4b | nomic-embed-text | Varies |
| Embeddings (multilingual) | qwen3-embedding:4b (local) | bge-m3 (pull needed) | qwen3-embedding:0.6b | Free |
| Router / classifier | qwen2.5:0.5b (local, free) | GPT-5.4 nano | qwen3.5:0.8b | Free |
| Scheduled automation | Claude Cowork | OpenClaw cron | - | Subscription |
| Browser automation | Claude Chrome ext | OpenClaw CDP | - | Subscription |
| Multi-channel messaging | OpenClaw | Hermes Agent | - | Free (self-hosted) |
| Self-learning agent | Hermes Agent | - | - | Free (self-hosted) |
| Cross-AI delegation | Codex as MCP server | - | - | OpenAI API |

---

# PART 7: GAPS AND STATUS

| Item | Status | Action Required |
|------|--------|----------------|
| Hermes Agent | INSTALLED v0.15.2 | Run `hermes setup` to configure model provider |
| OpenClaw | CONFIG VALIDATED | Gateway start attempted. Verify with `openclaw health`. Add cloud provider API keys for full capability. |
| MCP bridges | NONE CONFIGURED | Claude Desktop mcpServers is empty. Need Ollama MCP wrapper for local model access from Cowork/CLI. |
| Codex CLI auth | UNVERIFIED | Run `codex --version` then attempt inference to verify API key. |
| Gemini CLI auth | UNVERIFIED | Run `gemini` to verify Google auth. |
| Copilot auth | UNVERIFIED | Check VS Code accounts panel. |
| LM Studio | INSTALLED NOT RUNNING | Start if needed. Redundant with Ollama. |
| Ollama MCP server | NOT CONFIGURED | Install an Ollama MCP wrapper package, configure in Claude Desktop and/or Claude Code. |
| Newer Ollama models | NOT PULLED | qwen3.6, deepseek-v4 distills, kimi-k2.6 distills are available and would improve local capability. |
| Hugging Face models | NOT EXPLORED | transformers v4.51.3 gives access to 400K+ models. Specialized models for specific tasks available. |

---

# DOCUMENT METADATA

| Field | Value |
|-------|-------|
| Generated | 2026-06-04 |
| Machine | Dell Inspiron 7706 2n1 (DESKTOP-LOOCRQ2) |
| Verified by | Runtime PowerShell commands this session |
| Benchmark sources | Vellum LLM Leaderboard, llm-stats.com, Artificial Analysis, official provider docs, AI Magicx, Admix |
| Predecessor | MSI_AI_ROSTER_2026-06-04.md (SUPERSEDED, marked as draft) |
| Canonical location | Home of Claude - MSI Auto Project/DELL_AI_MODEL_ROSTER_2026-06-04.md |

Sources:
- [Anthropic Models Overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Opus 4.8 Announcement](https://www.anthropic.com/news/claude-opus-4-8)
- [OpenAI Models](https://platform.openai.com/docs/models)
- [OpenAI Pricing](https://openai.com/api/pricing/)
- [Gemini Models](https://ai.google.dev/gemini-api/docs/models)
- [Gemini Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Vellum LLM Leaderboard](https://www.vellum.ai/llm-leaderboard)
- [llm-stats.com](https://llm-stats.com/)
- [Ollama Library](https://ollama.com/library)
- [OpenClaw Docs](https://docs.openclaw.ai/)
- [Hermes Agent](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
- [Hermes Agent GitHub](https://github.com/nousresearch/hermes-agent)
