# Hermes Free-Tier Stacking Plan: Verification, Expansion, Options

**Date:** 2026-08-04, Rev 2 (same day). **Scope:** evaluates and expands the "stack multiple free AI tiers for Hermes Agent" plan against Matt's actual subscription set. Analysis only; nothing installed, configured, or changed.

> **Live-state addendum (2026-08-04):** The original analysis and build sequence below are historical. Hermes-native OpenAI Codex and Nous OAuth are connected through their supported flows and persist in Hermes-managed stores. OpenRouter and OpenCode Zen keys have live catalog/quota receipts; their safe worker rows are admitted, while OpenCode remains billing-blocked. LM Studio is live and integrated, OmniRoute 3.8.49 is installed and integrated as an unknown-cost gateway, and the concurrent project-lane plus provider-failover proofs are complete. The only remaining owner boundary is completion of the separately labeled second ChatGPT workspace OAuth. No credentials were copied between applications.

**Rev 2 changes:** Rev 1 undercounted what's already paid for and led with caution rather than a build plan. Matt corrected the subscription picture directly (six paid channels, not three, all consumed quickly despite being substantial, because of how many concurrent projects run at once) and asked for a resourceful plan, not a hedge. This revision corrects the payroll table against his stated facts and this machine's actual credential store, finds the single largest existing pool isn't even connected to Hermes yet, confirms the credential system already supports multiple accounts per provider, narrows the auth-collision risk to the specific providers that actually carry it, and adds a concrete architecture for spreading concurrent projects across channels. The Rev 1 fact-checks (OpenRouter limits, model names, OmniRoute, OpenCode Zen) didn't change and are kept below for the record.

## Rev 3: execution boundary, OAuth correction, and the exact runbook

**Correction on OAuth:** the caution in Rev 2 was never about avoiding OAuth, it was about a second automated process racing Hermes's own handling of an OAuth refresh token, which is specifically what broke Nous in June. Codex, Copilot, and Nous are exactly the channels worth using hardest. ChatGPT Pro's OAuth is sitting there already logged in and fresh as of this morning (`.codex\auth.json`, `auth_mode: chatgpt`, last refreshed today), just not imported into Hermes yet. Using it fully, and getting Nous re-logged in, are the first two moves below.

**What this session can and can't execute, confirmed directly, not assumed:** this session has read/write access to files under `C:\Users\Couch\`, in a separate Linux sandbox with no path to the Windows machine, no reachable `hermes.exe`, no reachable `localhost:11434`, nothing. OAuth device-code approvals and anything that has to run as a Windows process are yours to run for that reason, not caution. Everything that doesn't require that, I already did (below) or will do the moment you hand back what's needed.

**What I verified so nothing below is a guess:**

| Provider (Hermes id) | What it needs | Exact mechanism | Status today |
|---|---|---|---|
| `openai-codex` (ChatGPT Pro) | Import existing login | Settings → Models → OpenAI Codex (import), or `hermes auth add openai-codex` | `.codex\auth.json` active and fresh; not imported into Hermes |
| `nous` | Fresh device-code login | `hermes portal login` or `hermes auth add nous --type oauth`, then `hermes auth status nous` | No token in Hermes's `auth.json` |
| `openrouter` | API key | Add `OPENROUTER_API_KEY=` to `AppData\Local\hermes\.env`, or `hermes auth add openrouter` | Not connected; needs an openrouter.ai signup first |
| `opencode-zen` | API key | Add `OPENCODE_ZEN_API_KEY=` to the same `.env` (a commented template already sits in `.env.example`) | Not connected; needs an opencode.ai/auth signup first |
| `anthropic` (API, ~$200/mo) | Policy unblock | Blocked in two places: `disabled_billing_classes` in `orchestrator\config\governance_policy.json`, and `CONTRACT_BLOCKLIST` in `orchestrator\routing\worker_routing.py:38` | Configured (2 keys in `auth.json`), deliberately policy-blocked |
| `openai-codex` (second, $20/mo workspace account) | Second OAuth login | Likely `hermes auth add openai-codex --label workspace`, same array pattern already working for your 2 Anthropic keys | Untested, first real attempt needed |

**The one thing I'm not flipping without you saying so:** the Anthropic API route is blocked in two places on purpose, and your own skill file says "NEVER use or re-enable without Matt's explicit approval naming the cost." That's a spend switch, not a technical blocker. Two questions, then I'll make both edits the moment you answer: is the $200/mo actually a console.anthropic.com API budget (not Claude Max, which is OAuth-only and stays out of Hermes regardless of approval, per ToS), and do you want it unblocked for Hermes to route to.

