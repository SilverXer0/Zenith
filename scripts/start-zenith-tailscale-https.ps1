$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
  throw "Tailscale was not found. Install it, sign in, and run this script again."
}

$tailscalePath = $tailscale.Source
if (-not $tailscalePath) { $tailscalePath = $tailscale.Path }
$statusJson = (& $tailscalePath status --json | Out-String)
if (-not $statusJson.Trim()) {
  throw "Tailscale did not return device status. Make sure Tailscale is running and this PC is connected."
}
$status = $statusJson | ConvertFrom-Json
$dnsName = [string]$status.Self.DNSName
if (-not $dnsName) {
  throw "Could not find this PC's Tailscale DNS name. Enable MagicDNS and try again."
}
$dnsName = $dnsName.TrimEnd('.')
$publicUrl = "https://$dnsName"

Write-Host "Configuring private Tailscale HTTPS access..."
& $tailscalePath serve --https=443 http://127.0.0.1:3000 --bg
if ($LASTEXITCODE -ne 0) {
  throw "Tailscale Serve could not be configured. It may need HTTPS enabled in the Tailscale admin console."
}

$env:ZENITH_HOST = "127.0.0.1"
if (-not $env:GOOGLE_REDIRECT_URI) {
  $env:GOOGLE_REDIRECT_URI = "$publicUrl/api/calendar/oauth/callback"
}
Write-Host "Zenith will be available privately at $publicUrl"
Write-Host "Google OAuth callback: $env:GOOGLE_REDIRECT_URI"
Write-Host "If this is your first time using Serve, follow any Tailscale approval link it displayed."
npm start
