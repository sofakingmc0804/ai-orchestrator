# Consequential Reasoning Enforcement — Spec + Implementation Plan v1

Generated: 2026-06-14
Authority: `C:\Users\Couch\dev\ai-orchestrator\` (vendor-neutral)
Status: PLAN — every action below is proposed and owner-gated; nothing here is executed by the act of writing this doc.

## Supersession

This doc supersedes the prose-format approach to reasoning enforcement:
- `docs/specs/EPISTEMIC_CONSTRAINT_SYSTEM_v1.2_archived-2026-06-12.md` (already archived).
- The live `epistemics-gate.ps1` / `enforcement/epistemic/engine.ps1` EPISTEMIC_BLOCK scanner and its Codex/Gemini/Hermes adapters (retired by Action 2 below).

The predecessor tried to ensure reasoning by requiring a text block. That is retired here for cause, stated in §1.

---

## 1. Principle — why format enforcement produced nothing, and what "consequence" means

A model's reasoning is *ensured* only when a flaw in it causes a detectable failure that **blocks completion** and forces revision. The blocking signal has to originate **outside the model's own text**, because:

- An EPISTEMIC_BLOCK is generated *after* the conclusion is already formed. It is a rationalization of a decision already made, not the reasoning that made it.
- "Done" stays the model's own self-assessment. A model cannot reliably grade the reasoning that just produced its answer — the same process that made the error evaluates the error (the self-correction / addressability problem).

Verified instance of the failure mode in this repo: `enforcement/epistemic/engine.ps1` blocks a response only when it contains a "recommendation" marker and lacks the literal string `## epistemic_block`, then instructs the agent to paste a template "BEFORE your recommendation." It checks for the presence of prose. It checks nothing about reality. The agent satisfies it by typing the template. Zero consequence.

**Consequence test for every mechanism in this plan:** *can a flaw produce a failure the model cannot author its way out of?* If the gate can be satisfied by writing the right words, it is theater. If the gate is satisfied only by reality (an exit code, a file that exists, a source that matches), it is consequence.

---

## 2. Ground truth — primitives that actually exist on this machine

Every row verified by reading the file on 2026-06-14. Paths are real.

| Primitive | Path | What it does | Verified weakness |
|---|---|---|---|
| Claude Code Stop chain | `~/.claude/settings.json` lines 93–114 | 3 Stop hooks: `validate-session-output.ps1`, `verify-completion.ps1`, `epistemics-gate.ps1` | last one is prose-scan theater |
| **Completion gate (real)** | `~/.claude/scripts/verify-completion.ps1` | Blocks Stop (exit 2) until Ralph state has `verified=true` AND (`testsRan`+`testResult`) OR (`buildRan`+`buildResult`) | trusts whatever result string was recorded |
| **Verification recorder (the hole)** | `~/.claude/scripts/update-state.ps1` action `ralph-verify`, lines 665–704 | Sets `testsRan = (cmd!="" -and result!="")`, `testResult = $RalphTestResult` verbatim | **never executes the command — records the agent's claim** |
| Ralph activation | `update-state.ps1` action `ralph-activate` | Opt-in per task; sets `active=true`, max iterations, resets verification | gate only runs when a task activates it |
| Governor | `~/.ai-resource-governor/ai_governor.py` via `.runtime/ai-resource-governor/bin/ai-route.ps1`; `policy.json` | Cost / model-selection router for `hermes_cli_model_call` and `openclaw_cli_model_call` | **not** a reasoning proxy; **not** in the Claude/Codex/Gemini request path |
| Codex completion | `~/.codex/config.toml`: `model_reasoning_effort="xhigh"` (l.2); `[[hooks.Stop]]`→`codex-stop.ps1` (l.204–209) | reasoning floor already at ceiling; Stop hook runs EPISTEMIC_BLOCK scan | the Stop hook is theater |
| Gemini completion | `~/.gemini/settings.json` `AfterAgent`→`gemini-afteragent.ps1` | runs EPISTEMIC_BLOCK scan after each agent turn | theater |
| Prose-scan engine | `enforcement/epistemic/engine.ps1` | `Get-EpistemicVerdict`; blocks on recommendation-without-`## epistemic_block` | scans text, checks no reality |
| Reality-check MCP | `example-enforcement` tools `verify_file_exists`, `verify_url`, `verify_claim` | registered and callable | **implementation UNVERIFIED — source not found on disk; do not rely until §Action 4a proves it checks reality** |

---

## 3. Architecture mapped to this machine

Three layers, ordered by how much real consequence each carries *on this stack* (not in the abstract):

