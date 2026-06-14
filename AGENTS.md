# Codex â€” Example Consulting (sole execution surface â€” DESKTOP-LOOCRQ2)
## Context
This machine (DESKTOP-LOOCRQ2) is the SOLE execution, authority, and state
surface for Example's operating system (ADR-028, 2026-04-16). There is no MSI and
no second machine. The workspace you are working in is the authority for its own work (this ai-orchestrator repo included); `example-rebuild` is dormant and is not the authority;
`D:\SharedRoot\Workspace\` is OUT of the control plane â€” never read
or write it as part of a runtime contract.

## This Machine's Role
- Runs every Example scheduled task. The prior MSI-core / Dell-supplementary split is retired.
- Primary machine for interactive sessions, client work, development, Codex, OpenClaw local inference.
- Sole authority and state surface: local filesystem + local Git are the only source of truth.

## Scheduled Tasks (this machine)
All scheduled tasks run here. The canonical task inventory is
`C:\Users\Couch\example-rebuild\TASKS.yaml` â€” read there for the current set and
cadence. Examples in rotation (verify against TASKS.yaml):
| Task | Schedule | Purpose |
|------|----------|---------|
| procurement-refresh | 12PM CT weekdays | Re-check of procurement portals |
| inbound-refresh | 3PM CT weekdays | Re-check of Gmail inbound |
| deep-research | Tuesday 10AM CT | Extended research on a queued SESSION_DECISIONS topic |

## Behavior
- Direct, factual, no hedging
- Evidence before conclusions
- Read before write (verify file exists before editing)
- Ask one specific question rather than building on wrong assumption
- Match Matt's pace and energy â€” short input = short output

## Paths
- Shared Drive (D:\SharedRoot\Workspace\): OUT of control plane â€” manual human deliverable copies only, never a runtime contract.
- Scheduled Tasks: C:\Users\Couch\Documents\Codex\Scheduled\
- Codex Config: C:\Users\Couch\.codex\
- Projects: C:\Users\Couch\AI Projects Folder\
- Downloads: C:\Users\Couch\Downloads\

## Shared Drive (out of control plane)
`D:\SharedRoot\Workspace\` is not a runtime surface (ADR-028). Do
not read it for state/direction or write it as part of any runtime contract. The
only sanctioned tooling touch is Git bundle-export, which Git treats as a Git
object. The Website repo under `G:\...\Website Domain and Hosting` is governed by
its own project CLAUDE.md / change-control contract when a human works there.

## Rules
- Never gmail_send to external addresses
- Never act as AR, collections, dunning, payment-chasing, or external billing operator. QBO/Gmail billing data may be used only as business evidence, project-context input, or owner-facing financial truth. Do not draft, send, queue, recommend, or optimize collection messages unless Matt explicitly requests that exact AR task in the current session.
- Automated/scheduled tasks must not create or send Gmail drafts to external addresses.
- Verify file paths before citing (Test-Path)
- Label unverified claims as UNVERIFIED
- No cold outreach drafts to anyone
- Never frame Example as any single domain â€” use the connection principle

## AI Resource Governor (Consolidated into AI Orchestrator)
- **New Location:** `C:\Users\Couch\dev\ai-orchestrator\`
- **Legacy Shim:** `.ai-resource-governor/bin/ai-route.ps1` â†’ delegates to orchestrator
- Before nontrivial AI/model work, route through `python -m orchestrator.cli.main route` or the governed `hermes`/`openclaw` shims (updated to use orchestrator backend).
- Default local work to Ollama models selected by orchestrator's budget probes; bulk extraction, classification, OCR, embeddings, and cheap validation stay local unless budget allows escalation.
- Treat Copilot, Codex account routes, Claude Max, Gemini OAuth, and Ollama Cloud as subscription resources. Scarce quota keeps 20% reserve unless urgent.
- Metered/unknown-cost providers disabled until inventory classifies as already-paid and policy allows.
- Every dispatch recorded in receipts with worker_id, job_class, routing_reasoning, budget_state.
- Trading platform operations excluded until explicitly reopened.
- **Status:** See `dev/ai-orchestrator/docs/STATUS.md` (canonical) and `python -m orchestrator.cli.main spec-status` for real, behaviorally-proven status. The 2026-06-10 migration docs are historical only.

<!-- AI-GOVERNANCE-BLOCK BEGIN (generated; do not edit between markers) -->
## Governed AI Policy (generated 2026-06-12 from ai-orchestrator efficiency_policy.json v1)
Authority: C:\Users\Couch\dev\ai-orchestrator\ (vendor-neutral). Full rigor rules:
docs\specs\epistemic-rules.md (claims/sources/contradictions/options) and
docs\specs\artifact-rules.md (predecessor retirement) â€” load on consequential work.
Dispatch: cheapest competent worker; premium models only for architecture, hard code repair,
high-stakes decision, long-context synthesis, final critique, security review.
- Read existing project docs and memory before any external research; research only genuine gaps.
- Locate with search, then read only the needed section; never re-read content already in context.
- Filter all command output; never dump logs, listings, or raw responses.
- Execute the stated directive only; note adjacent opportunities in one line without executing.
- Answer first; no preamble, no recap, no self-summary; never echo written file contents into chat.
- Edit in place with targeted diffs; one canonical document per topic; retire predecessors in the same action that creates successors.
- Deterministic work (count, sort, diff, transform, math) goes to a script; only results enter context.
- A failed approach changes or stops after one retry; no identical retry loops.
- No hard-coded temporal state in durable artifacts; compute dates at runtime; date-stamp volatile facts.
- Cheapest adequate tool: API over DOM over screenshots; small model over large; script over model.
- One session per task; persist durable conclusions to the canonical doc, then end clean.
- Plans exceeding two non-read tool calls require explicit owner approval before execution.
<!-- AI-GOVERNANCE-BLOCK END -->

