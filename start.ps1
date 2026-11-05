# Paatti one-click launcher (Windows). Double-click start.bat (it runs this file).
# Installs everything, starts Paatti, opens a public HTTPS link (needed for the phone mic)
# and opens it in your browser. Keep the window open during the demo; close it to stop.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force .tools | Out-Null

function Say($m) { Write-Host "`n==> $m" -ForegroundColor Green }
function Die($m) { Write-Host "`n$m" -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
function Run($exe, [string[]]$argv) {
  & $exe @argv
  if ($LASTEXITCODE -ne 0) { Die "Step failed: $exe $($argv -join ' ')" }
}

# Python 3.11+ (the "py" launcher first, then python on PATH)
$py = $null
foreach ($c in @("py", "python")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) {
    & $c -c "import sys; sys.exit(sys.version_info < (3, 11))" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
  }
}
if (-not $py) { Die "Python 3.11+ is missing. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run again." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Die "Node.js is missing. Install the LTS version from https://nodejs.org/ and run again." }

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Say "Created .env (add GEMINI_API_KEY / SARVAM_API_KEY there for AI replies and real voice; optional)"
}

Say "Installing the backend (first run takes a few minutes)"
if (-not (Test-Path .venv)) { Run $py @("-m", "venv", ".venv") }
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Run $vpy @("-m", "pip", "install", "-q", "--upgrade", "pip")
Run $vpy @("-m", "pip", "install", "-q", "-e", "backend")

Say "Building the dashboard"
Push-Location frontend
Run "npm.cmd" @("install", "--silent", "--no-fund", "--no-audit")
Run "npm.cmd" @("run", "build", "--silent")
Pop-Location

$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($cf) { $cf = $cf.Source } else {
  $cf = Join-Path $PSScriptRoot ".tools\cloudflared.exe"
  if (-not (Test-Path $cf)) {
    Say "Downloading cloudflared (makes the public HTTPS link)"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -OutFile $cf "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
  }
}

Say "Opening the public link"
$log = Join-Path $PSScriptRoot ".tools\tunnel.log"
if (Test-Path $log) { Remove-Item $log }
$tunnel = Start-Process -FilePath $cf -ArgumentList "tunnel --no-autoupdate --url http://localhost:8000" `
  -RedirectStandardError $log -RedirectStandardOutput "$log.out" -WindowStyle Hidden -PassThru
$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
  Start-Sleep -Seconds 1
  if (Test-Path $log) {
    $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value }
  }
}
if ($url) { $env:PAATTI_PUBLIC_URL = $url; $open = $url } else {
  $open = "http://localhost:8000"
  Write-Host "`nCould not create the public link (see .tools\tunnel.log). The laptop works on $open, but the phone QR needs the public link." -ForegroundColor Yellow
}

Write-Host "`n================================================================" -ForegroundColor Cyan
Write-Host "  Paatti is starting. OPEN THIS LINK:`n`n    $open`n" -ForegroundColor Cyan
Write-Host "  Laptop: dashboard. Phone: click 'Phone call via QR' and scan." -ForegroundColor Cyan
Write-Host "  Keep this window open. Close it to stop." -ForegroundColor Cyan
Write-Host "================================================================`n" -ForegroundColor Cyan

Start-Job -ScriptBlock { param($u) Start-Sleep 6; Start-Process $u } -ArgumentList $open | Out-Null
try {
  Set-Location backend
  & $vpy -m uvicorn paatti.main:app --host 0.0.0.0 --port 8000
} finally {
  if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
}
