$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
  throw "Tailscale was not found. Install it, sign in, and run this script again."
}

$tailscaleIp = (& $tailscale.Source ip -4 | Select-Object -First 1)
if ($tailscaleIp) {
  $tailscaleIp = $tailscaleIp.Trim()
}
if (-not $tailscaleIp -or $tailscaleIp -notmatch '^100\.') {
  throw "Could not find a Tailscale IPv4 address. Open Tailscale, sign in, and make sure this PC shows as connected, then run the script again. You can verify it with: tailscale ip -4"
}

$env:ZENITH_HOST = $tailscaleIp
Write-Host "Zenith will be available to your tailnet at http://${tailscaleIp}:3000"
npm start
