# AI Orchestrator Owner Receipt

Generated: 2026-06-11T04:22:56.962703+00:00
Runtime: `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator`
State DB: `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\state.sqlite`

## Acceptance

- Spec status: `24 passed`, `0 partial`, `0 missing`.
- Services: `14`. Capabilities: `46`. Open repairs: `0`.
- Cost-class mix: `{'local_resource': 6, 'subscription_quota': 7, 'subscription_usage': 1}`.
- Token flowmeters: `30` attempts, `1,316` total tokens counted or estimated.
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
| ollama-http | `qwen2.5:0.5b` | 11 | 100.0% | 151 | 184 | 335 | 30 |
| ollama-http | `deepseek-coder-v2:latest` | 2 | 100.0% | 18 | 200 | 218 | 109 |
| ollama-local | `qwen2.5:0.5b` | 2 | 100.0% | 60 | 99 | 159 | 79 |
| copilot-gh | `github-copilot-cli` | 2 | 100.0% | 20 | 132 | 152 | 76 |
| openclaw-gateway | `qwen2.5:0.5b` | 1 | 100.0% | 18 | 125 | 143 | 143 |
| hermes-agent | `qwen2.5:0.5b` | 2 | 100.0% | 23 | 47 | 70 | 35 |
| ollama-http | `qwen2.5-coder:7b` | 1 | 100.0% | 7 | 58 | 65 | 65 |
| ollama-cli | `qwen2.5:0.5b` | 1 | 100.0% | 13 | 46 | 59 | 59 |
| copilot-vscode | `github-copilot-cli` | 1 | 100.0% | 14 | 8 | 22 | 22 |
| claude-desktop-mcp | `sonnet` | 1 | 100.0% | 14 | 7 | 21 | 21 |
| claude-code-cli | `sonnet` | 1 | 100.0% | 13 | 7 | 20 | 20 |
| gemini-cli | `gemini-cli-oauth` | 1 | 100.0% | 12 | 5 | 17 | 17 |
| lm-studio | `text-embedding-nomic-embed-text-v1.5` | 1 | 100.0% | 9 | 6 | 15 | 15 |
| codex-desktop | `codex-desktop account route` | 1 | 100.0% | 0 | 9 | 9 | 9 |
| codex-desktop | `codex-account-low-effort` | 1 | 100.0% | 5 | 2 | 7 | 7 |
| codex-cli | `gpt-5.5 low-effort account route` | 1 | 100.0% | 0 | 4 | 4 | 4 |

## Road Through

- Keep bulk extraction, classification, embeddings, OCR, and cheap validation on local-resource routes.
- Spend subscription quota only when the receipt names a value reason and the reserve policy allows it.
- Treat any missing proof path, open repair, unknown cost, or metered default as a repair trigger before dispatch.
