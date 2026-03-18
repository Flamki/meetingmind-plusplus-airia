param(
  [int]$Port = 8501
)

$ErrorActionPreference = "Stop"

$logsDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot "tools") -Force | Out-Null

$streamlitLog = Join-Path $logsDir "streamlit_live.log"
$streamlitErr = Join-Path $logsDir "streamlit_live.err.log"
$tunnelLog = Join-Path $logsDir "cloudflare_tunnel_live.log"
$tunnelErr = Join-Path $logsDir "cloudflare_tunnel_live.err.log"
$cloudflaredPath = Join-Path $PSScriptRoot "tools\cloudflared.exe"
$cloudflaredUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

function Reset-LogFile {
  param([string]$Path)
  if (Test-Path $Path) {
    try {
      Clear-Content -Path $Path -Force -ErrorAction Stop
      return
    } catch {
      Write-Host "[WARN] Could not clear $Path (likely locked). New output will append."
      return
    }
  }
  New-Item -ItemType File -Path $Path -Force | Out-Null
}

Reset-LogFile -Path $streamlitLog
Reset-LogFile -Path $streamlitErr
Reset-LogFile -Path $tunnelLog
Reset-LogFile -Path $tunnelErr

# Download cloudflared if missing
if (-not (Test-Path $cloudflaredPath)) {
  Write-Host "[INFO] Downloading cloudflared..."
  Invoke-WebRequest -Uri $cloudflaredUrl -OutFile $cloudflaredPath
}

# Start Streamlit app
$streamlitArgs = @(
  "-NoProfile",
  "-Command",
  "Set-Location `"$PSScriptRoot`"; python -m streamlit run .\meetingmind_dashboard.py --server.port $Port --server.headless true"
)
$streamlitProc = Start-Process -FilePath "powershell.exe" -ArgumentList $streamlitArgs -PassThru -RedirectStandardOutput $streamlitLog -RedirectStandardError $streamlitErr

Start-Sleep -Seconds 5

# Start public tunnel (Cloudflare Quick Tunnel)
$tunnelArgs = @(
  "-NoProfile",
  "-Command",
  "Set-Location `"$PSScriptRoot`"; .\tools\cloudflared.exe tunnel --url http://localhost:$Port --no-autoupdate"
)
$tunnelProc = Start-Process -FilePath "powershell.exe" -ArgumentList $tunnelArgs -PassThru -RedirectStandardOutput $tunnelLog -RedirectStandardError $tunnelErr

Write-Host "[INFO] Streamlit PID: $($streamlitProc.Id)"
Write-Host "[INFO] Tunnel PID: $($tunnelProc.Id)"

$url = ""
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 1
  $contentOut = ""
  $contentErr = ""
  if (Test-Path $tunnelLog) {
    $contentOut = Get-Content $tunnelLog -Raw -ErrorAction SilentlyContinue
  }
  if (Test-Path $tunnelErr) {
    $contentErr = Get-Content $tunnelErr -Raw -ErrorAction SilentlyContinue
  }

  $combined = "$contentOut`n$contentErr"
  if ($combined -match "https://[a-z0-9-]+\.trycloudflare\.com") {
    $url = $Matches[0]
    break
  }
  if ($combined -match "https://[a-z0-9-]+\.loca\.lt") {
    $url = $Matches[0]
    break
  }
}

if ($url) {
  Write-Host "[LIVE] Public URL: $url" -ForegroundColor Green
  Write-Host "[INFO] Stop with: powershell -ExecutionPolicy Bypass -File .\stop_live_streamlit.ps1"
} else {
  Write-Host "[WARN] Tunnel URL not detected yet. Check logs: $tunnelLog and $tunnelErr"
}
