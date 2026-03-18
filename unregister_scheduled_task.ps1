param(
  [string]$TaskName = "MeetingMindDaily"
)

$ErrorActionPreference = "Stop"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "[OK] Scheduled task removed: $TaskName"
