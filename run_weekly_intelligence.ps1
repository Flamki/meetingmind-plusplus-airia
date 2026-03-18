param(
  [string]$EnvFile = ".\.env",
  [switch]$Force,
  [switch]$SendSlack,
  [switch]$SendEmail,
  [int]$LookbackDays = 7
)

$ErrorActionPreference = "Stop"

if (Test-Path $EnvFile) {
  Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
      [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
  }
}

$args = @(
  ".\meetingmind_weekly_intelligence.py",
  "--lookback-days", "$LookbackDays",
  "--output-json", ".\weekly_intelligence.json",
  "--output-md", ".\weekly_intelligence.md",
  "--output-pdf", ".\weekly_intelligence.pdf"
)
if ($Force) { $args += "--force" }
if ($SendSlack) { $args += "--send-slack" }
if ($SendEmail) { $args += "--send-email" }

python @args
if ($LASTEXITCODE -ne 0) { throw "Weekly intelligence run failed ($LASTEXITCODE)" }

Write-Host "[DONE] Weekly artifacts:" -ForegroundColor Green
Write-Host " - .\weekly_intelligence.json"
Write-Host " - .\weekly_intelligence.md"
Write-Host " - .\weekly_intelligence.pdf (if reportlab installed)"
