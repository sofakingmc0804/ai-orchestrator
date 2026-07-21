# Work Order: Reconsider TOKEN_BUDGET_ENGINE.md Against the GPT-5.6 Landscape

Created: 2026-07-10 (Cowork session, Claude)
Owner ruling: Matt, 2026-07-10. The 2026-07-05 draft predates the GPT-5.6
release and must be reconsidered before ratification. Ruling deferred until
the revision lands.
Status: QUEUED
Suggested lane: research on cheap lane; final revision draft reviewed by a
frontier model once; owner ruling last.

## What changed on 2026-07-09
- OpenAI GPT-5.6 family reached GA: Sol (flagship), Terra, Luna.
- API pricing per 1M tokens: Sol $5 in / $30 out, Terra $2.50 / $15,
  Luna $1 / $6. Sources verified 2026-07-10:
  https://openai.com/index/gpt-5-6/
  https://techcrunch.com/2026/07/09/openai-launches-its-new-family-of-models-with-gpt-5-6/
- Codex now runs the 5.6 family; the Codex seat on this machine is a 5.6-Sol
  worker with its own subscription windows.

## What ages with a model release (must be revised)
- Routing tables, cost thresholds, lane economics in the spec and in
  efficiency_policy.json derived material.
- Model roster docs and benchmarks dated 2026-06-07/06-09: STALE. Regenerate
  via tools/roster generators plus fresh operation benchmarks; a Luna-class
  lane at $1/$6 likely re-orders several dispatch decisions.

## What is roster-independent (candidate to survive review unchanged)
- The five conservation levers and the input-side waste analysis. Their
  argument does not depend on which vendor is frontier this week.

## Deliverable
Revised spec draft marked for owner ruling, plus regenerated roster with
behavioral evidence. No doctrine is adopted without Matt's explicit ruling.
