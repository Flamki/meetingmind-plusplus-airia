$ErrorActionPreference = "SilentlyContinue"

$patterns = @(
  "streamlit\s+run\s+\.\\meetingmind_dashboard\.py",
  "cloudflared\.exe\s+tunnel\s+--url\s+http://localhost",
  "npx\s+--yes\s+localtunnel",
  "localtunnel\s+--port"
)

Get-CimInstance Win32_Process | ForEach-Object {
  $cmd = $_.CommandLine
  if (-not $cmd) { return }
  foreach ($pattern in $patterns) {
    if ($cmd -match $pattern) {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      break
    }
  }
}

Write-Host "[DONE] Stopped live Streamlit/tunnel processes (if any)." -ForegroundColor Green
