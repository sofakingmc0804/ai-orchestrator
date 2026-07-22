# AI Orchestrator

A self-built multi-provider AI agent orchestration system. It routes work
across Claude, Codex, Gemini, Copilot, Ollama, and other local and cloud
providers, with budget-aware cost governance, skill-based routing and
permission gating, and an audit-receipt trail for every dispatch.

It exists to answer one question honestly at any time: *which provider should
do this piece of work, under what budget and safety constraints, and what
proof exists that it actually happened?* No single AI provider is treated as
a default; the router picks a worker based on specialization, budget state,
latency, and contract type, prefers local/subscription capacity over metered
usage, and avoids exhausted quotas. Every dispatch produces a receipt
(worker, job class, routing reasoning, budget state) so behavior can be
audited after the fact instead of taken on faith.

This is a working tool rather than a demo. It runs a real daily AI workload
across local and hosted models, and it has been shaped by what actually broke
in use rather than by what looked good in a diagram. Owner-specific values
(notification address, business domain, shared-drive root) are read from
`ORCHESTRATOR_*` environment variables, so the same codebase runs for a
different owner without code changes.

## What it does

- **Routes work across providers** — Claude, Codex, Gemini, Copilot, Ollama
  (local and cloud), LM Studio, and Hermes Agent, selected by job
  specialization, live budget/quota state, and latency.
- **Governs cost** — tracks per-provider budgets and quotas in real time and
  keeps a reserve on scarce resources rather than draining them to zero.
- **Gates and routes by skill** — a permission/skill-hook gate
  (`orchestrator/skills/gate.py`) decides what a given tool call or prompt is
  allowed to do before it runs, and a router
  (`orchestrator/skills/detector.py`) selects the matching skill for a
  prompt.
- **Keeps an audit trail** — every dispatch produces a receipt with the
  worker, job class, routing reasoning, and budget state at the time, so
  behavior is checkable after the fact (`orchestrator/dispatch/`).
- **Runs a local control plane** — a FastAPI service with dashboards for
  system health, workers, budget, and receipts, plus a CLI and Windows shell
  integration.

## Setup

Requires Python 3.13+.

```powershell
git clone <this-repo>
cd ai-orchestrator
pip install -e .
```

Owner-specific values are supplied through `ORCHESTRATOR_*` environment
variables and default to neutral placeholders. See
`orchestrator/skills/gate.py` and
`orchestrator/notifications/subscribers/email.py` for the full list.

## Usage

Start the service and open the local dashboard:

```powershell
python -m orchestrator.main
# http://127.0.0.1:8765/
```

Check the live control-plane state and CLI entry points:

```powershell
python -m orchestrator.cli.main dashboard-status --no-receipt
python -m orchestrator.cli.main --help
```

The `orchestrator` console script (installed by `pip install -e .`) is the
same CLI, e.g. `orchestrator dashboard-status` or `orchestrator status`.

For a guided walkthrough of the running system — health checks, the work
queue, discovery, and recovery — start with:

| Document | Use |
|----------|-----|
| [Quick Start](docs/guides/QUICK_START.md) | Check health, inspect the queue, discover source-backed work, and restore the local service. |
| [Instruction Manual](docs/guides/INSTRUCTION_MANUAL.md) | Understand discovery, routing, Hermes Desktop, validation, receipts, and scheduling. |
| [Operator Manual](docs/guides/OPERATOR_MANUAL.md) | Run the service, intervene safely, recover failed source tasks, and preserve evidence. |
| [Status](docs/STATUS.md) | Current, behaviorally-verified project status (canonical; supersedes the historical migration claims in `docs/HISTORY.md`). |

## Architecture

```
orchestrator/
├── adapters/          # Per-provider adapters (Claude, Codex, Gemini, Copilot, Ollama, ...)
├── governance/        # Job classifier, worker roster builder
├── routing/           # Worker-aware routing engine
├── dispatch/          # Dispatcher + receipts
├── discovery/         # Budget probes
├── skills/            # Skill routing and permission/hook gate
├── scheduler/         # Scheduled and recurring tasks
├── workspace_runtime.py, swarm/, evaluation/  # Multi-workspace + evaluation runtime
├── state/             # SQLite schema + store
├── ui/                # Dashboards (home, workers, budget, receipts, command center)
└── cli/               # Commands + aliases
data/rosters/          # Worker rosters
docs/                  # Guides, specs, migration/status history
tests/                 # Test suite
```

## Testing

```powershell
python -m pytest -q
```

As of this writing the suite has 2 known, pre-existing failures unrelated to
routine changes in this repo — both in `tests/test_skill_hook_gate.py`:
`test_codex_chrome_extension_prompt_selects_chrome_skill_and_lease` and
`test_router_benchmark_child_skill_prompts_select_expected_skill`. Both are
skill-router selection mismatches (the router returns a related but
differently-named skill than the test expects), not crashes or data-integrity
failures. They are tracked as known issues, not silently ignored.

## Project history

This project went through an internal two-system consolidation
(2026-06-10) and a later three-branch rebuild/cleanup pass (2026-07-21). The
full migration log, phase-by-phase claims, and superseded status banners are
preserved in [`docs/HISTORY.md`](docs/HISTORY.md) and `docs/migration/` rather
than at the top of this file. [`docs/STATUS.md`](docs/STATUS.md) is the
current, behaviorally-verified source of truth for project status.