### Runbook: exactly what you do, in order

1. **Connect ChatGPT Pro (2 min).** Open Hermes Desktop, Settings, Models, OpenAI Codex, use the import option if one shows (it should pick up `.codex\auth.json` directly). If there's no import option in the UI, open a terminal and run `hermes auth add openai-codex`. Tell me what model list shows up under Codex when it's done.
2. **Repair Nous Portal (2 min).** In a terminal: `hermes portal login` (or `hermes auth add nous --type oauth`), approve the device code in your browser inside the time window this time, then confirm with `hermes auth status nous`. Tell me when it's logged in and I'll check whether HY3 shows in the live model list.
3. **Answer the Anthropic question above.** One sentence back and I'll edit both policy files myself.
4. **OpenRouter (5 min).** Sign up at openrouter.ai, generate a key, add `OPENROUTER_API_KEY=<your key>` to `AppData\Local\hermes\.env`. Tell me once it's saved.
5. **OpenCode Zen (5 min).** Sign up at opencode.ai/auth, generate a key, add `OPENCODE_ZEN_API_KEY=<your key>` to the same `.env`. Tell me once it's saved.
6. **Second ChatGPT account (5 min, first real attempt).** Log into that account in a browser, then try `hermes auth add openai-codex --label workspace` (exact flag name unverified, first attempt). Paste me whatever it says, including any error.
7. **Once 1, 4, and 5 are confirmed done**, I'll wire OpenRouter and OpenCode Zen into the routing config using whatever `/model` shows live at that point rather than a guessed model name, since free-tier rosters rotate and a name I pick today could be stale by the time you act on it.
8. **Concurrent-load test, once the above is live.** Run two or three Hermes sessions on different projects at once and watch whether they spread across providers on their own or all pile onto the same one. Tell me what you see and I'll build the per-project lane assignment from that evidence instead of guessing it's needed.

## Rev 4: actually executed, found a real blocker, and it isn't OAuth

Loaded direct desktop/PowerShell execution on this machine (Windows-MCP) and ran the real commands instead of describing them.

`hermes auth list`, live: anthropic actually has 3 credentials, not 2. #1 manual API key (active, marked with the picker's `←`), #2 a `claude_code` OAuth credential Hermes has already pooled from Claude Code's own login on this machine, #3 the env-sourced API key. The active one is the manual API key, consistent with the earlier read. The `claude_code` entry sits in the pool unused, and per Hermes's own hard rule it should stay that way (ToS-restricts Claude Max/Claude Code OAuth to Claude Code and claude.ai). Flagging it so it doesn't get selected by accident later.

Ran `hermes auth add openai-codex --type oauth` directly. It got past argument parsing into the real login call and failed with `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate`. Ran `hermes auth add nous --type oauth`, identical failure, same code shape, different provider. Tested two real fixes before stopping: pointed the command at the CA bundle `.env` already names (`hermes-ca-bundle.pem`, confirmed to exist, 237KB, touched this morning) with `--ca-bundle`, same error; upgraded `certifi` inside Hermes's own venv via `uv` (2026.5.20 to 2026.7.22, a safe, completed change), same error. Norton 360 is confirmed actively running (NortonSvc plus 4 NortonUI processes). That combination points at Norton's SSL/web scanning intercepting the connection to `auth.openai.com` and `portal.nousresearch.com` specifically, most likely because those two domains have never been contacted from Hermes before, while Ollama Cloud and Copilot/GitHub, both in regular use, aren't hitting this.

This is not "OAuth needs a human." Both commands ran the real login logic and failed before any browser or device code would even appear. It's a local TLS trust problem that would block an identical attempt from you right now too, not a limitation on what this session can execute.

**What unblocks it, genuinely Matt's call since it's his security software:** add a Norton exclusion for `C:\Users\Couch\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe` (or the whole `hermes-agent` folder) under Norton's SSL/web-scan settings, or briefly disable Norton's web/SSL scanning to confirm that's really the cause. Say which and I'll retry both commands immediately after.

**Confirmed correct regardless:** `hermes auth add openai-codex --type oauth` and `hermes auth add nous --type oauth` are the right commands, both reached the real network call. Once the SSL issue clears, whatever comes next (an auto-import, or a device code for Matt to approve) should surface immediately on retry.

