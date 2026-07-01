# apply-cowork-core.ps1 — applies the v2.0 router core to Cowork's memory CLAUDE.md when the
# app's file lock releases. Bounded: stops after success sticks, or after 3 observed reverts
# (which proves the store is cloud-authoritative and in-app editing is the only path).
# Self-retiring: unregisters its scheduled task at terminal state. Created 2026-06-12.
$ErrorActionPreference = 'SilentlyContinue'
$core = 'C:\Users\Couch\dev\ai-orchestrator\docs\specs\cowork-core-v2.md'
$dst  = 'C:\Users\Couch\AppData\Roaming\Claude\local-agent-mode-sessions\6feaa568-5676-43ef-8e4c-fb60bfcd75c1\7e8c7fa1-391a-498b-b96d-8548698c345f\memory\CLAUDE.md'
$state = 'C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\apply-cowork-core.state.json'
$receipts = 'C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\receipts'
$s = if (Test-Path $state) { Get-Content $state -Raw | ConvertFrom-Json } else { @{applies=0; reverts=0} | ConvertTo-Json | ConvertFrom-Json }
function Done($reason) {
  @{script='apply-cowork-core'; terminal=$reason; when=(Get-Date -Format s); applies=$s.applies; reverts=$s.reverts} |
    ConvertTo-Json | Set-Content (Join-Path $receipts ("cowork-core-final-" + (Get-Date -Format yyyyMMddTHHmmss) + ".json")) -Encoding UTF8
  Unregister-ScheduledTask -TaskName 'Apply-Cowork-Core' -Confirm:$false
  exit 0
}
$cur = Get-Item $dst
$coreLen = (Get-Item $core).Length
if ([math]::Abs($cur.Length - $coreLen) -le 200) {
  # core is in place; if it survived 30+ minutes since last apply, declare success
  if ($s.applies -gt 0 -and ((Get-Date) - [datetime]$s.last_apply).TotalMinutes -ge 30) { Done 'SUCCESS_STABLE' }
  exit 0
}
if ($s.applies -gt 0) {
  $s.reverts = $s.applies  # old content is back after an apply: count as revert
  if ($s.reverts -ge 3) { Done 'CLOUD_AUTHORITATIVE_GIVE_UP' }
}
try {
  Copy-Item $core $dst -Force
  $after = (Get-Item $dst).Length
  if ([math]::Abs($after - $coreLen) -le 200) {
    $s.applies = [int]$s.applies + 1
    $s | Add-Member -NotePropertyName last_apply -NotePropertyValue (Get-Date -Format s) -Force
    @{script='apply-cowork-core'; event='APPLIED'; when=(Get-Date -Format s); attempt=$s.applies} |
      ConvertTo-Json | Set-Content (Join-Path $receipts ("cowork-core-apply-" + (Get-Date -Format yyyyMMddTHHmmss) + ".json")) -Encoding UTF8
  }
} catch {}
$s | ConvertTo-Json | Set-Content $state -Encoding UTF8
exit 0
