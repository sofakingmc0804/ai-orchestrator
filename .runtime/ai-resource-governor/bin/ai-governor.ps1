$ErrorActionPreference = "Stop"
$root = "C:\Users\Couch\.ai-resource-governor"
$python = "C:\Python313\python.exe"
$env:PYTHONPATH = $root
& $python (Join-Path $root "ai_governor.py") @args
exit $LASTEXITCODE