- **Layer 1 — reality-grounded done-criteria (load-bearing).** The only in-path, consequential completion surface that exists today is the Ralph loop (`verify-completion.ps1` + the recorder). It already blocks Stop. It just needs its verification to come from execution instead of self-report. This is where the work is.
- **Layer 2 — forced reasoning compute (mostly already done, weak, bounded reach).** Codex is already `xhigh`. There is **no proxy** in front of Claude Code / Gemini, so there is no honest universal-injection action available without building infrastructure that does not exist. The governor reaches only local Hermes/openclaw calls. Per the non-monotonic evidence (more thinking helps hard tasks, hurts easy ones; wrong answers average *longer* chains), "high" is the defensible default and a universal "max" is not a goal. This layer is necessary-not-sufficient and is not where consequence lives.
- **Layer 3 — independent bounded critic (real, selective, fallible).** For high-stakes claims with no mechanical check. The governor already reserves premium models for "final critique" and "security review" (`policy.json`), so there is a real hook. Costly (Verifier Tax ~2–2.8×); apply selectively.

---

## 4. Implementation — actions of consequence

Each action names the exact file, the change, and a **falsifiable acceptance test** — a check that fails if the action did not produce real consequence. Order is §5.

### ACTION 1 (load-bearing) — completion gate verifies execution, not self-report

**Problem closed:** today an agent satisfies the gate with `update-state.ps1 -Action ralph-verify -RalphTestCommand "pytest" -RalphTestResult "pass"` without running anything.

**Change:**
1. Add action `ralph-verify-exec` to `update-state.ps1`. It takes `-RalphTestCommand` (and optional `-RalphBuildCommand`) **only — no result parameter.** It executes each command in the project root with a hard timeout (e.g. 600 s), captures the real `$LASTEXITCODE` and the last ~40 lines of output, and records:
   - `testResult = "pass"` iff exit code 0, else `"fail:<code>"`,
   - `verifiedBy = "execution"`,
   - `exitCode`, `outputTail`, `verifiedAt`.
   The agent supplies the *command*; reality supplies the *result*.
2. Harden `verify-completion.ps1`: the gate allows Stop only when `verificationData.verifiedBy -eq "execution"` AND the recorded `testResult`/`buildResult` indicates exit 0. A hand-recorded result (no execution stamp) no longer satisfies the gate.
3. Deprecate the self-report `ralph-verify` path: it may still write telemetry, but its records carry `verifiedBy = "self-report"` and **cannot** satisfy the hardened gate.

**Acceptance (must all hold, or Action 1 failed):**
- (a) Activate a Ralph loop; run `ralph-verify-exec` with a command that exits non-zero (e.g. `cmd /c exit 1`, or a known-failing test) → `verify-completion.ps1` **blocks Stop** and feeds back the real failure tail.
- (b) Same with a command that exits 0 → gate **allows** Stop.
- (c) Call the old `ralph-verify -RalphTestResult "pass"` with no execution → gate **still blocks** (no `verifiedBy=execution`).
- If (a) or (c) allows Stop, the gate is still theater — revert and redo.

**Limit:** only meaningful for tasks with a runnable check. Tasks without one fall to Action 4/5. The agent still chooses the command — a deliberately trivial command (`exit 0`) passes; this raises the floor (you must name and run *something* real and it must pass) without claiming to prove the task is correct. That honest boundary is the point.

### ACTION 2 — retire the EPISTEMIC_BLOCK prose-scanner everywhere it is wired

A gate that enforces text produces false assurance and burns tokens emitting a template the model wrote after deciding. Removing it is itself consequential: it stops theater from being mistaken for enforcement.

**Change (each is a config/file edit with a named restore point):**
- `~/.claude/settings.json`: remove the `epistemics-gate.ps1` entry from the `Stop` chain (keep `validate-session-output.ps1` and the Action‑1‑hardened `verify-completion.ps1`).
- `~/.codex/config.toml`: remove the `[[hooks.Stop]]` block (l.204–209) calling `codex-stop.ps1`.
- `~/.gemini/settings.json`: remove the `AfterAgent` epistemic hook.
- `enforcement/epistemic/engine.ps1`: neutralize `Get-EpistemicVerdict` to always return `allow` (retain an append-only audit line for observability only), or delete it and its adapters (`codex-stop.ps1`, `gemini-afteragent.ps1`, `hermes-stop.ps1`). Observability ≠ enforcement; it must never block.

**Acceptance:**
- Grep the three live configs → no epistemic-block enforcement wired.
- A response that makes a recommendation with no `## EPISTEMIC_BLOCK` triggers **no** block in any tool.
- If an audit logger is kept, confirm it only writes a log line and returns exit 0 / `allow`.

### ACTION 3 — lock the reasoning-compute floor where it is in-path; document where it is not

**Change / verification (mostly already true — this action is mostly proof, not edits):**
- Codex: confirm `model_reasoning_effort = "xhigh"` in `config.toml`. (Verified present 2026-06-14.) Acceptance: read the file → `xhigh`.
- Claude Code: there is no proxy in its request path on this machine. **Do not claim injection.** The honest lever is session-level (the operator selecting extended thinking when the task is hard). No fake proxy is created.
- Governor: optionally add a `think`/reasoning flag to `ai_governor.py` routing for `hermes_cli_model_call` / `openclaw_cli_model_call`. Marked OPTIONAL, low value (reaches only local models).

**Acceptance:** Codex reads `xhigh`; no doc or script in the repo asserts a universal reasoning-injection proxy that does not exist. (A claim of nonexistent infrastructure is itself a defect this plan forbids.)

