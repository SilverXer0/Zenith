param(
  [switch]$TailscaleHttps,
  [string]$DataDir = "data"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  throw "Python setup is missing. Run powershell -ExecutionPolicy Bypass -File .\scripts\setup-zenith-python.ps1 first."
}

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { $npmCommand = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npmCommand) { throw "npm was not found. Install Node.js 20.9 or newer." }
$npmPath = $npmCommand.Source
if (-not $npmPath) { $npmPath = $npmCommand.Path }

if ([System.IO.Path]::IsPathRooted($DataDir)) {
  $dataPath = $DataDir
} else {
  $dataPath = Join-Path $root $DataDir
}
New-Item -ItemType Directory -Force -Path $dataPath | Out-Null

$allowedOrigins = @("http://localhost:3000", "http://127.0.0.1:3000")
$tailscalePath = $null
$publicUrl = $null
if ($TailscaleHttps) {
  $tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
  if (-not $tailscale) { $tailscale = Get-Command tailscale -ErrorAction SilentlyContinue }
  if (-not $tailscale) { throw "Tailscale was not found. Install it, sign in, and run this script again." }
  $tailscalePath = $tailscale.Source
  if (-not $tailscalePath) { $tailscalePath = $tailscale.Path }

  $statusJson = (& $tailscalePath status --json | Out-String)
  if (-not $statusJson.Trim()) { throw "Tailscale did not return device status. Make sure this PC is connected." }
  $status = $statusJson | ConvertFrom-Json
  $dnsName = [string]$status.Self.DNSName
  if (-not $dnsName) { throw "Enable MagicDNS and try again so Zenith can use a private HTTPS name." }
  $publicUrl = "https://" + $dnsName.TrimEnd(".")
  $allowedOrigins = @($publicUrl) + $allowedOrigins

  Write-Host "Configuring private Tailscale HTTPS access..."
  # Current Tailscale Serve syntax uses the local port as the target and
  # places --bg before it. HTTPS on port 443 is the default.
  & $tailscalePath serve --bg 3000
  if ($LASTEXITCODE -ne 0) { throw "Tailscale Serve could not be configured. Enable HTTPS for this tailnet." }
  Write-Host "Zenith will be available privately at $publicUrl"
  if (-not $env:GOOGLE_REDIRECT_URI) {
    $env:GOOGLE_REDIRECT_URI = "$publicUrl/api/calendar/oauth/callback"
  }
  $env:ZENITH_COOKIE_SECURE = "true"
} else {
  Remove-Item Env:ZENITH_COOKIE_SECURE -ErrorAction SilentlyContinue
  Write-Host "Zenith will be available locally at http://localhost:3000"
}

$env:ZENITH_DATA_DIR = $dataPath
$env:ZENITH_ALLOWED_ORIGINS = ($allowedOrigins -join ",")
$env:ZENITH_API_ORIGIN = "http://127.0.0.1:8000"
$apiLog = Join-Path $dataPath "zenith-python-api.log"
$apiErrorLog = Join-Path $dataPath "zenith-python-api.error.log"

Write-Host "Starting the Python API..."
$api = Start-Process -FilePath $python -WorkingDirectory $root -ArgumentList @(
  "-m", "uvicorn", "backend.app:create_app", "--factory",
  "--host", "127.0.0.1", "--port", "8000"
) -RedirectStandardOutput $apiLog -RedirectStandardError $apiErrorLog -PassThru

try {
  $ready = $false
  for ($attempt = 0; $attempt -lt 60; $attempt += 1) {
    try {
      $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
      if ($health.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    if ($api.HasExited) { throw "The Python API stopped during startup. See $apiErrorLog." }
    Start-Sleep -Milliseconds 500
  }
  if (-not $ready) { throw "The Python API did not become ready. See $apiErrorLog." }

  Write-Host "Starting the Next frontend. Keep this window open while Zenith is running."
  & $npmPath --prefix $frontend run start -- --hostname 127.0.0.1 --port 3000
  if ($LASTEXITCODE -ne 0) { throw "The Next frontend stopped with exit code $LASTEXITCODE." }
} finally {
  if ($api -and -not $api.HasExited) {
    Stop-Process -Id $api.Id -Force
  }
  Write-Host "Zenith processes stopped. The existing Node launcher remains available for rollback."
}
