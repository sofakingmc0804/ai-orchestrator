# Hermes Claude Code CLI headless/OAuth standard

## Operating contract

Hermes' Claude dispatch adapter uses the installed Claude Code CLI as the
execution surface for the owner's Claude.ai subscription. Claude Code remains
the owner of its provider-issued OAuth credentials and refresh lifecycle. The
dispatch and verification paths do not read, copy, print, or persist those
credentials.

The Hermes adapter launches one-shot work with:

- `claude -p`
- `--no-session-persistence`
- `--output-format json`
- `--permission-mode dontAsk`
- `--model <alias-or-full-model-id>`
- optional `--fallback-model <alias-or-full-model-id>`

Hermes intentionally does not add `--bare`: Claude Code documents that bare
mode skips OAuth/keychain reads, which would defeat the persisted subscription
login needed by this adapter.

The child environment removes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`CLAUDE_CODE_OAUTH_TOKEN`, provider-routing variables, and simple mode. This is
required because Claude Code gives API-key environment variables precedence in
non-interactive mode. Removing them makes the persisted Claude.ai OAuth login
the active credential for every Hermes Claude subscription dispatch.

## Model selection

The live CLI aliases admitted to the Hermes roster are:

- `default`
- `sonnet`
- `opus`
- `haiku`
- `fable`

The adapter also accepts a full Claude model ID without an allowlist rewrite.
The legacy Hermes worker id `claude-max-sonnet-opus` normalizes to `opus` for
CLI execution. The selected model remains visible in the dispatch receipt.

## Capacity and verification

`claude auth status` is checked in the same OAuth-only environment used for
dispatch. This proves the provider-owned login and admits the model aliases as
`subscription_quota` workers. The provider's usage window remains provider
owned; Hermes must not turn a successful login into an invented token balance.

Run the live verifier when a fresh proof is needed:

```powershell
python scripts/prove-claude-code-oauth.py --all-models
```

Run the capacity reconciler afterward to refresh the worker roster and receipt:

```powershell
python scripts/refresh-hermes-capacity.py
```

## Boundary

This integration is for the owner's local Hermes runtime. It must not expose
the Claude.ai login or route subscription credentials through a service for
other users. API-key-backed Anthropic work remains a separate provider lane.

Reference: [Claude Code headless mode](https://code.claude.com/docs/en/headless),
[Claude Code authentication](https://code.claude.com/docs/en/authentication),
[Claude Code model configuration](https://code.claude.com/docs/en/model-config),
and [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance).
