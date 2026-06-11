# Hermes Model Roster — Who's On Payroll, What They Do, What They Cost
**Owner:** Matt Couch | **Built:** 2026-06-09, revised same day after end-to-end testing | **Data sources:** local models.dev cache (`AppData\Local\hermes\models_dev_cache.json`), GitHub Copilot pricing docs, Anthropic help center, OpenAI Codex rate card, Artificial Analysis / SWE-bench leaderboards (June 2026), **live quota probes via AI Resource Governor**

**Authority chain:** this doc is the Hermes-dispatch companion to the canonical roster system in this repo (`data/rosters/`, `tools/roster/AI_MODEL_QUALITY_ROSTER_generator.py`) and the doctrine in `C:\Users\Couch\.ai-resource-governor\` (policy.json, operating packet 2026-06-05, v2 operating model 2026-06-09). Those govern; this applies them to Hermes.

**Rev 2 corrections from live testing (2026-06-09 afternoon):**
- Matt's Copilot Pro+ account is on **request-based billing: 1,500 premium interactions/month** (live probe: 1,284 remaining, resets 2026-07-01) — NOT the 7,000-AI-credit pool described in Section 1, which applies to accounts migrated to usage-based billing. Until GitHub migrates the account, budget in requests, keep a 20% (300-request) reserve, and remember every agent-loop interaction counts.
- Hermes default changed to `ollama-cloud / kimi-k2.6` (flat GPU-pressure quota, verified end-to-end) per governor rule "cheapest competent worker." Copilot Sonnet 4.6 is the fallback and quality-escalation, not the default.
- Nous Portal is NOT logged in (device-code flow timed out 2026-06-09 12:24) — all `nous` rows below are unusable until Matt re-runs login.
- `OPENAI_API_KEY` in Hermes `.env` was disabled (commented out) per governor policy: metered keys are blocked defaults.
- Ollama Cloud verified working end-to-end (kimi-k2.6, ministral-3:8b, qwen3-next:80b all answered live API + Hermes CLI tests). The morning's "not responding" was a picker bug/misroute: an Ollama model name was sent to the Nous endpoint (HTTP 404 in agent.log 12:50).

---

## 1. The Payroll Structure

| Channel | What you pay | What it covers in Hermes | Budget mechanics |
|---|---|---|---|
| **ChatGPT Pro** (~$200/mo) | Flat | `openai-codex` provider (OAuth): GPT-5.5, GPT-5.4, GPT-5.4-mini, GPT-5.3-codex, GPT-5.3-codex-spark (Pro-exclusive) | Message quotas per 5-hour window + weekly caps. No per-token billing. Your largest covered compute pool. |
| **GitHub Copilot Pro+** ($39/mo) | Flat + metered pool | `copilot` provider (OAuth): Claude Opus 4.8/4.7/4.5, Sonnet 4.6/4.5, Haiku 4.5, GPT-5.5, GPT-5.3-codex, Gemini 3.1 Pro, Gemini 3.5 Flash | **7,000 AI credits/mo** (3,900 base + 3,100 flex). 1 credit = $0.01, so a $70 token wallet at the per-MTok rates below. Overage only if you buy a budget. **Only subscription channel for Claude + Gemini in Hermes.** |
| **Ollama Cloud Pro** ($20/mo) | Flat | `ollama-cloud` provider: 41 open models (Kimi K2.6, DeepSeek V4 Pro, GLM-5.1, Qwen3 family, MiniMax M3...) | No per-token cost. 5-hour session caps + weekly caps (Ollama doesn't publish exact numbers; gauge in app). Your bulk-labor pool. |
| **Claude Max 20x** ($200/mo) | Flat | **Nothing in Hermes (compliantly).** Covers Claude Code, Cowork, claude.ai. From June 15, 2026 also grants a **$200/mo Agent SDK credit** — but only for apps built on the Agent SDK, which Hermes is not. | Keep Claude-heavy deep work in Claude Code/Cowork where this sub pays for it. |
| **Nous Portal** (free tier) | Free | `nous` provider: `:free` models (e.g. stepfun step-3.7-flash — your old default) plus paid portal credits for frontier models | Free models cost nothing. Paid portal models bill portal credits. Check `/usage`. |
| **Anthropic API key** | Per token | `anthropic` provider — full Claude lineup including Opus 4.8 | **Pure overtime.** Unbudgeted card spend. Use only with deliberate approval. |
| **OpenAI API key** | Per token | `openai-api` provider — GPT-5.5, 5.5-pro, 5.4 family | **Pure overtime.** Redundant with Codex OAuth for almost everything. Candidate for removal. |

**The one-sentence strategy:** burn Ollama (flat) and Codex (flat) first, spend Copilot's $70 token wallet deliberately on Claude/Gemini, keep the two API keys as emergency contractors, and do Claude-native work in Claude Code/Cowork where Max already pays for it.

---

## 2. Character Sheets — The Starting Squad

Stats from the local models.dev cache. Costs are $ per million tokens (input/output); "FLAT" = covered by flat subscription, no marginal cost.

### S-Tier: The Senior Partners (use when the problem is genuinely hard)

**Claude Opus 4.8** — *The Principal Engineer*
- Channel: `copilot` ($5/$25 from credit pool) | Overtime: `anthropic` API ($5/$25, 1M ctx)
- Stats: ctx 200K (Copilot) / 1M (API) | reasoning ✔ | tools ✔ | text+image+PDF
- Best at: highest intelligence on current leaderboards (Intelligence Index 61); hardest debugging, architecture, multi-hour agentic coding (Opus family leads SWE-bench Pro ~64%)
- Weak at: cost discipline — burns the Copilot pool ~5x faster than Haiku
- Use when: the task failed on a cheaper model, or mistakes are expensive

**GPT-5.5** — *The Chief of Staff*
- Channel: `openai-codex` (FLAT under ChatGPT Pro) | also `copilot` ($5/$30) and `openai-api` ($5/$30, 1M ctx)
- Stats: ctx 400K (Codex/Copilot) | reasoning ✔ (xhigh) | tools ✔ | text+image+PDF | knowledge Dec 2025
- Best at: frontier general intelligence (Index 60), strongest terminal/agentic coding (Terminal-Bench ~82.7%), freshest knowledge cutoff in your roster
- Weak at: nothing major; via Copilot it's pricey — always prefer the Codex channel
- Use when: heavy work of any kind, since your Pro plan already paid for it

**Gemini 3.1 Pro** — *The Research Scientist*
- Channel: `copilot` ($2/$12 — cheapest frontier in your pool)
- Stats: ctx 200K | reasoning ✔ | tools ✔ | text+image+video+audio+PDF
- Best at: scientific/math reasoning (GPQA Diamond ~94%), multimodal breadth (only video+audio input you have)
- Use when: science, data analysis, anything with mixed media

### A-Tier: The Workhorses (daily drivers)

**Claude Sonnet 4.6** — *The Site Foreman* ← **your new Hermes default**
- Channel: `copilot` ($3/$15) | overtime: `anthropic` API (1M ctx)
- Stats: ctx 200K (Copilot) | reasoning ✔ | tools ✔ | text+image+PDF
- Best at: dependable agentic execution, writing, tool use; the best quality-per-credit in the Claude family
- Use when: default for everyday Hermes sessions

**GPT-5.3-codex (+ Spark preview)** — *The Staff Software Engineer*
- Channel: `openai-codex` (FLAT) | Spark variant is ChatGPT Pro-exclusive
- Stats: ctx 400K | reasoning ✔ | tools ✔
- Best at: purpose-built coding agent; biggest covered message quota of the strong coders (600–3,000 local msgs/5h on Pro-tier plans)
- Use when: any coding session — it's free marginal cost to you

**GPT-5.4 / GPT-5.4-mini** — *The Generalist / The Fast Associate*
- Channel: `openai-codex` (FLAT)
- Stats: ctx 400K–1M | reasoning ✔ | 5.4-mini gets 1,200–7,000 msgs/5h
- Use when: versatile mid-weight work (5.4); fast iteration loops (5.4-mini)

**Kimi K2.6** — *The Open-Source Ace*
- Channel: `ollama-cloud` (FLAT)
- Stats: ctx 262K | reasoning ✔ | tools ✔ | text+image
- Best at: #1 open-weight model (Index ~54, SWE-bench Pro ~58.6%) — near-frontier quality at zero marginal cost
- Use when: you want S-tier-adjacent quality without touching a metered pool

**DeepSeek V4 Pro** — *The Long-Haul Analyst*
- Channel: `ollama-cloud` (FLAT)
- Stats: ctx **1M** | reasoning ✔ | tools ✔
- Best at: frontier-adjacent reasoning famously ~34x cheaper than closed rivals; your only flat-rate million-token context
- Use when: huge documents, codebases, long background jobs

**GLM-5.1** — *The Night-Shift Coder*
- Channel: `ollama-cloud` (FLAT)
- Stats: ctx 202K | reasoning ✔ | tools ✔
- Best at: built for ~8-hour autonomous coding runs (SWE-bench Pro ~58.4%, MIT license)
- Use when: long unattended agent jobs, cron tasks that write code

### B-Tier: The Support Staff (speed and volume)

| Model | Channel | Stats | Role |
|---|---|---|---|
| Claude Haiku 4.5 | `copilot` $1/$5 | 200K ctx, reasoning ✔ | Cheap Claude voice for light tasks |
| Gemini 3.5 Flash | `copilot` $1.50/$9 | 200K ctx, video+audio in | Fast multimodal triage |
| MiniMax M3 | `ollama-cloud` FLAT | 512K ctx, text+image+video | Big-context quick work |
| Qwen3-next:80b | `ollama-cloud` FLAT | 262K ctx | Now your **compression** aux model |
| Qwen3-VL 235B | `ollama-cloud` FLAT | 262K ctx, vision | Now your **vision** aux model |
| Ministral-3:8b | `ollama-cloud` FLAT | 262K ctx | Now your **title/skills/mcp** aux model |
| stepfun step-3.7-flash:free | `nous` FREE | — | Your old default; fine for trivial chat |

---

## 3. Coverage Rules — The "Insurance Card"

1. **Claude in Hermes = Copilot only.** Anthropic's Feb 2026 policy (enforced server-side since April 4) restricts subscription OAuth to Claude Code and claude.ai. Hermes technically has a Claude-OAuth seeding path, but using it violates the Consumer ToS and risks your Max account. Don't.
2. **June 15, 2026:** your Max 20x plan starts including a **$200/mo Agent SDK credit** (claim it once in your Claude account — watch for Anthropic's email). It covers `claude -p`, Agent SDK apps, and Claude Code GitHub Actions — not Hermes. Use it for Example automation built on the Agent SDK instead.
3. **Codex OAuth in third-party tools:** OpenAI has been permissive (its own programs name OpenCode/Cline/OpenClaw), but there is no explicit blanket blessing; community threads note ambiguity. Risk: low. Counter-evidence noted per your standards.
4. **Copilot in Hermes:** GitHub historically targeted unofficial API clients; Hermes authenticates via the GitHub device flow. Works today; same low-but-nonzero policy risk as every Copilot-in-agent integration.
5. **Copilot overage:** when the 7,000 credits run out, you either wait for reset, buy a dollar budget (1 credit = $0.01), or shift work to Codex/Ollama. Code completions in the IDE stay unlimited and never touch the pool.

## 4. Refinement Path (what's not yet verified)

- **Ollama Pro exact caps:** not published; verify in your dashboard at ollama.com after heavy days.
- **Your actual Codex weekly limits:** check `/usage` in Hermes once Codex is connected, or the Codex rate card (help.openai.com → "Codex rate card").
- **Nous Portal balance/tier:** run `/usage` in Hermes.
- **Whether Nous adds Agent SDK auth** (would unlock your $200 Anthropic credit inside Hermes): watch hermes-agent release notes.

## Sources

- Local: `AppData\Local\hermes\models_dev_cache.json`, `provider_models_cache.json`, hermes-agent source (codex_models.py, credential_sources.py, anthropic_adapter.py)
- [GitHub Copilot models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing)
- [GitHub Copilot usage-based billing for individuals](https://docs.github.com/en/copilot/concepts/billing/usage-based-billing-for-individuals)
- [Anthropic: Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [The Register: Anthropic clarifies ban on third-party tool access](https://www.theregister.com/2026/02/20/anthropic_clarifies_ban_third_party_claude_access/)
- [OpenAI: Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan) | [Codex rate card](https://help.openai.com/en/articles/20001106-codex-rate-card)
- [Ollama pricing](https://ollama.com/pricing)
- [Artificial Analysis leaderboard](https://artificialanalysis.ai/leaderboards/models) | [Vellum LLM leaderboard](https://www.vellum.ai/llm-leaderboard) | [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs/)
