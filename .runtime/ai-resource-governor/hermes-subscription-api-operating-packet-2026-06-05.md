# Hermes Subscription-Safe API Operating Packet

Date: 2026-06-05
Terminal state: produced

## Consequence

Hermes can run many providers, but provider availability is not the same thing as economic permission. The working rule on this PC is:

Use local Ollama first. Use subscription-backed account routes only when the task value justifies quota burn. Never let Hermes fall through to a metered API key or unknown provider.

## Sources Read

- Hermes AI Providers: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md
- Hermes fallback providers: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/fallback-providers.md
- Hermes configuration and auxiliary models: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md
- Hermes tools and Tool Gateway: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/tools.md
- Hermes provider runtime: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/provider-runtime.md
- Hermes security model: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/security.md
- Ollama Hermes integration: https://docs.ollama.com/integrations/hermes
- Hermes PyPI package: https://pypi.org/project/hermes-agent/

## Live Machine Findings

- Hermes version: 0.15.2.
- Hermes default provider: `custom`.
- Hermes default endpoint: `http://127.0.0.1:11434/v1`.
- Hermes default model: `qwen2.5:0.5b`.
- Hermes `.env`: exists, intentionally contains no metered API keys.
- Hermes fallback chain: empty.
- Hermes auxiliary tasks: pinned to local Ollama endpoint.
- OpenAI Codex auth: imported into Hermes auth store from existing Codex CLI credentials.
- GitHub Copilot auth: usable through `gh auth token`.
- Copilot plan: `individual_pro`.
- Copilot premium quota: 1342 of 1500 remaining, 89.5 percent remaining, reset `2026-07-01T00:00:00.000Z`.
- Claude auth: logged in as Claude Max in the Claude CLI, but Hermes docs state Claude Max through Hermes requires extra usage credits; base Max allowance is not a safe Hermes route.
- Gemini OAuth: a Gemini token file exists, but Hermes resolver fails with "No Google OAuth credentials found"; not usable until `hermes auth add google-gemini-cli` passes.
- Nous Portal: not logged in.
- Qwen OAuth: not logged in.
- MiniMax OAuth: not logged in.
- xAI OAuth: not logged in.

## Provider Classes

Allowed by default:

- `custom` / local Ollama endpoint: `local_resource`.
- Ollama cloud models through local Ollama: `subscription_quota`; use only when local models are insufficient.
- `copilot`: `subscription_quota`; preserve the 20 percent reserve.
- `openai-codex`: `subscription_unlimited` / account-backed; use as high-value escalation, not bulk work.

Allowed only after explicit OAuth repair and proof:

- `google-gemini-cli`: subscription/free-quota OAuth route, currently failed resolver.
- `nous`: subscription route, currently not logged in.
- `qwen-oauth`: OAuth route, currently not logged in.
- `minimax-oauth`: OAuth route, currently not logged in.
- `xai-oauth`: OAuth route for SuperGrok or X Premium+, currently not logged in.

Blocked as defaults:

- `openai-api`, `anthropic`, `openrouter`, `gemini`, `xai`, `zai`, `kimi-coding`, `minimax`, `deepseek`, `nvidia`, `stepfun`, `alibaba`, `bedrock`, `azure-foundry`, `huggingface`, and any unknown provider.

## Hermes Configuration Made

`C:\Users\Couch\.hermes\config.yaml` now uses:

- `model.provider: custom`
- `model.base_url: http://127.0.0.1:11434/v1`
- `fallback_providers: []`
- `auxiliary.*.provider: main`
- `auxiliary.*.base_url: http://127.0.0.1:11434/v1`
- `approvals.mode: manual`
- `approvals.cron_mode: deny`

This matters because Hermes auxiliary tasks can consume model calls for compression, vision, web extraction, title generation, and approval classification. Those side calls are now local unless the config is intentionally changed.

## Guardrails Made

- `C:\Users\Couch\.ai-resource-governor\bin\hermes.ps1` logs Hermes command calls.
- The Hermes shim denies explicit metered or unknown `--provider` overrides before Hermes starts.
- `C:\Users\Couch\.ai-resource-governor\bin\hermes.cmd` makes the shim visible to command-name resolution outside pure PowerShell.
- `C:\Users\Couch\.ai-resource-governor\receipts\blocked-command-invocations.jsonl` records blocked provider attempts.

## Verified Behavior

- `hermes status` reports `Provider: Custom endpoint`, `Model: qwen2.5:0.5b`, and no metered API keys.
- `hermes doctor` no longer rejects the provider setting.
- `hermes auth list` shows `copilot` and `openai-codex`.
- `hermes --provider openrouter -z ...` is blocked by the governor shim.
- Governor route for `hermes_cli_model_call` selects local `qwen2.5:0.5b`.
- Governor route for high-value code repair can select `codex-account`.
- Governor route forced to `openai-api` is denied as `metered_extra_cost`.

## Remaining Repair Work

- Hermes local `-z` still times out even though direct Ollama OpenAI-compatible calls work. The repair path is to patch Hermes local one-shot behavior or add a governed fast path that calls Ollama directly for bounded one-shot work.
- Gemini OAuth must be repaired with `hermes auth add google-gemini-cli` before being treated as usable.
- Nous Portal can become the cleanest all-in-one subscription route only if Matt has or chooses a Nous subscription and completes `hermes auth add nous --type oauth`; until then it is disabled.
- Claude Max must remain disabled for Hermes unless extra Claude usage credits are confirmed, because Hermes docs say base Max allowance is not consumed by this route.

## Road Through

1. Keep Hermes default local through Ollama/custom.
2. Use Codex account route for hard coding, architecture, and final review only through governor-approved escalation.
3. Use Copilot when coding value justifies premium quota burn and the reserve remains above 20 percent.
4. Repair Gemini OAuth and optional Nous Portal as account-backed lanes.
5. Keep all direct API-key providers blocked until a provider is proved already-paid and the governor policy is updated.
