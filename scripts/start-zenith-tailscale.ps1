$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
  throw "Tailscale was not found. Install it, sign in, and run this script again."
}

$tailscaleIp = (& $tailscale.Source ip -4 | Select-Object -First 1).Trim()
if (-not $tailscaleIp -or $tailscaleIp -notmatch '^100\.') {
  throw "Could not find a Tailscale IPv4 address. Make sure Tailscale is running and this PC is connected."
}

$env:ZENITH_HOST = $tailscaleIp
Write-Host "Zenith will be available to your tailnet at http://${tailscaleIp}:3000"
npm start
