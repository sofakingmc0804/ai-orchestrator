# Hermes capacity implementation — 2026-08-04

## Outcome

Hermes Desktop is currently configured for `z-ai/glm-5.2` through OpenRouter with `xhigh` reasoning. Normal manual chat keeps that configured provider and model. Explicitly classified orchestrator jobs can use a governed provider lane, and the selected lane is now passed to the Hermes process instead of being written only to a receipt.

## The corrected execution path

1. `scripts/hermes-router.ps1` receives a Hermes command.
2. A prompt is classified through `orchestrator.cli.main hermes-route`.
3. `orchestrator/hermes/capacity_lanes.py` translates the selected worker into a native Hermes provider name, model, lane state, and reason.
4. Metered or unknown-cost workers are rejected before invocation.
5. Only an explicitly classified orchestrator job receives injected `--provider` and `-m` arguments. Unclassified manual chat is left untouched.
6. `orchestrator.governance.hermes_exec` invokes the real Hermes executable with a true argument array.
7. The route receipt records the worker, adapter, provider, model, contract, lane state, and reason.

## Supported lane translations

| Worker/provider family | Hermes provider | Execution mode | Cost guard |
|---|---|---|---|
| Ollama local/HTTP/CLI | `custom` | existing local adapter | local resource |
| Ollama Cloud | `ollama-cloud` | existing cloud path | live subscription usage |
| OpenAI Codex | `openai-codex` | Hermes provider when explicitly connected; current live account route remains `codex-desktop` | subscription route only |
| Nous Portal | `nous` | Hermes provider | owner OAuth plus live status |
| OpenRouter free models | `openrouter` | Hermes provider | model must be explicitly `:free` |
| OpenCode Zen | `opencode-zen` | Hermes provider | quota/account proof required before roster activation |
| Anthropic/OpenAI direct API | native provider name | not executable by default | blocked as metered extra cost |

## Current live connection state

Verified from a fresh Hermes CLI process and the live orchestrator roster on 2026-08-04:

- Active default: `z-ai/glm-5.2` through OpenRouter, with `xhigh` reasoning.
- Hermes-native OpenAI Codex OAuth: logged in; refresh token present; Hermes records a successful refresh.
- Hermes-native Nous Portal OAuth: logged in; access token, refresh token, and expiry present; shared refresh state exists at `C:\Users\Couch\AppData\Local\hermes\shared\nous_auth.json`.
- OpenRouter and OpenCode Zen API keys: present in Hermes's `.env` and represented in Hermes's credential pool.
- Anthropic API credentials exist but remain policy-blocked as a metered lane.
- The existing Codex worker in the orchestrator remains `codex-desktop` / `dispatch_only`; it is not the Hermes-native `openai-codex` worker.
- The live reconciliation now has 68 rows in the active runtime roster and 117 rows in the source roster. It added live rows for OpenRouter, OpenCode Zen, Nous, native OpenAI Codex, LM Studio, and OmniRoute without copying credentials. OmniRoute rows are visible but remain `unknown_cost` and blocked until their upstream billing is proven.

## OAuth persistence verdict

No persistence repair was necessary. Hermes already owns the OAuth stores, writes them under a cross-process lock, and refreshes credentials back into those stores. A new Hermes process reported both `openai-codex: logged in` and `nous: logged in`; Nous reported `Refresh: yes`. No credentials, tokens, or OAuth sessions were copied between applications, and no second refresh manager was introduced.

## Directed work status after the end-to-end run

- OpenRouter: live API/model proof captured; 338 models and 14 explicit `:free` models observed; key usage reported `$0.00`. Four free workers are admitted.
- OpenCode Zen: live model catalog proof captured; 60 models observed. Its current provider contract is explicitly `third_party_metered`/billing-unproved, so the rows remain visible but are not executable.
- Nous: four free workers are admitted with live model/catalog evidence, but the current account reports `access depleted`; the workers are degraded and avoid deep/agentic work until the provider restores access.
- Native Codex: five workers are admitted with live catalog and usage proof; the current session reports 25% remaining. The existing direct `codex-desktop` route remains a truthful `dispatch_only` path.
- LM Studio: already installed and running; its live embedding model is admitted and the native adapter returned `embedding_dimensions=768`.
- OmniRoute: installed as npm package `3.8.49`, running hidden on `127.0.0.1:20128`, and integrated through the `omniroute` adapter. Its three live model rows are intentionally `unknown_cost`/blocked pending upstream billing proof.
- Failover: durable receipt `hermes-failover-proof-20260804T184140Z.json` proves one actual OpenRouter HTTP 400 primary failure followed by successful local Ollama completion with `FAILOVER_OK`.
- Concurrent project lanes: three projects were routed concurrently and each produced a stable project-lane receipt; three simultaneous local Ollama executions all completed. The observed starting assignments were OpenRouter for alpha/beta and Ollama local for gamma.
- Second ChatGPT workspace OAuth: still owner-bound. A labeled `workspace-2` device flow was launched but has not completed provider sign-in/consent; only one native Codex credential is currently persisted.

The remaining external boundary is only the second provider-owned OAuth consent. The technical admission, quota receipts, failover proof, concurrent load proof, LM Studio integration, and OmniRoute integration are complete. Metered/unknown-cost lanes remain blocked by design.

## Verification

- Focused current tests pass for live capacity admission, stable project lanes, Hermes bridge integration, and OmniRoute registration; the live failover harness also produced a passed receipt.
- A live consumer smoke test through `scripts/hermes-router.ps1 -z "Return exactly OK."` returned `OK`.
- Hermes status now reports `z-ai/glm-5.2` and OpenRouter, with both native OAuth providers logged in.
- The previously stale manual-chat override to `qwen2.5:0.5b` is no longer injected.
- A direct Codex Desktop selection now returns a truthful `dispatch_only` boundary instead of being misreported as a Hermes-native Codex call; paid direct-API and metered lanes return explicit block codes.
