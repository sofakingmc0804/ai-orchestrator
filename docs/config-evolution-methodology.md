# Config Evolution Methodology

Bad config freezes useful agents in yesterday's shape. Loose config lets drift become policy.

The working method is governed adaptation.

## Rule

Agents may propose and make bounded config improvements when a better route, model, connector, tool, or authority surface is available and the change can be proved.

The default posture is steering, not prevention. A guard should add context, route pressure, proof requirements, or a fast approval lane before it blocks action.

## Change Shape

Every config adjustment should answer five questions:

- Consequence: what gets better or safer if this changes?
- Mechanism: which route, provider, hook, connector, model, budget, or authority path changes?
- Authority: what live file, API, receipt, config schema, test, or owner instruction proves the change is allowed?
- Reversal: what file, prior value, backup, or commit can restore the old state?
- Receipt: where is the proof that the change was made and verified?

## Allowed Motion

Use this lane for:

- replacing hard blocks with advisory routing when the risk is manageable
- adding better models, tools, connectors, or browser/control surfaces
- updating aliases when provider or tool names drift
- promoting a newer proven authority path over a stale default
- tightening credential handling, mutation boundaries, or approval scope
- retiring config that only preserves a worse route

## Hard Stops

Stop or require explicit owner approval when the change would:

- copy, expose, or persist secrets
- mutate external systems without task-specific authority
- take over Matt's visible desktop or browser outside a bounded lease
- spend metered money or consume scarce quota outside policy
- weaken an explicit business, legal, email, safety, or credential boundary
- erase rollback evidence

## Completion Test

A config evolution is complete only when:

- the changed files are named
- unrelated worktree changes are left alone
- the relevant test, parser, health check, or readback passes
- the receipt or commit explains the authority and rollback path
- future agents can see why the new route is freer and safer than the old one

The road through is not to make the system obedient by making it small.

The road through is to let it improve with proof.