## What Hermes does that nothing else in this stack does

Claude.ai, ChatGPT, and Gemini's own apps are each locked to one vendor's models, run only when a session is open, and keep separate histories with no shared task view. Hermes is the one piece in this whole stack built to be provider-agnostic and to run unattended: `cron/` and `hooks/` give it real scheduled and event-triggered execution on Matt's own machine, not gated by a browser tab; `kanban.db` and `projects.db` give it a native, structured view across many concurrent projects instead of scattered chat threads; `credential_pool` in `auth.json` already stores multiple accounts per provider; and the model-router skill classifies every task into one of 14 job classes and walks a single failover ladder (health, live quota, measured quality, cost) across whichever of Matt's six subscriptions is best positioned to take it, with one continuous memory and skill set regardless of which vendor answered. No single vendor's own app does that, by design, since none of them will route a task to a competitor. That's the actual case for investing effort here: Hermes is the coordination layer across all six subscriptions plus free and local capacity, and "a ton of projects going on at the same time" is exactly the workload that layer exists for.

## The corrected payroll picture

| Channel | Matt's figure | Connected in Hermes today | Usable in Hermes | Notes |
|---|---|---|---|---|
| Anthropic API | ~$200/mo | Configured: two `api_key` credentials in `auth.json` (priority 0 manual, priority 1 from env) | Technically yes, currently policy-blocked as a forbidden metered default pending explicit approval | Both credentials are `auth_type: api_key`, not OAuth, which fits a budgeted API-console spend rather than Claude Max. Claude Max is OAuth-only and is explicitly ToS-blocked from Hermes in Hermes's own skill file (Hard Rule 2). If this $200/mo is actually Claude Max and not an API budget, this row inverts entirely and that channel stays out of Hermes. Worth a ten-second confirmation before unblocking it. |
| ChatGPT Pro | ~$200/mo | Not connected. No `openai-codex` entry exists in `auth.json` today | Not yet, one login away | The June walkthrough already planned this connection; it either never happened or was disconnected since. This was the largest single covered pool in the original roster and it's currently unused inside Hermes. |
| ChatGPT (workspace, different username) | ~$20/mo | Not connected | Plausible as a second credential entry, unverified | `credential_pool` already stores arrays per provider, exactly what a second OpenAI login needs; see below. |
| Ollama Cloud | ~$200/mo | Connected, current default | Yes, in daily use | Today's live model cache has 20 models (including `nemotron-3-ultra`, `kimi-k3`, `glm-5.2`), well beyond the 9 the June roster knew about. Current public pricing pages show tiers topping out at $100/mo ("Max," 10 concurrent models); Matt's figure is roughly double that, worth a direct look at the actual billing page. |
| Copilot Pro+ | Not restated by Matt this time | Connected, live fallback (`copilot / claude-sonnet-4.6` in `config.yaml`, one `api_key` credential in `auth.json`) | Yes, in daily use | Carried over from the June roster ($39/mo, ~1,500 request/mo budget). Flagging in case it's since been cancelled, since Matt's list didn't mention it. |
| Nous Portal | Free | Not connected, no access or refresh token | Not yet, one device-code login away | Free specifically, and unlocks HY3 per Rev 1's research. |
| Gemini Pro (personal, couchy48@gmail.com) | Not stated | Not applicable | No, structurally, not a Hermes gap | Confirmed against Google's own docs and independent write-ups: the consumer Google AI Pro subscription (currently $19.99/mo) doesn't include API access, that's sold separately. Gemini already reaches Hermes today through Copilot's bundled access instead, sharing Copilot's pool, not this subscription. |

## The single highest-leverage move available right now

Before adding anything new: ChatGPT Pro, the largest flat-rate pool in the original roster (GPT-5.5 and gpt-5.3-codex under a 5-hour-and-weekly message allowance, no per-token cost), has no stored credential in Hermes as of today. Connecting it (Settings, Models, OpenAI Codex, or importing the existing `.codex\auth.json` login) is a five-minute action against an account already being paid for, with more headroom than anything free-tier stacking would add on its own.

## Multi-account is a supported pattern, not a workaround

`auth.json`'s `credential_pool` stores an array of credentials per provider, not a single slot. `anthropic` already has two: one added manually, one sourced from the environment, ordered by priority. That's the same mechanism a second, separate OpenAI login (the $20/mo workspace account) would use, a second entry in an `openai-codex`-type array, not a new subsystem. It hasn't been tried end-to-end for OpenAI specifically, so treat it as a strong technical likelihood based on the existing pattern, not a confirmed fact until it's actually logged in.

