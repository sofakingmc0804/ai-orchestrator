# AI Resource Governor Retirement

Date: 2026-06-16 local / 2026-06-17 UTC

## Consequence

The legacy `.ai-resource-governor` home could still act on this machine through a scheduled refresh, PATH shims, and stale Hermes wrappers. That kept an old runtime surface alive after the orchestrator became the authority.

Retirement means the old path can no longer execute as a runtime contract. It does not mean useful history was thrown away.

## Mechanism

Owner-gate means a destructive machine-state change needed explicit owner intent because it touched resources outside Git:

- `C:\Users\Couch\.ai-resource-governor`
- Windows scheduled task `AI Resource Governor - Daily Refresh`
- user PATH entries
- live local runtime databases

The owner provided that intent in the 2026-06-16 instruction. The guarded action was archive first, harvest second, migrate useful rows third, then remove live entrypoints.

## Action Taken

- Archived the full old home to `.runtime\archives\ai-resource-governor-retirement-20260617T035028Z\home-dot-ai-resource-governor`.
- Harvested useful source, policy, roster, Hermes notes, repair queue, and tests to `docs\_harvested-2026-06-16\from-ai-resource-governor-retirement`.
- Exported the scheduled task XML before removal.
- Ran `python scripts\migrate-governor-to-orchestrator.py --execute`.
- Removed the scheduled task `AI Resource Governor - Daily Refresh`.
- Removed `C:\Users\Couch\.ai-resource-governor\bin` from user PATH.
- Renamed the old home to `C:\Users\Couch\.ai-resource-governor.retired-20260617T035028Z`.
- Added `.runtime\archives\` and `.runtime\backups\` to `.gitignore`.

## Proof

Persisted receipts:

- `.runtime\archives\ai-resource-governor-retirement-20260617T035028Z\retirement-continuation-receipt.json`
- `docs\_harvested-2026-06-16\from-ai-resource-governor-retirement\retirement-continuation-receipt.redacted.json`

Verified state:

- Legacy home exists after retirement: `False`
- Retired home exists: `True`
- Scheduled task exists after retirement: `False`
- Orchestrator DB rows after migration: `worker_cards=96`, `job_classes=14`, `budget_probes=6`
- Remaining scheduled AI task: `AI Orchestrator`

The migration script did migrate worker and job rows, then failed in its compatibility symlink step because it tried to unlink `inventory.sqlite` while its own process still held that source DB open. Retirement did not depend on that compatibility layer; the old home was renamed instead.

## Road Through

Use `python -m orchestrator.cli.main route` and `python -m orchestrator.cli.main spec-status` as the authority. Treat the retired folder as cold evidence only. If a missed dependency appears, harvest it from the retired folder into this repo and then keep the runtime pointed at orchestrator, not at `.ai-resource-governor`.
