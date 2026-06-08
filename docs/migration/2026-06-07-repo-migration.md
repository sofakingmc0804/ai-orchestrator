# Repo Migration Receipt

Generated: 2026-06-08T00:11:17.811974+00:00
Repo: `C:\Users\Couch\dev\ai-orchestrator`

## Migrated Surfaces

- `orchestrator_runtime`: `C:\Users\Couch\.orchestrator` -> `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator` (149 files, 99741823 bytes)
- `resource_governor`: `C:\Users\Couch\.ai-resource-governor` -> `C:\Users\Couch\dev\ai-orchestrator\.runtime\ai-resource-governor` (598 files, 1288858 bytes)
- `spec_v4`: `C:\Users\Couch\Downloads\AI_ORCHESTRATOR_SPEC_v4.0.md` -> `C:\Users\Couch\dev\ai-orchestrator\docs\specs\AI_ORCHESTRATOR_SPEC_v4.0.md` (1 files, 49969 bytes)
- `agents`: `C:\Users\Couch\AGENTS.md` -> `C:\Users\Couch\dev\ai-orchestrator\AGENTS.md` (1 files, 3672 bytes)

## Runtime Defaults

- `ORCHESTRATOR_HOME`: `C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator`
- `AI_RESOURCE_GOVERNOR_HOME`: `C:\Users\Couch\dev\ai-orchestrator\.runtime\ai-resource-governor`

## Notes

- Legacy source folders were preserved as backups.
- SQLite databases were copied with `sqlite3.Connection.backup`, not raw file copy.
- Mutable `.sqlite`, WAL/SHM files, logs, caches, and local env files are ignored by Git to prevent accidental publication.
- The repo now carries the machine operating law in `AGENTS.md`.
