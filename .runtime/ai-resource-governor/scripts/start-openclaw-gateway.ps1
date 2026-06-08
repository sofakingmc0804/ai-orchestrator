$ErrorActionPreference = "Continue"
$root = "C:\Users\Couch\.ai-resource-governor"
$openclaw = "C:\Users\Couch\AppData\Roaming\npm\openclaw.cmd"
$logDir = Join-Path $root "receipts"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "openclaw-gateway-$stamp.log"

& $openclaw gateway run --port 18789 --bind loopback --auth token --compact *> $log
exit $LASTEXITCODE
