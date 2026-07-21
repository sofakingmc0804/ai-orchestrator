# Work Order: Flow-Meter Usage Accounting (No Agent Self-Report)

Created: 2026-07-10 (Cowork session, Claude)
Owner ruling: Matt, 2026-07-10. Agents must NOT spend tokens reporting their own
consumption. Usage and limits are measured at the pipe, like flow meters.
Status: QUEUED
Suggested lane: local-first (Ollama) for parsers; escalate only parser edge cases.

## Problem

Orchestrator-dispatched work leaves receipts. Interactive sessions (Claude
Cowork, Codex CLI, Gemini CLI) eat premium quota unmetered. The ledger cannot
allocate or prevent weekly spoilage while its largest consumers are invisible.

## Design: three meter classes, zero AI tokens spent on accounting

### Class A: vendor-written local artifacts (actuals, parse with a cron script)
- Claude Code / Cowork: session transcript JSONL under the user profile
  carries per-message usage token counts. Parser reads newest files only.
- Codex CLI: local session logs. VERIFY current format/location first (cheap
  local grep, do not trust this doc).
- Gemini CLI: local telemetry/logs. VERIFY format first.
- Ollama: response fields (prompt_eval_count, eval_count). Local lane is not
  quota-bound; meter it anyway for capacity planning.

### Class B: provider authority endpoints (remaining quota windows)
- Extend orchestrator/discovery/subscription_usage.py. It already stores
  rolling quota windows per provider without forcing token semantics. Add or
  verify probes for the current provider set, including the GPT-5.6 lineup.

### Class C: rejected: network interception (TLS proxy). Fragile, invasive,
  and Class A already contains actuals. Recorded so it is not re-proposed.

## Integration
- Parser runs in the existing budget_probes_cron lane (15-min cadence).
- Writes into existing usage tables (orchestrator/usage/accounting.py schema).
- Backfill: parse historical transcripts once, so past consumption is visible.

## Phase 2 (blocked by meters): anti-spoilage drain
Once remaining-window and reset-time are measured per provider, the scheduler
drains soon-to-expire surplus into the queued backlog with hard per-run caps.
Nothing runs aimless; surplus feeds the queue, shortage halts discretionary work.

## Acceptance (behavioral, spec-status culture)
A meter is passed only when a receipt row shows parsed actuals from a real
session artifact for that provider. Synthetic or hand-imported rows do not count.


## ADDENDUM 2026-07-10 ~12:50 CT (verified via CLI this session, supersedes Design where they conflict)

- Instruments ALREADY EXIST: token-flow, token-backfill, refresh-subscriptions,
  budget-status, budget, refresh-budget (orchestrator.cli.main). Do not rebuild. Extend.
- Verified readings 2026-07-10T17:48Z: openai_chatgpt 97.0% window left
  (provider_usage_window); google_gemini 100.0% left; github_copilot
  session_consumption_only (needs COPILOT_GITHUB_TOKEN/GH_TOKEN with Copilot
  Requests access; token exchange returned HTTP 404); anthropic_claude
  auth_required ("Claude credentials do not contain an OAuth access token");
  nous_hermes auth_required (remedy per probe: hermes auth add nous --type
  oauth, owner-approved lane only).
- token-flow last-50 dispatch window: 50 attempts, 42 successes, 2215 tokens
  total. Dispatch volume is small; interactive sessions remain the unmetered bulk.
- Remaining genuine gaps: (1) Anthropic seat metering, owner-lane auth or
  credential-source fix; (2) verify whether token-backfill already covers
  Cowork/Codex interactive transcripts before writing any parser; (3) Phase 2
  anti-spoilage drain still absent.
- fleet-recovery patrol (orchestrator\process\recovery.py, receipts under
  .runtime\orchestrator\fleet-recovery) wrote 94 receipts, last at
  2026-07-10T04:29Z, none since. Restart wiring unverified. Investigate why it
  stopped the same night .codex\hooks.json changed (2026-07-09 23:34 CT).
