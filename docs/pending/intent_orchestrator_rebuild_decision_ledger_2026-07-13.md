# Intent Orchestrator Rebuild - Co-design Decision Ledger

Status: PENDING OWNER-APPROVED DESIGN; NO RUNTIME AUTHORITY

Source task: `019f4a24-319c-74d2-8829-98db938ffb76`

Audited predecessor task: `019f3e2c-9caf-73c1-b478-68833604d900` (`Enforce Claude Desktop protocols`)

## Governing intent

The orchestrator exists to prevent an AI service from silently converting its interpretation of a request into the owner's intent. It must discover material intent, preserve alternatives and disagreement, discover and use competent capabilities, coordinate services through one shared frame, and verify outcomes from the real authority surface.

This is not a regex classifier, a prompt constitution, a completion-policing hook, or a system for forcing hidden reasoning to be exposed. Rigor means enough inquiry, research, capability discovery, evidence, and owner control to understand and complete the task. It does not mean asking questions that cannot change the result.

## Predecessor audit findings

- The earlier work correctly recognized that prose-only policy was not enforcement and that completion claims needed real receipts.
- It produced useful distinctions between field mutation and submission, local proof and app-facing proof, connector authorization and UI evidence, and action claims and real readback.
- It drifted into bounded-outcome scoring, possibility counts, prompt-intake hooks, Stop hooks, verifier recursion, UI automation, cached proof, and parser-pattern tests.
- Those mechanisms acted too late or at the wrong layer. They could block or classify an answer, but they could not create a shared owner-approved frame before consequential decisions were made.
- Observed failure modes included Stop-hook token loops, recursive verifier launches, stale proof, unsent UI text, stale prompts appended to new prompts, cross-bound transcripts, false parser results, patch corruption, unsupported hook states, and resource-heavy repair/proof loops.
- The old bounded-outcome machinery is a recoverable evidence source and parts library. It is not the design authority for the rebuild.

## Locked product and interaction decisions

1. The governed orchestrator is the normal front door for nontrivial AI work.
2. Supported purposes are Answer, Research, Explore, Design, Execute, Monitor, and Review.
3. Owner authority modes are Ask First, Recommend Then Ask, Decide Reversible Details, and Decide Within Explicit Boundaries.
4. The system may move automatically into a safer mode; execution requires the authority established for that task.
5. Contextual multiple-choice answers are offered with an equal free-form route. A recommendation is explained but never preselected.
6. Consequential execution waits for a shared frame and for material decisions to be answered or explicitly delegated.
7. No service may silently replace the owner's frame with its own interpretation.
8. The desktop workbench uses three panes: Conversation 30%, Shared Frame 46%, Now 24%.
9. Pane borders and internal sections are draggable, fully expandable, and progressively disclosed.
10. Mobile and tablet retain full function through adaptive single-pane tabs.
11. The Shared Frame is a structured canvas backed by a synchronized dependency graph, version history, forks, and time travel.
12. Direct owner edits to the canvas are proposed changes. Downstream effects are shown before confirmation.
13. A correction invalidates and pauses only work that depends on the changed frame node.
14. The Now pane presents one active decision and a reorderable queue.
15. Provenance is expandable and structured. The system must never claim access to hidden chain-of-thought.
16. Research continues until an owner-controlled evidence checkpoint establishes sufficiency for the current consequence.
17. Capability discovery is tiered and exhaustive across plausible tools, skills, connectors, services, local sources, and external sources before declaring a mechanism unavailable.
18. Multiple AI services collaborate through one frame. Their questions normalize into the central decision queue.
19. Unattended work uses reversible defaults and pauses before external or irreversible actions outside standing authority.
20. The PC is the authority surface. The workbench is local and may be available over the owner's private network.
21. Inputs include text, files, screenshots/images, and voice with visible transcription and correction.
22. Owner notifications are bidirectional: send, receive, correlate, log, and close duplicates through one decision record.
23. Email, SMS, and WhatsApp are adapters over the same decision ledger. No automation may repeatedly type or ping through visible AI application UIs.
24. Email is the first notification channel.
25. Notifications use existing inbox threads and signed decision identifiers/headers rather than a dedicated mailbox.
26. Gmail is the v1 email authority. Outlook is deferred.
27. A decision may be resolved by an allowlisted email reply or a secure workbench link.
28. Routine decisions remain in the queue, blocking decisions email immediately, and critical decisions alert immediately.
29. There are no quiet hours.
30. If the requested response or action remains unresolved for one hour, escalate once through the configured mobile route. A response through any channel resolves the central decision.
31. Delivery, reply correlation, duplicate closure, and owner readback are part of the notification completion contract.
32. State uses append-only meaningful events in the existing SQLite authority plus rebuildable current-state projections.
33. Governed launchers and wrappers are the default. Direct vendor sessions remain possible, are visibly ungoverned, and are imported/reconciled.
34. Recursive learning follows test, propose, promote. Replay and adversarial evaluation precede owner approval for governance changes.
35. The core architecture is event ledger, shared frame/graph, decision service, capability registry/router, service adapters, transcript index, notification adapters, verifier, and learning proposal lane.
36. Owner-only v1 trusts the already authenticated Gmail, phone/WhatsApp, PC, and private devices. No added workbench login ceremony is required.
37. A question is justified only when its answer can materially change intent, consequence, authority, cost, user experience, or external action. Reversible technical defaults are displayed for review rather than asked.
38. The first proof spans live Codex and Claude roles end to end.
39. The lifecycle is intake, shared-frame formation, capability/source discovery, evidence checkpoint, design/decision resolution, governed execution, correction propagation, authority-surface verification, and receipt.
40. Claude Code, Claude Desktop, Codex CLI/Desktop, Hermes, and Gemini each use an adapter capability contract: frame injection, native-question normalization, pause/resume, telemetry, transcript export, and receipts. Missing capabilities are visible fallbacks, never silent discretion.
41. Verification includes unit and contract tests, adapter conformance, real transcript evaluations, fault injection, restart/replay, a live user path, and independent authority-surface readback.
42. The rebuild replaces the core incrementally and preserves useful existing machinery until the replacement proves each route.
43. Every locally accessible Claude, Codex, Hermes, and Gemini conversation is automatically indexed with source, service, time, task, and visible exclusion status.
44. The owner may add, remove, replace, or lock any recommended service/model/tool participant before or during work.
45. The orchestrator recommends the service team and explains the recommendation.
46. Material model disagreement remains visible with evidence and consequences. The system attempts evidence-based resolution and asks the owner only when the unresolved difference can change the outcome.
47. Transcript controls include reversible Exclude from search/reasoning/learning and separately confirmed Forget, which purges orchestrator copies and derived indexes but does not claim to delete source-service records.
48. Every governed task starts with guided intent intake before participating AI services begin work.
49. Intake is an adaptive conversation: infer and prefill known context, ask one consequential question at a time, and stop when the frame is sufficient.
50. A complete low-consequence request briefly displays its inferred frame and proceeds automatically unless the owner edits or stops it.
51. The first usable release is an end-to-end vertical slice: real three-pane workbench, event ledger, guided intake, decision queue, and live Claude-plus-Codex adapters.
52. The slice is accepted only when it uses its own intake, frame, decision, execution, correction, verification, and receipt path to build the orchestrator's next real capability.
53. First-release acceptance requires the slice to govern the construction and live verification of all three next capabilities: transcript intelligence, Hermes/Gemini service expansion, and Gmail-to-Hermes-WhatsApp decision escalation.

