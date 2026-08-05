---
name: claude-code-cli-headless
description: Execute local Hermes Claude subscription work through the provider-owned Claude Code CLI OAuth session.
---

# Claude Code CLI headless

Use Hermes' `claude-code-cli` adapter for Claude.ai subscription work.

## Required practice

- Invoke `claude -p` through the Hermes adapter.
- Keep `--no-session-persistence` enabled for one-shot work.
- Request `--output-format json` and preserve structured usage metadata.
- Pass the selected alias or full model ID through `--model`.
- Use `--fallback-model` only when the caller explicitly supplies one.
- Remove API-key, bearer-token, cloud-provider, custom-base-URL, and simple-mode environment overrides from the child process.
- Let Claude Code read and refresh its own Windows OAuth credential store; never copy that credential into Hermes state, receipts, prompts, or environment files.
- Keep subscription usage distinct from Anthropic API billing. API-key-backed Anthropic workers are a separate lane.

## Model selection

The live alias set is `default`, `sonnet`, `opus`, `haiku`, and `fable`. Full model IDs are accepted without rewriting. The adapter owns the legacy `claude-max-sonnet-opus` to `opus` compatibility mapping.

## Permission boundary

Use `dontAsk` for one-shot Hermes dispatches. Do not add automatic write, shell, network, or browser tools to a headless call without an explicit task-scoped policy. Do not use `--bare` for OAuth subscription dispatch: Claude Code documents that bare mode skips OAuth/keychain reads.

## Verification

Run `python scripts/prove-claude-code-oauth.py --all-models`, then refresh live capacity with `python scripts/refresh-hermes-capacity.py`. The verifier must show `auth_method=claude.ai`, no API-key source, and a successful marker for every selected alias.
