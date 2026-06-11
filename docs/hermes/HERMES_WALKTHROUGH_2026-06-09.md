# Hermes Desktop — Operating Walkthrough (Rev 2, post end-to-end testing)
**For:** Matt | **Date:** 2026-06-09 | Companion to `HERMES_MODEL_ROSTER_2026-06-09.md` | Doctrine: `.ai-resource-governor`

## Verified end-to-end (live tests on this machine, 2026-06-09 afternoon)

| Test | Result |
|---|---|
| Ollama Cloud API direct (ministral-3:8b, kimi-k2.6, qwen3-next:80b) | PASS — all returned PONG |
| `hermes -z` one-shot via `ollama-cloud / kimi-k2.6` | PASS |
| `hermes -z` one-shot via default (config-driven) | PASS |
| `hermes -z` via `nous` | **FAIL — "Hermes is not logged into Nous Portal"** (your 12:24 device-code login timed out). Fix below. |
| Codex OAuth | Not yet connected — your 12:51 attempts hit 429 rate limiting. Retry after a few hours. |
| Morning "Ollama not responding" root cause | Picker misroute: Ollama model `gemma4:31b` was sent to the Nous endpoint → HTTP 404 (agent.log 12:50). Ollama itself was never broken. |

## Current configuration (already applied)

1. **Default:** `ollama-cloud → kimi-k2.6` — best open-weight model, flat GPU-pressure quota, zero marginal cost. Per governor rule: cheapest competent worker.
2. **Fallback:** `copilot → claude-sonnet-4.6` — auto-engages on errors/rate limits without losing the conversation.
3. **Output cap removed** (`max_tokens: 128` was truncating every response).
4. **Auxiliary models on flat-rate Ollama Cloud:** vision → qwen3-vl:235b-instruct, compression → qwen3-next:80b, titles/skills/MCP → ministral-3:8b. All live-verified.
5. **Cost display on**; empty `max_concurrent_sessions` line removed (Hermes warned it silently drops settings).
6. **`OPENAI_API_KEY` disabled in Hermes `.env`** (commented, value preserved) per your governor policy: metered keys are blocked defaults.
7. **`model-router` skill v2 installed** — implements the governor operating loop (cheapest competent worker, 20% reserve, escalation reasons, blocked metered routes).
8. **Backup:** `config.yaml.backup-claude-20260609T182809Z` in `AppData\Local\hermes\`.

**→ Restart Hermes Desktop so config changes load.** (Skills hot-reload; config.yaml needs restart.)

## Your two pending logins (only you can do these — they need browser approval)

1. **Nous Portal:** in Hermes type `hermes login` (CLI) or Settings → Models → Nous → complete the device-code page in your browser within the time window this time. Until then, every `nous` model including the old stepfun default is dead.
2. **Codex:** Settings → Models → OpenAI Codex. It can import your existing `C:\Users\Couch\.codex\auth.json` login. This unlocks gpt-5.5 / gpt-5.3-codex / spark under your ChatGPT Pro plan. If you see 429 again, wait an hour — you tripped the device-auth rate limit this morning.

## Step-by-step: first session after restart

1. Open Hermes Desktop — status should show `ollama-cloud · kimi-k2.6`.
2. Type `/usage` — checks account limits. Copilot remaining: `gh api /copilot_internal/user` (the governor probes this; 1,284/1,500 as of today, resets July 1).
3. **When picking models in the picker, confirm the PROVIDER switches too** — this morning's failure was an Ollama model name riding on the Nous provider.
4. Switch mid-chat with `/model`; conversation survives the switch.
5. Ask Hermes "which model for X?" — the router skill answers per the governor doctrine.

## Daily routing cheat card

| Situation | Say `/model` and pick |
|---|---|
| Normal day, mixed tasks | `ollama-cloud` → kimi-k2.6 (default — flat rate, near-frontier) |
| Quality writing / careful agentic execution | `copilot` → claude-sonnet-4.6 (~1 premium request per interaction; 20% reserve rule) |
| Coding session | `openai-codex` → gpt-5.3-codex (once connected — account quota, not per-token) |
| Hardest problems only (architecture, security review, final critique) | `copilot` → claude-opus-4.8 (expensive multiplier — escalation reasons only) |
| Science/math/data | `copilot` → gemini-3.1-pro-preview |
| Bulk/background/cron | `ollama-cloud` → kimi-k2.6 or deepseek-v4-pro (FLAT) |
| Huge documents (>250K tokens) | `ollama-cloud` → deepseek-v4-pro (1M context, FLAT) |
| Trivial chat | `ollama-cloud` → ministral-3:8b (flat) — `nous` free models return only after you re-login |

## Budget watch

- **Copilot:** your account is on **request-based billing: 1,500 premium interactions/month** (live: 1,284 left, resets 2026-07-01). Probe anytime: `gh api /copilot_internal/user`. Keep the 20% (300) reserve. The 7,000-AI-credit system in the roster doc applies only if/when GitHub migrates your account to usage-based billing — watch for that email; the economics change that day.
- **Codex quota:** `/usage` in Hermes once connected, or chatgpt.com Codex page. Resets per 5-hour window + weekly.
- **Ollama:** ollama.com dashboard. Flat; caps reset per 5-hour session and weekly.
- **Overtime channels** (`anthropic`, `openai-api`): OPENAI_API_KEY is now commented out in `AppData\Local\hermes\.env` per governor policy. The router skill blocks both routes without your explicit approval.
- **June 15:** claim your **$200/mo Agent SDK credit** on your Claude Max account (Anthropic is emailing instructions). It does not apply to Hermes, but it's $200/month of Claude automation for Example scripts via `claude -p` / Agent SDK — free money otherwise left on the table.

## Customization map (what else you can shape)

| File (under `AppData\Local\hermes\`) | What it controls | Reload |
|---|---|---|
| `SOUL.md` | Personality/tone of Hermes | Hot — next message |
| `skills\<name>\SKILL.md` | Teachable procedures (like the router) | Hot |
| `config.yaml` | Default/fallback/aux models, approvals, toolsets, TTS/STT, cron | Restart |
| `hooks\` | Scripts on events | Restart |
| `cron\` | Scheduled jobs (pair with flat-rate models!) | Managed in-app |
| `memories\` | Persistent agent memory | Automatic |

## Hardware note

Your VRAM limit is irrelevant to all of the above — every model in the roster runs in someone else's datacenter. Local Ollama (`localhost:11434`) is still installed from your June 6-7 experiments; the only model it can realistically serve is tiny (qwen2.5:0.5b-class). Recommendation: don't run local models on this box; your $20 Ollama Cloud sub exists precisely to outsource that.

## Housekeeping done today

- `C:\Users\Couch\.hermes\` (old CLI-era Hermes home, last touched June 7, default model qwen2.5:0.5b) is **not** used by the Desktop app on Windows — the live home is `AppData\Local\hermes`. A `_SUPERSEDED_README.txt` marker was placed in the old folder. If you never use `hermes` under WSL/older CLI, you can delete the folder entirely.
