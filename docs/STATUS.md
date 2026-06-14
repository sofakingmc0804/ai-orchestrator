# AI Orchestrator — Status (canonical)

**This is the single source of truth for project status.** It supersedes every
"100% complete / all 6 phases" claim in `README.md`, `CONSOLIDATION_STATUS.md`,
`docs/MIGRATION_COMPLETE.md`, and `docs/SNAPSHOT_FINAL.md` (all retained only as
dated historical migration logs).

## How status is measured (the honest rule)

Status is **behavioral, not asserted**. The live scoreboard is:

```
python -m orchestrator.cli.main spec-status
```

As of 2026-06-13 (Phase 0.1) a capability target is "passed" only when it is
backed by **real, re-runnable evidence**, never a row count or a file existing
on disk:

- A dispatch is "proven" only when a receipt carries `proof_kind='live'` with
  persisted `raw_output` from a real adapter call (`orchestrator/spec_status.py`,
  `store.list_live_proven_dispatches`). Hand-imported and synthetic rows do not
  count.
- Selection capture is "proven" only on a real selection→dispatch round trip,
  not because a `.ps1`/`app.js` file exists.

Run `spec-status` for the current numbers. Do not quote a fixed percentage here —
it would go stale and re-introduce the exact dishonesty this file exists to end.

## What is actually real today (verified by audit, 2026-06-13)

- **Dispatch is genuine.** `orchestrator/adapters/builtins.py` makes real
  HTTP/subprocess calls (Ollama `127.0.0.1:11434`, `claude -p`, `codex exec`,
  `hermes -z`, etc.). Local Ollama produces real model output end to end.
- **Routing intelligence is real and wired:** dynamic 96-worker roster, job
  classifier, worker-aware scoring (`orchestrator/routing/worker_routing.py`).
- **Live quota probing is real** (`orchestrator/discovery/subscription_usage.py`:
  authentic OAuth usage windows for ChatGPT/Claude/Gemini) — but see gaps.
- **159 tests pass**, exercising internal logic against tmp SQLite. They mock
  every external model boundary; no test proves a real provider call.

## Known gaps (tracked in the build plan)

- **Live quota is bypassed by routing** — the router still reads the placeholder
  `budget_probes` table (`999999` sentinels), not the real snapshots. (Plan P1.1)
- **The primary FastAPI UI does not run** — `on_event` is removed in the
  installed FastAPI/Starlette; `main.py` falls back to a read-only server. The
  interactive `app.js` is orphaned. (Plan P1.3)
- **Quality is inferred, not measured** — worker stats come from model
  reputation, not head-to-head comparative accuracy. (Plan P2)
- **No always-on process; the skill-hook gate is advisory only.** (Plan P4/P5)
- **`spec-status` still has soft passes** (some targets pass on module/file
  existence — CT-19/CT-20/etc.). Honesty-hardening is Plan P7.

## Direction

This project is being built into an **AI operating system**: Hermes Agent is the
kernel/shell; this repo is the vendor-neutral brain it consults (capability +
measured quality, live quota, failover, governance). Full plan and finished-state
definition: `C:\Users\Couch\.claude\plans\make-the-end-to-end-plan-linked-widget.md`.
