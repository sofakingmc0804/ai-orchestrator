# Consolidation Trail — Budget Probes
**File:** `orchestrator/discovery/budget_probes.py`, `orchestrator/scheduler/budget_probes_cron.py`  
**Date:** 2026-06-10  
**Phase:** 3 (Budget Probes)  
**Parent Trail:** `docs/migration/2026-06-10-phase3-execution.md`

---

## What Was Created

### Budget Probe Module (`orchestrator/discovery/budget_probes.py`)

Probes 6 providers for real-time quota state:

| Provider | Probe Type | Method |
|----------|-----------|--------|
| `github_copilot` | Copilot subscription | GitHub API (token-based) |
| `claude` | Anthropic API quota | Anthropic usage API |
| `codex` | OpenAI API quota | OpenAI usage API |
| `ollama` | Local availability | localhost:11434 health check |
| `gemini` | Google AI Studio quota | API key check (no usage API) |
| `nous` | Nous subscription | Hermes config check |

**Fallback Behavior:**
- No API key → assumes subscription active (unlimited)
- API error → marks probe failed, routing falls back to last known state
- Local services (Ollama) → checks availability, not quota

### Scheduler Integration (`orchestrator/scheduler/budget_probes_cron.py`)

- Runs every 15 minutes via scheduler
- Stores results in `budget_probes` table
- CLI command: `orchestrator cli refresh-budget` (manual trigger)

---

## Acceptance Criteria

- [ ] Probes run every 15 minutes
- [ ] All 6 providers probed
- [ ] Results stored in `budget_probes` table
- [ ] Routing checks fresh quota state
- [ ] CLI command `refresh-budget` works

---

**Executed By:** Hermes Agent  
**Phase:** 3 of 6
