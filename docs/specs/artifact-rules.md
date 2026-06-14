# Artifact Rules (full rigor, loaded on trigger)
Derived from Epistemic Constraint System v1.2 GATE 6, INV-007/008/009, FP-010/011/012.

## Before creating or modifying any artifact
- Identify every predecessor: older versions in other paths, stale dependencies, scattered
  duplicates, disabled-but-not-deleted tasks/scripts/components, superseded docs still claiming
  authority, one-shot helpers past their use, logs/outputs of superseded versions.
- For each: RETIRE in this same action (delete, archive to a dated archive dir, or merge into
  the successor) or JUSTIFY in writing with verifiable evidence (named active dependency, cited
  contract, in-flight runtime, explicit owner instruction). Forbidden justifications: "might be
  useful", "for backup", "just in case", "will clean up later".
- Copying a file to a new canonical home requires, in the same action: mark the new copy
  authoritative, and retire or redirect-stub every other copy. Two writable copies of one
  source of truth without drift acknowledgment is a violation.

## Before any terminal state (done-claim, handoff, commit, moving on)
- Execute the orphan audit. Verify retirements happened on disk; list the commands or tool calls
  that did them. Never claim retirement without verification evidence in the same response.
- State the record: "Orphan retirement: N retired, M justified-retained" with paths and reasons.
- Commits adding a new artifact name the predecessor and include its retirement.
- No future-work tickets, TODO markers, or backlog entries satisfy any of the above.
