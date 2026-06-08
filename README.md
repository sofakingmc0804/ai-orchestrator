# AI Orchestrator

Standalone user-facing AI operating layer built from `AI_ORCHESTRATOR_SPEC_v4.0.md`.

The app is Windows-first and cross-OS by interface. It now keeps its operating state inside this repo at `.runtime\orchestrator`, keeps the AI Resource Governor mirror at `.runtime\ai-resource-governor`, discovers AI services on the machine, exposes a FastAPI UI/API, and routes local-safe work to adapter contracts.

Legacy source folders still exist as backups:

- `C:\Users\Couch\.orchestrator`
- `C:\Users\Couch\.ai-resource-governor`
- `C:\Users\Couch\Documents\Claude\Projects\Home of Claude - MSI Auto Project`

The default runtime path is repo-owned unless `ORCHESTRATOR_HOME` or `AI_RESOURCE_GOVERNOR_HOME` explicitly overrides it.

## Migrated model roster and benchmark artifacts

The MSI Auto Project model roster, benchmark harness, results, and Orchestrator score contract now live in this repo.

- Roster workbook: `data/rosters/AI_MODEL_QUALITY_ROSTER_2026-06-07.xlsx`
- Roster generator: `tools/roster/AI_MODEL_QUALITY_ROSTER_generator.py`
- Benchmark manifest: `data/benchmark_fixtures/manifest.json`
- Operation fixtures: `data/benchmark_fixtures/first_pack.json`
- Benchmark runners: `scripts/run_local_ollama_benchmarks.py`, `scripts/run_operation_benchmarks.py`
- Routing score contract: `data/contracts/local_model_operation_scores_2026-06-07.json`
- Migration receipt: `docs/migration/2026-06-08-msi-auto-project-artifact-migration.md`

Start the API/UI:

```powershell
python -m orchestrator.main
```

The preferred path is FastAPI + uvicorn. If the installed global Starlette/FastAPI packages are incompatible, the entrypoint falls back to a standard-library HTTP server with the same v1 API endpoints so the Orchestrator still opens.

Run a discovery refresh:

```powershell
python -m orchestrator.cli.main refresh
```

Export the owner receipt:

```powershell
python -m orchestrator.cli.main owner-receipt
```

Run tests:

```powershell
python -m pytest
```