## Free tiers, correctly risk-scoped

The incident that retired `.ai-resource-governor` in June was specific: an automated process raced with Nous Portal's OAuth device-flow refresh token and revoked it. That risk lives in OAuth/device-flow providers, Nous, and whichever process ends up handling the Codex and second-ChatGPT logins. It doesn't apply to OpenRouter or OpenCode Zen, both of which authenticate with a stable API key, the same mechanism already running cleanly today for Ollama Cloud and both Anthropic credentials. Adding those two as automated fallback rungs doesn't reintroduce the June failure mode, it's a structurally different, lower-risk mechanism. The caution that does still hold: don't run a second, independent process managing Nous's or Codex's OAuth session alongside Hermes's own handling of it, since that specific pattern is what broke last time, not multi-provider automation in general.

The Rev 1 numbers on the free layers didn't change: OpenRouter free gives 20 requests a minute and 50 to 1,000 a day depending on whether $10 in lifetime credit has ever been purchased; OpenCode Zen gives 100 free requests a day with no key needed, including a "DeepSeek V4 Flash Free" listing; HY3 (Tencent, 295B MoE, Apache 2.0, released 2026-07-06) is free specifically through Nous once it's reconnected.

## Architecture for concurrent projects: lanes, not one waterfall

The model-router skill already classifies every task into one of 14 job classes and walks a failover ladder ordered by health, live quota, measured quality, and cost, which in theory should already spread concurrent work across providers as each one's quota depletes. Whether that live-quota tracking actually keeps up under real concurrent load, several projects running at once, is worth testing directly rather than assuming either way; `STATUS.md` itself insists on measured evidence over asserted completeness for exactly this kind of claim, and the skill file's own token-conservation section notes parts of this infrastructure are still design-phase, not wired end-to-end. If quota-awareness turns out to lag reality under concurrent load, the more reliable fix is coarser: assign a default channel per project instead of per task, for instance one active project pinned to Ollama Cloud, another to Copilot, another to OpenRouter free, another to Codex once connected, so concurrent sessions start from different points in the ladder instead of converging on whichever model is ranked first and draining it together before any of them reach the next rung. Automatic failover still applies inside each project's lane; only the starting point becomes fixed per project instead of shared.

One operational note for unattended work specifically: the model-router skill flags that a governor approval gate still intercepts `terminal` and `execute_code` calls and can silently sit waiting for consent. Worth checking that any cron job intended to run unattended isn't going to stall on that gate.

## Worth a direct check

Whether the $200/mo Anthropic channel is a budgeted API key or Claude Max under a different mental model (changes whether it can touch Hermes at all). Whether Ollama Cloud's actual current tier matches the $100/mo "Max" ceiling on public pricing pages or something higher Matt is actually paying for. Whether Copilot Pro+ is still active. Whether a second OpenAI login actually slots cleanly into `credential_pool` as a new array entry. And whether the failover ladder's live-quota tracking actually holds up with several Hermes sessions running at once, versus needing the per-project lane assignment above.

## Build sequence

Superseded by the Rev 3 runbook above, which replaces this with exact commands and verified provider IDs instead of general ordering.

---

## Detail: Rev 1 claims verified (unchanged)

