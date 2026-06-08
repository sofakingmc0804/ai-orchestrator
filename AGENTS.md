# Codex — Example Consulting (Dell — Mobile + Supplementary)

## Context
This machine (DESKTOP-LOOCRQ2) is Matt Couch's mobile workstation.
It runs supplementary tasks when on, and is used for all interactive work.
See D:\SharedRoot\Workspace\AGENTS.md for full business context.

## This Machine's Role
- Runs 3 supplementary tasks when the machine is on (procurement-refresh, inbound-refresh, deep-research)
- Primary machine for interactive sessions, client work, development, Codex
- If this machine is off, MSI covers all core functions. Nothing breaks.
- Dell supplementary tasks do NOT create Gmail drafts (MSI only)
- Dell supplementary tasks write to DIFFERENT filenames than MSI tasks

## Supplementary Tasks (Dell)
| Task | Schedule | Purpose |
|------|----------|---------|
| procurement-refresh | 12PM CT weekdays | Afternoon re-check of procurement portals MSI scanned at 4AM |
| inbound-refresh | 3PM CT weekdays | Afternoon re-check of Gmail after MSI 6AM scan |
| deep-research | Tuesday 10AM CT | Extended research on queued SESSION_DECISIONS topic |

## Behavior
- Direct, factual, no hedging
- Evidence before conclusions
- Read before write (verify file exists before editing)
- Ask one specific question rather than building on wrong assumption
- Match Matt's pace and energy — short input = short output

## Paths
- Shared Drive: D:\SharedRoot\Workspace\
- Scheduled Tasks: C:\Users\Couch\Documents\Codex\Scheduled\
- Codex Config: C:\Users\Couch\.Codex\
- Projects: C:\Users\Couch\AI Projects Folder\
- Downloads: C:\Users\Couch\Downloads\

## Shared Drive Access
Desktop Commander may return empty on G:\ paths. Use PowerShell:
  Get-ChildItem "D:\SharedRoot\Workspace\[PATH]"
  Get-Content -Path "D:\SharedRoot\Workspace\[PATH]" -Raw

## Rules
- Never gmail_send to external addresses
- Never act as AR, collections, dunning, payment-chasing, or external billing operator. QBO/Gmail billing data may be used only as business evidence, project-context input, or owner-facing financial truth. Do not draft, send, queue, recommend, or optimize collection messages unless Matt explicitly requests that exact AR task in the current session.
- Dell supplementary tasks do NOT create Gmail drafts (MSI only)
- Verify file paths before citing (Test-Path)
- Label unverified claims as UNVERIFIED
- No cold outreach drafts to anyone
- Never frame Example as any single domain — use the connection principle

## AI Resource Governor
- Before nontrivial AI/model work, route the task through `C:\Users\Couch\.ai-resource-governor\bin\ai-route.ps1` or the governed `hermes`/`openclaw` shims.
- Default local work to Ollama models selected by `C:\Users\Couch\.ai-resource-governor\policy.json`; bulk extraction, classification, OCR, embeddings, and cheap validation must stay local unless the governor receipt allows escalation.
- Treat Copilot, Codex account routes, Claude Max, Gemini OAuth, and Ollama Cloud as subscription resources, not free air. Scarce quota keeps a 20 percent reserve unless the task is urgent and the receipt names the value reason.
- OpenAI API, Anthropic API, OpenRouter, direct Gemini API, and any unknown-cost provider are forbidden defaults. They stay disabled until `inventory.sqlite` classifies them as already-paid and `policy.json` allows them.
- Every model route needs a receipt under `C:\Users\Couch\.ai-resource-governor\receipts\` or an entry in `C:\Users\Couch\.ai-resource-governor\repair-queue.jsonl` naming the failed surface and next repair action.
- Trading platform operations are excluded from this governor until Matt explicitly reopens that lane.
