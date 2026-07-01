# Codex ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â Example Consulting (sole execution surface ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â DESKTOP-LOOCRQ2)
## About Matt And Business Fact Authority

Do not treat this file as authority for Matt's biography, public voice, credentials, brewing history, side projects, approved public wording, or current business facts. Use `C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\README_20260625.md` for the current review-and-approval lane for "About Matt" and related personal or public-facing claims.

Do not treat this file as standing authority for current Example rates, vendors, clients, certifications, registrations, GovCon status, staff facts, or project status. Resolve current Example business facts through current workspace files and business authority ledgers.

## Context
This machine (DESKTOP-LOOCRQ2) is the SOLE execution, authority, and state
surface for Example's operating system (ADR-028, 2026-04-16). There is no MSI and
no second machine. The workspace you are working in is the authority for its own work (this ai-orchestrator repo included); `example-rebuild` is dormant and is not the authority;
`D:\SharedRoot\Workspace\` is OUT of the control plane ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â never read
or write it as part of a runtime contract.

## This Machine's Role
- Runs every Example scheduled task. The prior MSI-core / Dell-supplementary split is retired.
- Primary machine for interactive sessions, client work, development, Codex, OpenClaw local inference.
- Sole authority and state surface: local filesystem + local Git are the only source of truth.

## Scheduled Tasks (this machine)
All scheduled tasks run here. The canonical task inventory is
`C:\Users\Couch\example-rebuild\TASKS.yaml` ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â read there for the current set and
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
- Match Matt's pace and energy ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â short input = short output

## Paths
- Shared Drive (D:\SharedRoot\Workspace\): OUT of control plane ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â manual human deliverable copies only, never a runtime contract.
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
- Never frame Example as any single domain ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â use the connection principle

## AI Resource Governor (Consolidated into AI Orchestrator)
- **New Location:** `C:\Users\Couch\dev\ai-orchestrator\`
- **Legacy Shim:** `.ai-resource-governor/bin/ai-route.ps1` ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ delegates to orchestrator
- Before nontrivial AI/model work, route through `python -m orchestrator.cli.main route` or the governed `hermes`/`openclaw` shims (updated to use orchestrator backend).
- Default local work to Ollama models selected by orchestrator's budget probes; bulk extraction, classification, OCR, embeddings, and cheap validation stay local unless budget allows escalation.
- Treat Copilot, Codex account routes, Claude Max, Gemini OAuth, and Ollama Cloud as subscription resources. Scarce quota keeps 20% reserve unless urgent.
- Metered/unknown-cost providers disabled until inventory classifies as already-paid and policy allows.
- Every dispatch recorded in receipts with worker_id, job_class, routing_reasoning, budget_state.
- Trading platform operations excluded until explicitly reopened.
- **Status:** See `dev/ai-orchestrator/docs/STATUS.md` (canonical) and `python -m orchestrator.cli.main spec-status` for real, behaviorally-proven status. The 2026-06-10 migration docs are historical only.

<!-- AI-GOVERNANCE-BLOCK BEGIN (generated; do not edit between markers) -->
## Governed AI Policy (generated 2026-06-29 from ai-orchestrator efficiency_policy.json v1)
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

<!-- ABOUT_MATT_LOADER_HANDOFF BEGIN -->

## About Matt Generated Context Handoff

Before using Matt biography, brewing history, Example role/title facts, Example Co details, credentials, side projects, public voice, or personal/business identity:

1. Check C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\receipts\about_matt_freshness_check.json.
2. If freshness is not PASS, run C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\scripts\run_about_matt_refresh.ps1.
3. Read the truth-source map at C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\generated\about_matt_truth_source_map.md or .json before deciding what any source proves.
4. Load approved facts from C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\generated\about_matt_agent_context_bundle.md or .json.
5. Load do-not-use guidance from C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\generated\about_matt_deprecated_claim_ledger.md or .json.
6. If a claim is absent from the generated bundle, keep it source-scoped and route to C:\Users\Couch\Documents\Codex\ABOUT_MATT_AUTHORITY\reviews\about_matt_owner_priority_questions_20260625.md.

Education influences About Matt context; it does not dictate the complete profile. Do not promote raw Education evidence, generated helper artifacts, old Claude session skills, profile prose, cached plugin files, or memory snippets into durable biography.

<!-- ABOUT_MATT_LOADER_HANDOFF END -->



