# Token Efficiency Spec
Created: 2026-06-12. Canonical home of the token-waste catalog and mitigation rules.
Supersedes: Cowork session outputs token-efficiency-program.md (retired to redirect stub same day).
Consumed by: orchestrator/config/efficiency_policy.json and scripts/generate-surface-stubs.ps1.

## Cost model
Per-call statelessness means every turn re-pays system prompt + instructions + tools + history.
Always-loaded instruction tokens are paid every turn of every session. Output tokens cost 3-5x input.
Prompt caching discounts stable prefixes only; volatile content high in context breaks it.
Local models pay in VRAM, latency, and silent context truncation instead of dollars.

## Waste catalog (25 modes, grouped)
Input side, paid every turn:
1. Bloated always-loaded instruction files -> keep core under ~500 tokens, route the rest on demand.
2. All tool schemas loaded upfront -> deferred loading; lean per-workflow profiles.
3. Plugin/skill sprawl -> quarterly prune; few broad skills over many narrow.
4. Whole-file reads for section needs -> search first, read offset/length.
5. Re-reading known content -> trust tool results; verify with targeted checks.
6. Re-researching documented facts -> canonical index read before external research (GATE 0).
7. Pasting large content into chat -> files on disk, partial reads.
8. Unfiltered command output -> head/grep/count; redirect verbose output to file.
9. Duplicate context across subagents -> minimum brief per agent; parent keeps conclusions only.
10. Screenshots where API/CLI exists -> tool tiering: API > DOM > pixels; never poll visually.
11. Conversation accumulation -> one session per task; persist conclusions, end clean.
12. Cache-breaking layouts -> stable content first, volatile last.
Output side:
13. Preamble/recap/postamble padding -> answer first, no self-summary.
14. Echoing written files into chat -> report path + one line.
15. Whole-artifact regeneration for small edits -> targeted diffs under ~1/3 file changed.
16. Document proliferation (v2, final, final_REAL) -> one canonical doc; retire predecessor in same action.
17. Mandatory per-response ceremony -> rigor proportional to stakes (see ceremony_tiers).
18. Over-formatting -> prose default; structure only for multi-dimensional content.
19. Max reasoning effort on trivial tasks -> effort scaled to difficulty.
Behavioral:
20. Goal drift / scope creep -> stated directive only; adjacencies noted, not executed.
21. Identical retry loops -> change approach or stop after one retry.
22. Hard-coded temporal state -> runtime dates; date-stamped volatile facts.
23. Model as calculator/sorter -> deterministic work to scripts.
24. Stale orphans polluting future sessions -> GATE 6 retirement discipline.
25. Wrong model tier -> orchestrator dispatch loop: cheapest competent worker, escalation by named value reason.

## Enforcement map
Deterministic (hooks/scripts): predecessor checks, output validators, scheduled stub regeneration, receipts.
Routed guidance (generated stubs): always_on_rules from efficiency_policy.json, per surface.
Spec-only (model judgment): proportional rigor, drift avoidance; audited via receipts and weekly snapshots.
Target metric: tokens per completed directive, not tokens per response. Under-context causing redo cycles is also waste.
