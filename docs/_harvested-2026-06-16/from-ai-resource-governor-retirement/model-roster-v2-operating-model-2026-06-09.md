# Model Roster V2 Operating Model

Date: 2026-06-09
Terminal state: produced

## Consequence

The current failure is not only cost visibility.

The failure is labor dispatch.

Hermes and the surrounding tools expose a giant list of possible workers, but they do not tell Matt which worker is good at which job, which payroll account covers the worker, when a request becomes overtime, or whether this Dell can run the worker locally without turning useful work into a furnace test.

The next governor must treat every model as a worker with a character sheet, a contract, a tool license, and a dispatch rule.

## Current State

- This Dell has limited VRAM. Local models must be treated as utility workers, not the main workforce.
- The first-generation governor exists at `C:\Users\Couch\.ai-resource-governor`.
- The governor has providers, models, quotas, receipts, repairs, and routing.
- The existing route logic is too shallow. It ranks billing class and capability tags, but it does not model task specialization, surface-specific access, latency, context, model behavior, tool reliability, or approval thresholds.
- The governor currently knows 22 models and 10 providers.
- The only live quota currently captured in SQLite is GitHub Copilot: 1,284 of 1,500 premium interactions remain, 85.6 percent, reset 2026-07-01T00:00:00Z.
- Codex account login status was broken by `service_tier = "default"` in `C:\Users\Couch\.codex\config.toml`; this was repaired to `service_tier = "flex"` and `codex.cmd login status` now returns logged in.
- Claude CLI is logged in through a first-party Claude account route, but this local probe does not expose remaining Claude subscription allowance.
- Hermes CLI status resolves to a safe local Ollama custom endpoint, but Hermes Desktop has a separate AppData-local config. This split-brain must be resolved before Hermes Desktop can be trusted as the dispatch console.
- Hermes is not logged into Nous Portal, and gateway tools are not configured.
- Ollama Cloud exposes plan/concurrency/usage-level concepts, but not a simple token allowance. Its unit is closer to GPU-time pressure than fixed tokens.

## Target State

The target is a workforce manager, not a model list.

The operating loop:

1. Classify the job.
2. Identify the required abilities.
3. Check hardware suitability.
4. Check the worker's contract.
5. Check remaining budget or quota.
6. Choose the cheapest competent worker that can finish cleanly.
7. Escalate only when the job value justifies the worker.
8. Write a receipt.

## Worker Card Schema

Each model-entry needs these fields:

```json
{
  "worker_id": "claude-sonnet-4.6@copilot",
  "base_model": "Claude Sonnet 4.6",
  "surface": "github-copilot",
  "provider": "Anthropic via GitHub",
  "contract_type": "subscription_quota",
  "salary_bucket": "Copilot premium interactions or AI credits",
  "overtime_rule": "blocked unless budget allows paid overage",
  "marginal_cost": {
    "unit": "provider_specific",
    "input_per_mtok": null,
    "output_per_mtok": null,
    "request_multiplier": 1
  },
  "remaining_budget_source": "gh api /copilot_internal/user",
  "hardware_fit": "cloud_only",
  "context_window": 1000000,
  "modalities": ["text", "code", "image_if_surface_supports_it"],
  "tools": ["chat", "agent", "edit", "mcp_if_surface_supports_it"],
  "stats": {
    "coding": 9,
    "agentic_loop": 8,
    "debugging": 8,
    "architecture": 7,
    "speed": 6,
    "structured_output": 7,
    "long_context": 9,
    "public_writing_fit": 5,
    "cost_pressure": 4,
    "stability": 7
  },
  "best_jobs": ["general coding", "agent tasks", "debugging with repository context"],
  "avoid_jobs": ["bulk extraction", "cheap classification", "tasks needing direct API receipts"],
  "approval_required": false,
  "source_evidence": ["official_docs", "local_probe", "observed_receipts"],
  "last_verified": "2026-06-09"
}
```

The same base model can appear multiple times because the surface changes the worker:

- `claude-sonnet-4.6@anthropic-api` is an independent contractor.
- `claude-sonnet-4.6@claude-app` is an employee under Claude subscription rules.
- `claude-sonnet-4.6@github-copilot` is a contractor billed through Copilot allowance or credits.
- `claude-sonnet-4.6@openrouter` is a contractor billed through OpenRouter, even if Matt separately pays Anthropic.

## Contract Types

- `local_resource`: Runs on this machine. No cash burn, but hardware and time are the budget.
- `subscription_unlimited`: Paid account surface where the practical limit is policy/rate/plan, not per-call billing. Still needs receipts.
- `subscription_quota`: Paid account surface with a monthly or rolling allowance. Treat the reserve as protected.
- `subscription_usage`: Paid account surface with included credits plus usage accounting. Needs live budget readback.
- `metered_extra_cost`: Direct API or marketplace billing. Overtime. Approval required.
- `third_party_metered`: A model already paid elsewhere, but this surface bills separately because of the route.
- `unknown_cost`: Block until classified.

## Job Classes

The router should classify the work before selecting a model:

- `routing_triage`: classify task, select tool, estimate risk.
- `bulk_extraction`: pull structured facts from many files or messages.
- `schema_validation`: check output shape and data integrity.
- `simple_coding`: small functions, one-file edits, syntax help.
- `repo_coding`: multi-file changes with tests.
- `agentic_repair`: tool loop, inspect/edit/run/verify.
- `deep_debugging`: logs, failures, race conditions, performance.
- `architecture`: system design, irreversible tradeoffs.
- `security_review`: adversarial review and high-consequence critique.
- `long_context_synthesis`: large documents, many receipts, big repo context.
- `visual_reasoning`: screenshots, diagrams, UI inspection.
- `ocr_document_intake`: image/PDF text extraction.
- `public_content`: external prose subject to Matt's public content law.
- `business_sensitive`: finance, legal, client, or account authority work.

