param(
  [string]$TaskName = "MeetingMindWeeklyIntel",
  [string]$WeeklyAt = "17:00",
  [string]$WorkingDir = "C:\Users\bbook\Desktop\airia",
  [string]$EnvFile = ".\.env"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $WorkingDir "run_weekly_intelligence.ps1"
if (-not (Test-Path $scriptPath)) {
  throw "Missing script: $scriptPath"
}

$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -File `"$scriptPath`" -EnvFile `"$EnvFile`" -SendSlack -SendEmail"

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $WeeklyAt
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Host "[OK] Registered weekly task '$TaskName' for Friday $WeeklyAt" -ForegroundColor Green
