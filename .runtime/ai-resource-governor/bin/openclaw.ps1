$ErrorActionPreference = "Stop"
$root = "C:\Users\Couch\dev\ai-orchestrator\.runtime\ai-resource-governor"
$receiptDir = Join-Path $root "receipts"
New-Item -ItemType Directory -Force -Path $receiptDir | Out-Null

$receipt = @{
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  shim = "openclaw"
  command = @($args)
  route_policy = "ai-orchestrator"
  state = "retired"
  retired_at = "2026-06-16"
  archive_path = "C:\Users\Couch\Archive\openclaw-retired-2026-06-16"
  replacement = "python -m orchestrator.cli.main route"
} | ConvertTo-Json -Compress
Add-Content -Path (Join-Path $receiptDir "command-invocations.jsonl") -Value $receipt -Encoding UTF8

Write-Error "OpenClaw is retired on this machine. Use ai-orchestrator routing instead."
exit 410
