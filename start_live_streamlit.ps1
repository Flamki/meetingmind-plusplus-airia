param(
  [int]$Port = 8501
)

$ErrorActionPreference = "Stop"

$logsDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

$streamlitLog = Join-Path $logsDir "streamlit_live.log"
$streamlitErr = Join-Path $logsDir "streamlit_live.err.log"
$tunnelLog = Join-Path $logsDir "localtunnel_live.log"
$tunnelErr = Join-Path $logsDir "localtunnel_live.err.log"

if (Test-Path $streamlitLog) { Remove-Item $streamlitLog -Force }
if (Test-Path $streamlitErr) { Remove-Item $streamlitErr -Force }
if (Test-Path $tunnelLog) { Remove-Item $tunnelLog -Force }
if (Test-Path $tunnelErr) { Remove-Item $tunnelErr -Force }

# Start Streamlit app
$streamlitArgs = @(
  "-NoProfile",
  "-Command",
  "Set-Location `"$PSScriptRoot`"; python -m streamlit run .\meetingmind_dashboard.py --server.port $Port --server.headless true"
)
$streamlitProc = Start-Process -FilePath "powershell.exe" -ArgumentList $streamlitArgs -PassThru -RedirectStandardOutput $streamlitLog -RedirectStandardError $streamlitErr

Start-Sleep -Seconds 5

# Start public tunnel
$tunnelArgs = @(
  "-NoProfile",
  "-Command",
  "Set-Location `"$PSScriptRoot`"; npx --yes localtunnel --port $Port"
)
$tunnelProc = Start-Process -FilePath "powershell.exe" -ArgumentList $tunnelArgs -PassThru -RedirectStandardOutput $tunnelLog -RedirectStandardError $tunnelErr

Write-Host "[INFO] Streamlit PID: $($streamlitProc.Id)"
Write-Host "[INFO] Tunnel PID: $($tunnelProc.Id)"

$url = ""
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 1
  if (Test-Path $tunnelLog) {
    $content = Get-Content $tunnelLog -Raw -ErrorAction SilentlyContinue
    if ($content -match "https://[a-z0-9-]+\.loca\.lt") {
      $url = $Matches[0]
      break
    }
  }
}

if ($url) {
  Write-Host "[LIVE] Public URL: $url" -ForegroundColor Green
  Write-Host "[INFO] Stop with: powershell -ExecutionPolicy Bypass -File .\stop_live_streamlit.ps1"
} else {
  Write-Host "[WARN] Tunnel URL not detected yet. Check logs: $tunnelLog"
}