## Locked recovery and retirement decisions

- Hermes personal WhatsApp is the first mobile escalation route after one unresolved hour. It remains disabled until owner-only pairing and a live two-way correlation test pass.
- Old bounded-outcome hooks, proof harnesses, and tables are quarantined intact after replacement proof. They lose live authority, retain evidence and reusable parts, and require separate owner approval before permanent deletion.
- The specific visible-UI Claude ping automation previously suspected is not currently evidenced as running. Any later-discovered automation of that kind is stopped and quarantined. The general AI Orchestrator scheduled task is not removed without evidence that it performs the wasteful behavior.

## First-release proof contract

The bootstrap implementation may be built through the existing development path. Acceptance begins only after the vertical slice can govern its own next change.

The proof run must:

1. Start from a natural owner request and complete adaptive guided intake.
2. Produce an editable shared frame with alternatives, assumptions, authority, success conditions, and a service recommendation.
3. Dispatch distinct Claude and Codex roles through the same frame.
4. Normalize at least one native service question into the central decision queue.
5. Preserve a material disagreement or alternative until evidence resolves it or the owner decides it.
6. Accept a mid-task owner correction and invalidate only dependent work.
7. Build all three required next capabilities through the slice in dependency order: transcript intelligence, Hermes/Gemini service expansion, then Gmail-to-Hermes-WhatsApp escalation.
8. Verify each capability independently from its actual user-facing or authority surface and then verify the three-capability path as one integrated workflow.
9. Restart and replay the event ledger without losing the frame, decisions, correlation, or receipts.
10. Produce an independent completion readback tied to the original success conditions.

No test suite, transcript, screenshot, or model assertion substitutes for the live proof. Failed steps remain open, repair through the same frame, and cannot be converted into a completion claim.

## Acceptance expansion

The three capabilities are one release contract, not alternatives. Each capability is built in a separate governed change cycle through the new slice so its frame, decisions, corrections, receipts, and recovery can be independently replayed. Release acceptance then runs an integrated scenario that uses indexed transcript context, delegates work through the expanded service set, and resolves a timed owner decision through Gmail with verified Hermes WhatsApp escalation.
