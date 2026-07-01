$ErrorActionPreference = "Stop"
$root = "C:\Users\Couch\dev\ai-orchestrator\.runtime\ai-resource-governor"
$python = "C:\Python313\python.exe"
$env:PYTHONPATH = $root
& $python (Join-Path $root "ai_governor.py") --root $root route @args
exit $LASTEXITCODE
