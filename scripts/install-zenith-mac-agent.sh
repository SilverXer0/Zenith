#!/bin/zsh

set -euo pipefail

root="${0:A:h:h}"
label="com.zenith.manager"
plist_dir="$HOME/Library/LaunchAgents"
plist="$plist_dir/$label.plist"
domain="gui/$(id -u)"

if [[ "${1:-}" == "--remove" && $# -eq 1 ]]; then
  launchctl bootout "$domain/$label" 2>/dev/null || true
  rm -f "$plist"
  print "Zenith will no longer start automatically at Mac login."
  exit 0
fi

if [[ $# -gt 0 || ! -x "$root/scripts/start-zenith-mac.sh" ]]; then
  print -u2 "Usage: zsh ./scripts/install-zenith-mac-agent.sh [--remove]"
  exit 1
fi

mkdir -p "$plist_dir" "$root/data"
launchctl bootout "$domain/$label" 2>/dev/null || true

/usr/bin/python3 - "$plist" "$root" "$label" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path, root, label = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": ["/bin/zsh", f"{root}/scripts/start-zenith-mac.sh"],
    "WorkingDirectory": root,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "ZENITH_CONFIG_FILE": f"{root}/.env",
    },
    "StandardOutPath": f"{root}/data/zenith-launcher.log",
    "StandardErrorPath": f"{root}/data/zenith-launcher.error.log",
}
with Path(plist_path).open("wb") as handle:
    plistlib.dump(payload, handle, sort_keys=False)
PY

launchctl bootstrap "$domain" "$plist"
launchctl kickstart -k "$domain/$label"
print "Zenith is installed to start automatically at Mac login."
print "Check logs in $root/data/zenith-launcher.log and $root/data/zenith-launcher.error.log"
print "To remove it, run: zsh ./scripts/install-zenith-mac-agent.sh --remove"
