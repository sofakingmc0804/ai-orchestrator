# measure-token-overhead.ps1 — weekly snapshot of fixed AI context overhead.
# Token estimate: bytes/4. Receipts to .runtime\orchestrator\receipts. Created 2026-06-12.
$ErrorActionPreference = 'Continue'
$repo = Split-Path $PSScriptRoot -Parent
$files = @(
  'C:\Users\Couch\AppData\Roaming\Claude\local-agent-mode-sessions\6feaa568-5676-43ef-8e4c-fb60bfcd75c1\7e8c7fa1-391a-498b-b96d-8548698c345f\memory\CLAUDE.md',
  'C:\Users\Couch\.claude\CLAUDE.md',
  'C:\Users\Couch\AGENTS.md',
  'C:\Users\Couch\dev\ai-orchestrator\AGENTS.md',
  'C:\Users\Couch\.gemini\GEMINI.md'
)
$entries = foreach ($f in $files) {
  if (Test-Path $f) {
    $sz = (Get-Item $f).Length
    @{ file=$f; bytes=$sz; est_tokens=[math]::Round($sz/4) }
  } else { @{ file=$f; bytes=0; est_tokens=0; missing=$true } }
}
$pluginDirs = @(Get-ChildItem 'C:\Users\Couch\AppData\Roaming\Claude\local-agent-mode-sessions\6feaa568-5676-43ef-8e4c-fb60bfcd75c1\7e8c7fa1-391a-498b-b96d-8548698c345f\rpm' -Directory -ErrorAction SilentlyContinue)
$sessBase = 'C:\Users\Couch\AppData\Roaming\Claude\local-agent-mode-sessions\6feaa568-5676-43ef-8e4c-fb60bfcd75c1\7e8c7fa1-391a-498b-b96d-8548698c345f'
$newest = Get-ChildItem $sessBase -Directory -Filter 'local_*' -ErrorAction SilentlyContinue | Sort-Object CreationTime -Descending | Select-Object -First 1
$inj = 0; $injName = 'none'
if ($newest) { $p = Join-Path $newest.FullName '.claude\CLAUDE.md'; if (Test-Path $p) { $inj = (Get-Item $p).Length; $injName = $newest.Name } }
$stamp = Get-Date -Format yyyyMMddTHHmmssZ
$out = @{
  script = 'measure-token-overhead'; when = $stamp
  instruction_files = $entries
  cowork_plugin_count = $pluginDirs.Count
  newest_session = $injName
  newest_session_injected_bytes = $inj
  total_est_instruction_tokens = (($entries | ForEach-Object { $_.est_tokens }) | Measure-Object -Sum).Sum
}
$receiptDir = Join-Path $repo '.runtime\orchestrator\receipts'
$out | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $receiptDir "token-baseline-$stamp.json") -Encoding UTF8
Write-Host "Receipt: token-baseline-$stamp.json / est instruction tokens: $($out.total_est_instruction_tokens) / plugins: $($pluginDirs.Count)"
