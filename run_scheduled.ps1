param(
  [string]$WorkingDir = "C:\Users\bbook\Desktop\airia",
  [string]$EnvFile = ".\.env",
  [string]$InputDir = ".\transcripts",
  [string]$Pattern = "*.txt",
  [switch]$NoWebhook,
  [switch]$Recursive,
  [switch]$RequireActions
)

$ErrorActionPreference = "Stop"
Set-Location $WorkingDir

if (Test-Path $EnvFile) {
  Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
      $name = $parts[0].Trim()
      $value = $parts[1].Trim()
      [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
  }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = ".\logs"
if (-not (Test-Path $logDir)) {
  New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logPath = Join-Path $logDir "scheduled-run-$timestamp.log"

try {
  $args = @(
    "-ExecutionPolicy", "Bypass",
    "-File", ".\run_all.ps1",
    "-EnvFile", $EnvFile,
    "-InputDir", $InputDir,
    "-Pattern", $Pattern
  )
  if ($NoWebhook) { $args += "-NoWebhook" }
  if ($Recursive) { $args += "-Recursive" }
  if ($RequireActions) { $args += "-RequireActions" }

  "[$(Get-Date -Format s)] Starting scheduled run" | Out-File -FilePath $logPath -Encoding utf8
  & powershell @args 2>&1 | Out-File -FilePath $logPath -Encoding utf8 -Append
  if ($LASTEXITCODE -ne 0) {
    throw "run_all.ps1 failed with exit code $LASTEXITCODE"
  }
  "[$(Get-Date -Format s)] Completed scheduled run" | Out-File -FilePath $logPath -Encoding utf8 -Append
} catch {
  "[$(Get-Date -Format s)] Scheduled run failed: $($_.Exception.Message)" | Out-File -FilePath $logPath -Encoding utf8 -Append
  throw
}