## Dispatch Rules

Default rules:

- Local Dell models are utility workers only because VRAM is limited.
- Use local tiny models for routing, titles, cheap checks, OCR, and embeddings when they actually complete within acceptance windows.
- Use Copilot quota for coding surfaces where Copilot is the native authority and quota is above reserve.
- Use Codex account for repo repair, architecture, and high-value coding work when the work is in Codex.
- Use Claude subscription/app route for reasoning and critique when the task benefits from Claude's strengths and the surface is covered.
- Use Anthropic API only when direct API control, usage receipts, or automation require it and overtime is approved.
- Use OpenAI API only when direct API behavior, Responses/tools, usage accounting, or model availability requires it and overtime is approved.
- Use Ollama Cloud for open-model work that this Dell cannot run locally, especially coding/reasoning with open models, while respecting its GPU-time usage model.
- Use OpenRouter only as a marketplace fallback after proving no already-paid surface can do the job.

Escalation triggers:

- The task has high consequence.
- The cheaper worker produced an ambiguous or failed result.
- The work requires a larger context window.
- The work requires stronger tool behavior.
- The work requires a model only available through a specific surface.
- The expected rework cost exceeds the model cost.

De-escalation triggers:

- The job is repeatable, bulk, or low-risk.
- The output is schema-bound.
- The job can be checked deterministically.
- The worker is being used only to summarize or classify known facts.

## Initial Worker Interpretation

This is a starting roster interpretation, not the final measured benchmark.

- `GPT-5.3-Codex @ Copilot`: coding agent worker. Best for agentic software tasks inside Copilot surfaces. Not a generic writing or bulk extraction worker.
- `GPT-5.4 / GPT-5.5 @ OpenAI API`: high-end reasoning and professional work contractors. Use for deep debugging, architecture, and tool-capable workflows only when API spend is approved.
- `GPT-5.4 mini / nano @ OpenAI API`: cheaper contractors for subagents, structured tasks, and interactive coding support where OpenAI API is required.
- `Claude Sonnet 4.6`: senior generalist. Strong for coding, agents, critique, and complex reasoning. Contract depends entirely on surface.
- `Claude Opus 4.8 / 4.7 / 4.6`: principal architect/reviewer. Use for hardest reasoning, not chores.
- `Claude Haiku 4.5`: fast junior worker. Use for lightweight explanations, quick edits, and repetitive prompts.
- `Ollama Cloud qwen3-coder / glm / minimax / deepseek class models`: cloud open-model workforce. Good when local hardware cannot run the model and the job does not need proprietary account surfaces.
- `Local qwen2.5:0.5b`: dispatcher, title generator, tiny classifier.
- `Local qwen3.5:4b`: utility generalist if it finishes inside the time budget.
- `Local qwen2.5-coder:7b` and `deepseek-coder-v2`: only use when local performance is acceptable; otherwise they are theoretical workers on this PC.
- `Local OCR / embedding models`: useful because their jobs are bounded and measurable.

## Budget Ledger Requirements

Remaining budget cannot be guessed from model names.

Each provider needs a budget probe:

- OpenAI API: use the official Costs endpoint and Usage API; ChatGPT subscription is a different pool and does not automatically cover API calls.
- Anthropic API: use Claude Console cost/usage export or admin/analytics API where available; Claude app subscription is not the same as Anthropic API spend.
- GitHub Copilot: use GitHub's usage/entitlement surfaces; currently the local governor can read Copilot premium quota.
- Ollama Cloud: use account usage page and response metrics; plan usage is based on cloud utilization/GPU-time pressure, not fixed tokens.
- Hermes/Nous: use `hermes portal status`, `hermes portal tools`, and portal account state once logged in.
- OpenRouter and other marketplaces: always metered unless a specific prepaid balance and limit are proved.

If a budget source cannot be read, the provider's `remaining_budget_state` is `unproved`, and overtime is blocked.

## Hermes Customization Path

Do this in layers:

1. Resolve Hermes home/config split-brain.
2. Extend the governor schema to worker cards and job classes.
3. Generate a compact Hermes model catalog from the worker roster.
4. Prune Hermes visible models to approved workers.
5. Add a preflight command: `ai-route job --task <class> --surface hermes`.
6. Only then patch Hermes Desktop UI to show badges like `EMPLOYEE`, `OVERTIME`, `CODING`, `LONG_CTX`, `FAST`, `APPROVAL`.

Patching the Desktop first would decorate an untrusted picker.

## Completion Test

The system is not done until a user can ask:

> Which model should I use for this job?

and receive:

- chosen worker
- why this worker
- why the nearest alternatives lost
- salary/contract status
- remaining budget or quota state
- overtime approval requirement
- expected failure mode
- receipt path

## Sources Used

- OpenAI model and pricing docs: https://developers.openai.com/api/docs/models and https://developers.openai.com/api/docs/pricing
- OpenAI Usage and Costs API reference: https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage
- Anthropic Claude intro and pricing docs: https://platform.claude.com/docs/en/intro and https://platform.claude.com/docs/en/about-claude/pricing
- Claude Console cost and usage reporting: https://support.claude.com/en/articles/9534590-cost-and-usage-reporting-in-the-claude-console
- GitHub Copilot supported models, comparison, billing, and LTS docs: https://docs.github.com/en/copilot/reference/ai-models/supported-models, https://docs.github.com/en/copilot/reference/ai-models/model-comparison, https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing, https://docs.github.com/en/copilot/concepts/models/fallback-and-lts-models
- Ollama Cloud and usage docs: https://docs.ollama.com/cloud, https://ollama.com/pricing, https://docs.ollama.com/api/usage
