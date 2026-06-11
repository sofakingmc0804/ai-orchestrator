# AI Orchestrator Owner Receipt

Generated: 2026-06-11T04:05:43.672513+00:00
Runtime: `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator`
State DB: `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\state.sqlite`

## Acceptance

- Spec status: `24 passed`, `0 partial`, `0 missing`.
- Services: `14`. Capabilities: `46`. Open repairs: `0`.
- Cost-class mix: `{'local_resource': 6, 'subscription_quota': 7, 'subscription_usage': 1}`.
- Token flowmeters: `2` attempts, `159` total tokens counted or estimated.
- Guardrail: metered and unknown-cost providers are not default routes; local/resource-cheap work remains first.

## Providers

| Provider | Adapter | Cost class | Health | Quota posture | Latest proof |
|---|---|---|---|---|---|
| hermes | `hermes-agent` | `local_resource` | `degraded` | local resource; no external quota | `C:\Users\Couch\.orchestrator\adapter-proof\hermes-agent\ORCHESTRATOR_OUTPUT\2026-06-05\int_c2a6af9e22bc4e5c\receipt.json` |
| lm_studio | `lm-studio` | `local_resource` | `stopped` | local resource; no external quota | `C:\Users\Couch\.orchestrator\adapter-proof\lm-studio\ORCHESTRATOR_OUTPUT\2026-06-06\int_596b4bb4e1054283\receipt.json` |
| ollama | `ollama-cli` | `local_resource` | `healthy` | local resource; no external quota | `C:\Users\Couch\.orchestrator\adapter-proof\ollama-cli\ORCHESTRATOR_OUTPUT\2026-06-05\int_a0db1d231cc24788\receipt.json` |
| ollama | `ollama-http` | `local_resource` | `healthy` | local resource; no external quota | `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\ORCHESTRATOR_OUTPUT\2026-06-11\int_c479d4cbb33948ee\receipt.json` |
| openclaw | `openclaw-gateway` | `local_resource` | `healthy` | local resource; no external quota | `C:\Users\Couch\.orchestrator\adapter-proof\openclaw-gateway\ORCHESTRATOR_OUTPUT\2026-06-05\int_1641165fec344f9b\receipt.json` |
| synthetic | `synthetic-test-service` | `local_resource` | `healthy` | local resource; no external quota | `none` |
| claude | `claude-code-cli` | `subscription_quota` | `healthy` | subscription quota; no live probe recorded | `C:\Users\Couch\.orchestrator\adapter-proof\claude-code-cli\ORCHESTRATOR_OUTPUT\2026-06-05\int_93c5feae5af74119\receipt.json` |
| claude | `claude-desktop-mcp` | `subscription_quota` | `healthy` | subscription quota; no live probe recorded | `C:\Users\Couch\.orchestrator\adapter-proof\claude-desktop-mcp\ORCHESTRATOR_OUTPUT\2026-06-05\int_2d76b32bc66f442c\receipt.json` |
| codex | `codex-cli` | `subscription_quota` | `healthy` | subscription quota; no live probe recorded | `C:\Users\Couch\.orchestrator\adapter-proof\codex-cli\ORCHESTRATOR_OUTPUT\2026-06-05\int_43bafbbe75cf480f\receipt.json` |
| codex | `codex-desktop` | `subscription_quota` | `healthy` | subscription quota; no live probe recorded | `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\adapter-proof\codex-desktop\ORCHESTRATOR_OUTPUT\2026-06-11\int_635af6b0becb4c59\receipt.json` |
| gemini | `gemini-cli` | `subscription_quota` | `healthy` | subscription quota; no live probe recorded | `C:\Users\Couch\.orchestrator\adapter-proof\gemini-cli\ORCHESTRATOR_OUTPUT\2026-06-05\int_71b87dad9c6c4200\receipt.json` |
| github_copilot | `copilot-gh` | `subscription_quota` | `healthy` | 1341/1500 remaining (89.4%; consumed 159) | `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\ORCHESTRATOR_OUTPUT\2026-06-11\int_56c4824613f04d8d\receipt.json` |
| github_copilot | `copilot-vscode` | `subscription_quota` | `healthy` | 1341/1500 remaining (89.4%; consumed 159) | `C:\Users\Couch\.orchestrator\adapter-proof\copilot-vscode\ORCHESTRATOR_OUTPUT\2026-06-05\int_b110ccff88d94e6c\receipt.json` |
| ollama_cloud | `ollama-cloud` | `subscription_usage` | `healthy` | unknown cost; disabled until classified | `none` |

## Token Flowmeters

| Provider | Model | Attempts | Success rate | Tokens in | Tokens out | Total tokens | Avg per attempt |
|---|---|---:|---:|---:|---:|---:|---:|
| ollama-local | `qwen2.5:0.5b` | 2 | 100.0% | 60 | 99 | 159 | 79 |

## Road Through

- Keep bulk extraction, classification, embeddings, OCR, and cheap validation on local-resource routes.
- Spend subscription quota only when the receipt names a value reason and the reserve policy allows it.
- Treat any missing proof path, open repair, unknown cost, or metered default as a repair trigger before dispatch.
