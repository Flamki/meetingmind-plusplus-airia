param(
  [string]$TaskName = "MeetingMindDaily",
  [string]$WorkingDir = "C:\Users\bbook\Desktop\airia",
  [string]$DailyAt = "09:00",
  [string]$InputDir = ".\transcripts",
  [string]$Pattern = "*.txt",
  [switch]$NoWebhook,
  [switch]$Recursive,
  [switch]$RequireActions
)

$ErrorActionPreference = "Stop"

$taskScript = Join-Path $WorkingDir "run_scheduled.ps1"
if (-not (Test-Path $taskScript)) {
  throw "run_scheduled.ps1 not found at $taskScript"
}

$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$taskScript`" -WorkingDir `"$WorkingDir`" -InputDir `"$InputDir`" -Pattern `"$Pattern`""
if ($NoWebhook) { $argument += " -NoWebhook" }
if ($Recursive) { $argument += " -Recursive" }
if ($RequireActions) { $argument += " -RequireActions" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $WorkingDir
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "[OK] Scheduled task registered: $TaskName at $DailyAt"
Write-Host "[INFO] Run now (optional): Start-ScheduledTask -TaskName `"$TaskName`""
