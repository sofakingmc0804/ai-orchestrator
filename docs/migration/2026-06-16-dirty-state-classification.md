# Dirty State Classification

Date: 2026-06-16 local / 2026-06-17 UTC

## Consequence

The worktree still contains unrelated dirty files. Mixing them into the governor retirement or Hermes auth repair would hide the mechanism and make rollback unsafe.

## Classification

`runtime-generated, do not stage for this repair`

- `.runtime\ai-resource-governor\receipts\**`
- `.runtime\orchestrator\ORCHESTRATOR_OUTPUT\**`
- `.runtime\orchestrator\adapter-proof\**`
- `.runtime\orchestrator\acceptance\**`
- `.runtime\orchestrator\owner-receipts\**`
- `.runtime\orchestrator\receipts\**`
- `.runtime\orchestrator\skill-hooks\**`
- `.runtime\orchestrator\supervisor\**`
- `.runtime\p1_3\**`
- `.runtime\p3_2_hermes_shim_output.txt`
- `.runtime\p5_hook_live\**`

Mechanism: these are execution receipts, proofs, outputs, and state created by previous test and acceptance runs. They are evidence surfaces, not hand-authored source for this repair.

`legacy-runtime, now retired outside Git`

- `.runtime\ai-resource-governor\bin\*.ps1`
- `.runtime\ai-resource-governor\bin\hermes.cmd`
- `.runtime\ai-resource-governor\scripts\refresh-governor.ps1`

Mechanism: these are repo-local legacy runtime shims. The live home at `C:\Users\Couch\.ai-resource-governor` is now retired, and the scheduled task that called the repo-local refresh script has been removed. These files are not part of the current auth repair commit unless a later cleanup sprint explicitly removes the repo-local compatibility surface.

`fixture-edits, leave with prior acceptance work`

- `benchmarks\fixtures\operations\first_pack.json`
- `data\benchmark_fixtures\first_pack.json`

Mechanism: these are benchmark fixture changes tied to comparative/evaluation work, not to governor retirement or Hermes auth.

`skill-hook-source, leave with prior consequence-enforcement work`

- `orchestrator\skills\detector.py`
- `orchestrator\skills\gate.py`
- `orchestrator\skills\runtime.py`
- `tests\test_skill_hook_gate.py`
- `AGENTS.md`

Mechanism: these files implement or exercise the consequence/skill-hook enforcement path. They should be reviewed and committed with that acceptance battery, not folded into the retirement patch.

`adapter-governance-source, leave with prior routing/governance work`

- `orchestrator\adapters\builtins.py`
- `orchestrator\config\governance_policy.json`
- `orchestrator\process\recovery.py`
- `tests\test_agent_adapters.py`

Mechanism: these belong to provider adapter, OpenClaw/Hermes, and governance behavior. They are adjacent to the auth issue but are not evidence of this retirement action.

`spec/doc leftovers, classify before cleanup`

- `docs\specs\EPISTEMIC_CONSTRAINT_SYSTEM_v1.2_archived-2026-06-12.md`
- `docs\specs\cowork-core-v2.md`
- `enforcement\epistemic\engine.ps1.20260615T001014.bak`
- `scripts\apply-cowork-core.ps1`

Mechanism: these look like generated or migrated spec-enforcement artifacts from prior P7/consequence work. They should be resolved by the Phase/F1-F8 acceptance cleanup, not by the governor retirement.

`current-repair, safe to stage`

- `.gitignore`
- `docs\migration\2026-06-16-ai-resource-governor-retirement.md`
- `docs\migration\2026-06-16-dirty-state-classification.md`
- `docs\hermes\NOUS_AUTH_REPAIR_2026-06-16.md`
- `docs\_harvested-2026-06-16\from-ai-resource-governor-retirement\**`

Mechanism: these are the retirement/auth receipts and harvested evidence produced by this repair.

## Road Through

Stage only `current-repair` files for this commit. Leave every other dirty bucket untouched until its owning sprint or cleanup action claims it.
