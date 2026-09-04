$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venv = Join-Path $backend ".venv"
$python = Join-Path $venv "Scripts\python.exe"

$pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
  throw "Python Launcher was not found. Install Python 3.12 for Windows, then run this setup again."
}

if (-not (Test-Path -LiteralPath $python)) {
  Write-Host "Creating the Python backend environment..."
  & $pythonLauncher.Source -3.12 -m venv $venv
  if ($LASTEXITCODE -ne 0) { throw "Python 3.12 could not create backend\.venv." }
}

Write-Host "Installing the Python backend packages..."
& $python -m pip install --disable-pip-version-check -r (Join-Path $backend "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { $npmCommand = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npmCommand) {
  throw "npm was not found. Install Node.js 20.9 or newer, then run this setup again."
}
$npmPath = $npmCommand.Source
if (-not $npmPath) { $npmPath = $npmCommand.Path }

Write-Host "Installing the Next frontend packages..."
& $npmPath --prefix $frontend install
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }

Write-Host "Building the Next frontend..."
& $npmPath --prefix $frontend run build:webpack
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }

New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null
Write-Host "Zenith Python setup is ready."
Write-Host "The setup does not install Ollama, Qwen, Whisper, or TTS packages; those remain optional local services."
