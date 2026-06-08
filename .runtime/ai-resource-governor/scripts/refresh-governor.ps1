$ErrorActionPreference = "Continue"
$root = "C:\Users\Couch\.ai-resource-governor"
$python = "C:\Python313\python.exe"
$env:PYTHONPATH = $root
$logDir = Join-Path $root "receipts"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "scheduled-refresh-$stamp.log"

& $python (Join-Path $root "ai_governor.py") refresh --scan-root "C:\" --scan-root "G:\" --max-depth 4 *> $log
exit $LASTEXITCODE
