# generate-surface-stubs.ps1 — injects the governed AI policy block into every AI surface
# instruction file. Source of truth: orchestrator/config/efficiency_policy.json.
# Marker-based: only content between markers is managed; owner text outside is never touched.
# Created 2026-06-12 per token-architecture-plan.md Rev 2 (Matt-approved).
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$policy = Get-Content (Join-Path $repo 'orchestrator\config\efficiency_policy.json') -Raw | ConvertFrom-Json
$begin = '<!-- AI-GOVERNANCE-BLOCK BEGIN (generated; do not edit between markers) -->'
$end   = '<!-- AI-GOVERNANCE-BLOCK END -->'

$rules = ($policy.always_on_rules | ForEach-Object { "- $_" }) -join "`n"
$block = @"
$begin
## Governed AI Policy (generated $(Get-Date -Format yyyy-MM-dd) from ai-orchestrator efficiency_policy.json v$($policy.version))
Authority: C:\Users\Couch\dev\ai-orchestrator\ (vendor-neutral). Full rigor rules:
docs\specs\epistemic-rules.md (claims/sources/contradictions/options) and
docs\specs\artifact-rules.md (predecessor retirement) — load on consequential work.
Dispatch: cheapest competent worker; premium models only for architecture, hard code repair,
high-stakes decision, long-context synthesis, final critique, security review.
$rules
$end
"@

$targets = @(
  'C:\Users\Couch\AGENTS.md',
  'C:\Users\Couch\.claude\CLAUDE.md',
  'C:\Users\Couch\dev\ai-orchestrator\AGENTS.md',
  'C:\Users\Couch\.gemini\GEMINI.md'
)
$report = @()
foreach ($t in $targets) {
  $dir = Split-Path $t -Parent
  if (-not (Test-Path $dir)) { $report += "SKIP (no dir): $t"; continue }
  if (Test-Path $t) {
    $content = Get-Content $t -Raw
    if ($null -eq $content) { $content = '' }
    $pattern = [regex]::Escape($begin) + '[\s\S]*?' + [regex]::Escape($end)
    if ($content -match $pattern) {
      $newContent = [regex]::Replace($content, $pattern, $block.TrimEnd())
      $action = 'UPDATED'
    } else {
      $newContent = $content.TrimEnd() + "`n`n" + $block
      $action = 'APPENDED'
    }
  } else {
    $newContent = "# GEMINI instructions`n`n" + $block
    $action = 'CREATED'
  }
  Set-Content -Path $t -Value $newContent -Encoding UTF8
  $report += "${action}: $t"
}
$receiptDir = Join-Path $repo '.runtime\orchestrator\receipts'
if (Test-Path $receiptDir) {
  $stamp = Get-Date -Format yyyyMMddTHHmmssZ
  @{ script='generate-surface-stubs'; when=$stamp; results=$report } | ConvertTo-Json |
    Set-Content (Join-Path $receiptDir "stub-sync-$stamp.json") -Encoding UTF8
}
$report | ForEach-Object { Write-Host $_ }
