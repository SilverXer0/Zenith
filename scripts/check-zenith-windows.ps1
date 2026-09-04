param(
  [switch]$TailscaleHttps,
  [string]$DataDir = "data"
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

function Pass($message) { Write-Host "PASS  $message" -ForegroundColor Green }
function Warn($message) { [void]$warnings.Add($message); Write-Host "WARN  $message" -ForegroundColor Yellow }
function Fail($message) { [void]$failures.Add($message); Write-Host "FAIL  $message" -ForegroundColor Red }

function CommandPath($names) {
  foreach ($name in $names) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command) {
      $commandPath = $command.Source
      if (-not $commandPath) { $commandPath = $command.Path }
      if ($commandPath) { return $commandPath }
    }
  }
  return $null
}

function VersionFrom($text) {
  $match = [regex]::Match([string]$text, "\d+\.\d+(\.\d+)?")
  if (-not $match.Success) { return $null }
  try { return [version]$match.Value } catch { return $null }
}

Write-Host "Zenith Windows preflight"
Write-Host "Repository: $root"

$nodePath = CommandPath @("node.exe", "node")
if (-not $nodePath) {
  Fail "Node.js was not found. Install Node.js 20.9 or newer."
} else {
  $nodeVersion = VersionFrom (& $nodePath --version 2>&1)
  if (-not $nodeVersion -or $nodeVersion -lt [version]"20.9.0") { Fail "Node.js 20.9 or newer is required." }
  else { Pass "Node.js $nodeVersion" }
}

$npmPath = CommandPath @("npm.cmd", "npm")
if (-not $npmPath) { Fail "npm was not found." } else { Pass "npm is available" }

$pythonLauncher = CommandPath @("py.exe", "py")
if (-not $pythonLauncher) {
  Fail "Python Launcher was not found. Install Python 3.12."
} else {
  $pythonVersion = (& $pythonLauncher -3.12 --version 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0 -or -not (VersionFrom $pythonVersion)) { Fail "Python 3.12 was not found through py.exe." }
  else { Pass "Python 3.12 is available" }
}

$python = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { Fail "backend\.venv is missing. Run setup-zenith-python.ps1." }
else { Pass "Python backend environment exists" }

$buildId = Join-Path $root "frontend\.next\BUILD_ID"
if (-not (Test-Path -LiteralPath $buildId)) { Fail "The Next production build is missing. Run setup-zenith-python.ps1." }
else { Pass "Next production build exists" }

if (-not (Test-Path -LiteralPath (Join-Path $root "frontend\node_modules\next"))) { Fail "Next dependencies are missing. Run setup-zenith-python.ps1." }
else { Pass "Next dependencies exist" }

if ([System.IO.Path]::IsPathRooted($DataDir)) { $dataPath = $DataDir }
else { $dataPath = Join-Path $root $DataDir }
if (-not (Test-Path -LiteralPath $dataPath)) {
  Warn "Data directory does not exist yet: $dataPath. A first launch will create it."
} else {
  Pass "Data directory exists: $dataPath"
  $database = Join-Path $dataPath "zenith.sqlite"
  if (Test-Path -LiteralPath $database) { Pass "SQLite database exists" }
  else { Warn "No zenith.sqlite was found; this launch will start a new local database." }
}

foreach ($port in @(3000, 8000)) {
  $connection = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($connection) { Fail "Port $port is already in use. Stop the other Zenith runtime first." }
  else { Pass "Port $port is available" }
}

if ($TailscaleHttps) {
  $tailscalePath = CommandPath @("tailscale.exe", "tailscale")
  if (-not $tailscalePath) {
    Fail "Tailscale was not found."
  } else {
    $statusJson = (& $tailscalePath status --json 2>$null | Out-String)
    if (-not $statusJson.Trim()) {
      Fail "Tailscale did not return status. Sign in and connect this PC."
    } else {
      try {
        $status = $statusJson | ConvertFrom-Json
        $dnsName = [string]$status.Self.DNSName
        if (-not $dnsName) { Fail "Tailscale DNS name is missing. Enable MagicDNS." }
        else { Pass "Tailscale connected as $($dnsName.TrimEnd("."))" }
      } catch { Fail "Tailscale returned unreadable status." }
    }
  }
}

if ($warnings.Count -gt 0) { Write-Host ""; Write-Host "$($warnings.Count) warning(s) reported." -ForegroundColor Yellow }
if ($failures.Count -gt 0) {
  Write-Host "$($failures.Count) check(s) failed. Fix them before launching Zenith." -ForegroundColor Red
  exit 1
}
Write-Host "Preflight passed. Zenith is ready for the selected launch mode." -ForegroundColor Green