| Claim | Status | Confidence | Evidence / notes |
|---|---|---|---|
| OpenRouter free tier: 20 requests/min | Accurate | HIGH | 5 independent sources (OpenRouter's own help center, klymentiev.com, flo2.com, datastudios.org, truefoundry.com) |
| OpenRouter free tier: 200 requests/day | Inaccurate | HIGH | Actual is 50/day under $10 lifetime credit, 1,000/day once $10+ has been purchased (permanent) |
| "Omniroot" local gateway, 230+ endpoints | Real tool, name garbled | HIGH | Real name is OmniRoute: MIT-licensed, 237-248 providers, OpenAI-compatible endpoint, Node/Docker/Electron app |
| OmniRoute reliability | Unresolved | LOW | One headline reads "Free, Open-Source, and Under Scrutiny"; article body and issue tracker not reviewed |
| "OpenCode API / Provider Tiers" | Real, described vaguely | HIGH | Real mechanism is OpenCode Zen: 100 free requests/day, OpenAI-compatible, no key needed for tagged free models |
| HY3 (Tencent) exists | Accurate | HIGH | Tencent's own release announcement plus five independent outlets: 295B MoE, 21B active params, 256K context, Apache 2.0, released 2026-07-06 |
| HY3 free via Nous Portal | Plausible | MEDIUM | Single source (mer.vin); confirm directly once Nous login is repaired |
| Nemotron 3 Ultra exists | Accurate | **HIGH (upgraded)** | Now confirmed directly: `nemotron-3-ultra` is live in this machine's own Ollama Cloud model cache today |
| Hermes 3 (405B) | Accurate | HIGH | Established Nous Research release, fine-tune of Llama 3.1 405B |
| DeepSeek V4 Flash | Accurate | HIGH | Confirmed in live `config.yaml`, dated roster, and OpenCode Zen's free-model list |
| Step 3.7 Flash | Accurate | HIGH | Matt's own dated, live-tested roster: `stepfun step-3.7-flash:free` via Nous |
| Qwen 3.6 Plus | Version accurate, tier name unconfirmed | MEDIUM-HIGH | Tracked in the dated internal roster against Alibaba's own docs; "Plus" label not re-checked |
| "$0 cost, unlimited" framing | Not supported as written | HIGH | Every layer has a real ceiling; the corrected picture above still adds meaningful capacity, just not unlimited capacity |
| "3-5 automated complex projects/day" | Unsupported | LOW | No source found for this figure anywhere |

## Sources

- [OpenRouter Rate Limits](https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know) · [OpenRouter Free Tier 2026](https://klymentiev.com/blog/openrouter-free-tier) · [OpenRouter Free Tier Limits](https://flo2.com/blog/openrouter-free-tier-limits) · [OpenRouter Pricing 2026](https://www.truefoundry.com/blog/openrouter-pricing) · [OpenRouter Rate Limits Explained](https://www.datastudios.org/post/openrouter-rate-limits-explained-request-caps-free-model-limits-provider-quotas-scaling-issues)
- [OmniRoute (ChrisCompton)](https://github.com/ChrisCompton/omniroute) · [OmniRoute (diegosouzapw)](https://github.com/diegosouzapw/OmniRoute/blob/main/llm.txt) · [OmniRoute: Under Scrutiny](https://www.compsmag.com/news/omniroute-ai-gateway-free-open-source/) · [OmniRoute overview](https://dev.co/ai/mcp/omniroute)
- [Tencent Hunyuan Officially Releases Hy3](https://www.tencent.com/en-us/articles/2202386.html) · [Tencent launches Hunyuan Hy3](https://technode.com/2026/07/07/tencent-launches-hunyuan-hy3-integrates-model-across-multiple-products/) · [Hy3 free on Nous Portal](https://mer.vin/2026/07/tencent-hy3-295b-moe-agent-model-free-on-nous-portal/) · [Hy3 vs Nemotron 3 Ultra](https://openrouter.ai/compare/tencent/hy3/nvidia/nemotron-3-ultra-550b-a55b)
- [OpenCode Zen docs](https://opencode.ai/docs/zen/) · [OpenCode Zen](https://opencode.ai/zen) · [OpenCode Zen Free Models 2026](https://www.maximalstudio.in/blog/opencode-zen-free-models)
- [Ollama Cloud Pricing 2026 ($0/$20/$100 tiers)](https://pooyagolchian.com/blog/ollama-cloud-pricing-hardware-requirements-2026/) · [Ollama Cloud Tiers Explained](https://blog.progressiverobot.com/ollama-cloud-free-vs-pro-usage-limits-pricing-and-what-you-actually-get-2026)
- [Google AI Plans, Gemini API docs](https://ai.google.dev/gemini-api/docs/google-ai-plans) · [Gemini subscription access for third-party apps (Google AI Developers Forum)](https://discuss.ai.google.dev/t/gemini-subscription-access-for-third-party-apps-is-a-sanctioned-program-planned/175577) · [Google AI Pro & Ultra](https://gemini.google/subscriptions/)

Local sources read today: `AppData\Local\hermes\config.yaml`, `SOUL.md`, `auth.json` (structure only, secrets redacted), `provider_models_cache.json`, `skills\model-router\SKILL.md`; `dev\ai-orchestrator\docs\hermes\HERMES_MODEL_ROSTER_2026-06-09.md`, `HERMES_WALKTHROUGH_2026-06-09.md`, `NOUS_AUTH_REPAIR_2026-06-16.md`; `dev\ai-orchestrator\docs\CURRENCY_VERIFICATION_SOP.md` and `STATUS.md`.
