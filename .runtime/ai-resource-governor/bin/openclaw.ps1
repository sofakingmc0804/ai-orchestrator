$ErrorActionPreference = "Stop"
$root = "C:\Users\Couch\.ai-resource-governor"
$python = "C:\Python313\python.exe"
$actual = "C:\Users\Couch\AppData\Roaming\npm\openclaw.cmd"
$env:PYTHONPATH = $root

$receipt = @{
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  shim = "openclaw"
  command = @($args)
  route_policy = "ai-resource-governor"
  default_lane = "ollama-local-first; no-metered-fallback"
} | ConvertTo-Json -Compress
Add-Content -Path (Join-Path $root "receipts\command-invocations.jsonl") -Value $receipt -Encoding UTF8

if ($args.Count -gt 0 -and @("ask","chat","run","prompt","complete","agent") -contains [string]$args[0]) {
  & $python (Join-Path $root "ai_governor.py") route --task-type "openclaw_cli_model_call" --capability "routing" --context-tokens 0 | Out-Null
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $actual @args
exit $LASTEXITCODE