**Honest framing:** this layer is largely in place where reachable and unreachable elsewhere without new infrastructure. It is not where consequence lives. It raises the floor of effort; it does not make reasoning correct.

### ACTION 4 (conditional) — wire reality-checks for file/claim done-criteria, only after proving they are real

**4a. Verify the `example-enforcement` tools actually check reality.** Call `verify_file_exists` with one path that exists and one that does not; call `verify_url` with one URL that resolves and one that 404s. Confirm the boolean flips with reality. If it does not flip, the tool is theater — record that finding and stop here.

**4b (only if 4a passes).** For file-delivery tasks, the done-gate calls `verify_file_exists` on the claimed output path; Stop stays blocked unless it returns true. This operationalizes the existing `CLAUDE.md` Rule 5 ("Test-Path must return True before claiming delivery") as a gate instead of a guideline.

**Acceptance:** a task claiming delivery of a path that does not exist → blocked; a real path → allowed. If 4a fails, Action 4 is dropped, not faked.

### ACTION 5 (selective) — independent, bounded soundness critic for ungrounded high-stakes claims

For claims with no mechanical check (a design judgment, a strategy, a security posture). Fallible; use where Action 1/4 cannot reach.

**Change:** a critic invocation (routed through the governor's existing premium "final critique" reason) that:
- receives **claim + cited evidence, NOT the generator's chain-of-thought** (independence is the whole point),
- judges against explicit, falsifiable criteria; emits a **specific** objection ("claim X contradicts source Y at Z"), never "improve this,"
- is **bounded to ≤3 revisions**, then emits `UNCERTAIN` rather than looping,
- runs only at checkpoints flagged high-stakes (Verifier Tax ~2–2.8×).

**Acceptance:** a claim that contradicts its own cited source → critic returns a specific objection; the loop never exceeds 3 cycles; on the 3rd unresolved cycle it returns `UNCERTAIN`, not another pass. Multi-agent debate is explicitly **not** used (its ceiling is the strongest single agent and it decays to an echo chamber).

---

## 5. Sequencing, gating, reversibility

**Order:** Action 1 (build the real gate) → Action 2 (remove the fake gate) → Action 3 (lock floor / delete false claims) → Action 4 (verify then wire reality-checks) → Action 5 (selective critic). Action 1 before 2 so a real gate exists before the theater is removed.

**Gating:** each action is a separate owner-approved execution. This document is the plan; per the standing rule, plans exceeding two non-read tool calls require explicit approval before execution.

**Reversibility (mandatory before any edit):**
- `git -C ~/.claude commit -am "pre-consequence-enforcement snapshot"` (restore point for all `~/.claude` edits).
- Copy `config.toml`, `~/.gemini/settings.json`, and `engine.ps1` to timestamped `.bak` files before editing (not all are git-tracked).
- Confirm each `.bak` exists (Test-Path) before touching the original.

---

## 6. What this does NOT do (honest limits)

- It cannot force correct reasoning on open-ended tasks that have no ground truth. It forces a *runnable check to exist and pass*; it does not prove the check is sufficient.
- It cannot inject reasoning parameters into subscription-locked surfaces (Copilot, chat UIs) or into Claude Code, because no proxy sits in those paths here.
- Self-reported anything is not consequence. Only execution-derived results (exit codes, file existence, source match) count toward "done."
- The Layer-3 critic is fallible (LLM-judge biases) and costly. It is a check, not an oracle.
- The agent still authors the test command in Action 1. The floor is "you must run something real and it must pass," not "the task is provably correct." Stated plainly so no one mistakes the floor for a ceiling.

---

## 7. System-level acceptance — the whole thing is "real" iff

1. A deliberately failing test **blocks completion** in at least one wired tool (Claude Code Ralph loop), proven by observation, not by reading code.
2. No prose-format gate remains wired in any tool (`settings.json`, `config.toml`, `.gemini/settings.json`).
3. Every "pass" recorded in Ralph state carries `verifiedBy = "execution"` with an exit code — no hand-typed result can satisfy the gate.

If any of the three fails, the system is back to theater and the failing action is redone.

---

## Evidence log (files read to ground this plan, 2026-06-14)

- `~/.claude/settings.json` — Stop chain and the three hooks.
- `~/.claude/scripts/verify-completion.ps1` — the real blocking gate and its allow condition.
- `~/.claude/scripts/update-state.ps1` — `ralph-verify` records claimed results verbatim (lines 665–704); never executes.
- `.runtime/ai-resource-governor/bin/ai-route.ps1` + `policy.json` — governor is cost routing for local calls, not a reasoning proxy.
- `~/.codex/config.toml` — `xhigh` floor present; `[[hooks.Stop]]`→`codex-stop.ps1` theater present.
- `~/.gemini/settings.json` — `AfterAgent` epistemic theater present.
- `enforcement/epistemic/engine.ps1` — prose-scan verdict logic confirmed.
- `example-enforcement` MCP tools — registered; implementation not found on disk → marked UNVERIFIED pending Action 4a.
