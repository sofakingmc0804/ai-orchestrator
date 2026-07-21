# Pending Repair: Answer-Only Evidence Mapping For Bounded Contract Stop

Created: 2026-07-09

## Consequence

The Stop hook can reject an answered planning/workflow request even after the assistant has produced the requested local apply-desk artifacts.

Observed failing plan: `shp_c16d45d603d748ec`

Observed evaluator output:

```json
{
  "authority_gap_count": 480,
  "covered_count": 0,
  "terminal_state": "continuation_required",
  "unresolved_required_count": 480
}
```

First counterexample:

```text
ce_pos_00001_evidence_weaker_than_required
request_type=answer
authority_surface=user_prompt
proof_level_required=answer_only
```

## Mechanism

The bounded evaluator correctly accepts `prose_only` evidence for answer-only requirements when supplied directly:

```python
EvidenceItem(evidence_class="prose_only", authority_surface="user_prompt", subject="answer")
EvidenceItem(evidence_class="prose_only", authority_surface="current_chat", subject="answer")
EvidenceItem(
    evidence_class="ui_trace",
    authority_surface="browser_dom",
    subject="ui_action",
    detail="runtime_ui_trace: browser_dom playwright non_foreground no_foreground_control application tabs observed",
)
```

Direct evaluator result with those three evidence items:

```json
{
  "authority_gap_count": 0,
  "covered_count": 480,
  "terminal_state": "verified_success",
  "unresolved_required_count": 0
}
```

The Stop hook path, however, ignores assistant-supplied evidence and `_contract_evidence_from_verification(...)` does not emit `prose_only` answer evidence for `user_prompt` or `current_chat`. It only maps hook-owned verification lines into local mutation, governance, resource, connector, or UI evidence.

## Proposed Minimal Repair

In `orchestrator/skills/gate.py`, update `_contract_evidence_from_verification(...)` so hook-owned verification can emit answer-only evidence when the plan prompt actually derives required answer items.

Suggested behavior:

1. Derive the contract requirements from `plan.prompt`.
2. If required items include `request_type == "answer"` and `proof_level_required == "answer_only"`:
   - add `EvidenceItem(evidence_class="prose_only", authority_surface="user_prompt", subject="answer")` when `user_prompt` is required.
   - add `EvidenceItem(evidence_class="prose_only", authority_surface="current_chat", subject="answer")` when `current_chat` is required.
3. Do not add these items for artifact, mutation, external, governance, UI, or connector requirements.
4. Leave the existing assistant-supplied-evidence rejection intact.

## Test To Add

Add a regression test using a prompt shaped like:

```text
What workflow is most seamless, and what are you able to do without me versus what do you need me to do with you?
```

The test should verify:

- direct answer-only evidence closes the answer requirements.
- Stop hook-owned evidence can include answer-only evidence for answer prompts.
- artifact and mutation prompts still reject prose-only evidence.
- browser/UI requirements still require real `runtime_ui_trace` evidence.

## Authorization Boundary

This is a governance-adjacent hook repair. Do not apply the code change without Matt's explicit authorization or a bounded governance repair instruction.

## Immediate Non-Governance Workaround

Continue the application workflow from local artifacts without trying to close this Stop hook:

1. Use `C:\Users\Couch\Documents\Resumes\APPLY_TRACKER_2026-07-09.md`.
2. Start with Databricks row 1.
3. Use a bounded browser lease if Matt wants Codex to operate the browser.
4. Stop before final submit.
