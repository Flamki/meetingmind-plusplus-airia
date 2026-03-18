param(
  [string]$TranscriptManual = ".\transcripts\meeting_01_product_sync.txt",
  [string]$TranscriptAuto = ".\transcripts\meeting_02_customer_escalation.txt",
  [switch]$UseWebhook,
  [switch]$PostSlack,
  [switch]$PostTeams,
  [switch]$CreateJira,
  [switch]$CreateAsana,
  [switch]$SendEmail,
  [switch]$StrictIntegrations,
  [switch]$SequentialFanout,
  [switch]$NotifySlackApproval,
  [switch]$StrictBatch,
  [switch]$BatchRequireActions,
  [switch]$DryRun,
  [string]$EnvFile = ".\.env"
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

if (-not (Test-Path $TranscriptManual)) { throw "Manual transcript not found: $TranscriptManual" }
if (-not (Test-Path $TranscriptAuto)) { throw "Auto transcript not found: $TranscriptAuto" }

$baseArgs = @(".\meetingmind_runner.py")
if (-not $UseWebhook) { $baseArgs += "--no-webhook" }
if ($PostSlack) { $baseArgs += "--post-slack" }
if ($PostTeams) { $baseArgs += "--post-teams" }
if ($CreateJira) { $baseArgs += "--create-jira" }
if ($CreateAsana) { $baseArgs += "--create-asana" }
if ($SendEmail) { $baseArgs += "--send-email" }
if ($StrictIntegrations) { $baseArgs += "--strict-integrations" }
if ($SequentialFanout) { $baseArgs += "--fanout-mode"; $baseArgs += "sequential" }
if ($NotifySlackApproval) { $baseArgs += "--notify-slack-approval" }
if ($DryRun) { $baseArgs += "--dry-run" }

Write-Host "[1/4] Manual mode run..." -ForegroundColor Cyan
python @baseArgs --mode manual --transcript-file $TranscriptManual --save-raw ".\raw\demo_manual.airia.json" --run-report ".\raw\demo_manual.run_report.json"
if ($LASTEXITCODE -ne 0) { throw "Manual mode run failed ($LASTEXITCODE)" }

Write-Host "[2/4] Auto mode run..." -ForegroundColor Cyan
python @baseArgs --mode auto --require-actions --transcript-file $TranscriptAuto --save-raw ".\raw\demo_auto.airia.json" --run-report ".\raw\demo_auto.run_report.json"
if ($LASTEXITCODE -ne 0) { throw "Auto mode run failed ($LASTEXITCODE)" }

Write-Host "[3/4] Batch report run..." -ForegroundColor Cyan
$batchArgs = @(
  ".\meetingmind_batch.py",
  "--input-dir", ".\transcripts",
  "--pattern", "*.txt",
  "--recursive",
  "--mode", "auto",
  "--save-raw-dir", ".\raw",
  "--output-json", ".\demo_report.json"
)
if (-not $UseWebhook) { $batchArgs += "--no-webhook" }
if ($StrictBatch) { $batchArgs += "--strict" }
if ($BatchRequireActions) { $batchArgs += "--require-actions" }
python @batchArgs
if ($LASTEXITCODE -ne 0) { throw "Batch run failed ($LASTEXITCODE)" }

Write-Host "[4/4] Render markdown report..." -ForegroundColor Cyan
python .\meetingmind_report_md.py --input-json .\demo_report.json --output-md .\demo_report.md --title "MeetingMind Hackathon Evaluation"
if ($LASTEXITCODE -ne 0) { throw "Markdown render failed ($LASTEXITCODE)" }

Write-Host "[DONE] Winning demo artifacts generated:" -ForegroundColor Green
Write-Host " - .\raw\demo_manual.airia.json"
Write-Host " - .\raw\demo_manual.run_report.json"
Write-Host " - .\raw\demo_auto.airia.json"
Write-Host " - .\raw\demo_auto.run_report.json"
Write-Host " - .\demo_report.json"
Write-Host " - .\demo_report.md"
