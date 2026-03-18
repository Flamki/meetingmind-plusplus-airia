param(
  [string]$InputDir = ".\transcripts",
  [string]$Pattern = "*.txt",
  [string]$OutputJson = ".\demo_report.json",
  [string]$OutputMd = ".\demo_report.md",
  [string]$RawDir = ".\raw",
  [string]$EnvFile = ".\.env",
  [string]$WebhookUrl = "",
  [switch]$NoWebhook,
  [switch]$Recursive,
  [switch]$RequireActions,
  [int]$Retries = 3,
  [double]$BackoffSeconds = 1.25,
  [double]$JitterSeconds = 0.25,
  [int]$MaxActions = 100,
  [int]$MaxInputChars = 300000
)

$ErrorActionPreference = "Stop"

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

if (-not $NoWebhook -and -not $WebhookUrl -and $env:AIRIA_WEBHOOK_URL) {
  $WebhookUrl = $env:AIRIA_WEBHOOK_URL
}

if ($NoWebhook) {
  $WebhookUrl = ""
}

if (-not $WebhookUrl) {
  if (-not $env:AIRIA_API_KEY) {
    Write-Error "AIRIA_API_KEY is not set."
  }

  if (-not $env:AIRIA_PIPELINE_ID) {
    Write-Error "AIRIA_PIPELINE_ID is not set."
  }
}

if (-not (Test-Path $InputDir)) {
  Write-Error "InputDir not found: $InputDir"
}

Write-Host "[1/2] Running batch pipeline..."
$batchArgs = @(
  ".\meetingmind_batch.py",
  "--input-dir", $InputDir,
  "--pattern", $Pattern,
  "--save-raw-dir", $RawDir,
  "--output-json", $OutputJson,
  "--strict",
  "--retries", $Retries,
  "--backoff-seconds", $BackoffSeconds,
  "--jitter-seconds", $JitterSeconds,
  "--max-actions", $MaxActions,
  "--max-input-chars", $MaxInputChars
)
if ($Recursive) { $batchArgs += "--recursive" }
if ($RequireActions) { $batchArgs += "--require-actions" }
if ($NoWebhook) { $batchArgs += "--no-webhook" }
if ($WebhookUrl) {
  $batchArgs += @("--webhook-url", $WebhookUrl)
}
python @batchArgs
if ($LASTEXITCODE -ne 0) {
  throw "Batch run failed with exit code $LASTEXITCODE"
}

Write-Host "[2/2] Rendering markdown report..."
python .\meetingmind_report_md.py `
  --input-json $OutputJson `
  --output-md $OutputMd `
  --title "MeetingMind Hackathon Evaluation"
if ($LASTEXITCODE -ne 0) {
  throw "Markdown render failed with exit code $LASTEXITCODE"
}

Write-Host "[DONE] Generated:"
Write-Host " - $OutputJson"
Write-Host " - $OutputMd"
