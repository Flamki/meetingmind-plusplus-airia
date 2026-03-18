$ErrorActionPreference = "SilentlyContinue"

$targets = @("streamlit run .\\meetingmind_dashboard.py", "localtunnel --port", "npx --yes localtunnel")

Get-CimInstance Win32_Process | ForEach-Object {
  $cmd = $_.CommandLine
  if (-not $cmd) { return }
  foreach ($t in $targets) {
    if ($cmd -like "*$t*") {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      break
    }
  }
}

Write-Host "[DONE] Stopped live Streamlit/tunnel processes (if any)." -ForegroundColor Green
