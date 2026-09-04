# Windows home-server cutover

This is the controlled path from the working Node prototype to the Python/FastAPI core plus Next frontend. The Node launcher remains the rollback path. Run only one Zenith runtime against a live database at a time.

## Prerequisites

- Windows 10/11.
- Python 3.12 with the Python Launcher, py.exe.
- Node.js 20.9 or newer, including npm.
- Tailscale installed, signed in, and connected on the Windows PC and client devices.
- MagicDNS and Tailscale HTTPS enabled before using the HTTPS launcher.

Ollama/Qwen, Google Calendar credentials, Whisper, and TTS are optional. The core and frontend do not require them to start.

## First setup

From the repository folder in PowerShell:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-zenith-python.ps1
~~~

This creates backend\.venv, installs the pinned Python runtime packages, installs the Next packages, and creates a production frontend build. It does not install or download Ollama, Qwen, Whisper, or TTS.

After setup, run the read-only preflight:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-zenith-windows.ps1 -TailscaleHttps
~~~

It checks the required versions, backend environment, Next build, data directory, free ports, and Tailscale identity without starting or modifying Zenith. Omit the TailscaleHttps switch when verifying local-only startup.

Before the first Python launch, stop the Node server and make a backup of the current data. The Python API is schema-compatible with the Node database, but the two servers must never write to it simultaneously. A simple stopped-server backup is:

~~~powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item .\data\zenith.sqlite ".\data\zenith.sqlite.before-python-$stamp"
~~~

The launcher uses data\ by default so existing tasks, accounts, sessions, memory, and Calendar connection state remain available. To test with a copy instead, pass a separate directory:

~~~powershell
Copy-Item .\data\zenith.sqlite .\data-python\zenith.sqlite
powershell -ExecutionPolicy Bypass -File .\scripts\start-zenith-python.ps1 -DataDir data-python
~~~

## Local verification

Start the Python runtime locally:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-zenith-python.ps1
~~~

Open http://localhost:3000 and verify:

1. /api/health reports SQLite storage.
2. Existing credentials sign in and existing tasks are present.
3. Create, edit, complete, reopen, and delete a task.
4. Open the page on another device or browser session and confirm changes arrive without a refresh.
5. Planning, memory, voice-unconfigured, and Calendar-unconfigured states remain usable.
6. Assistant status can be offline without affecting task access.

The API log is written to data\zenith-python-api.log; startup errors go to data\zenith-python-api.error.log.

## Private Tailscale HTTPS verification

After local verification, stop the local launcher and run:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-zenith-python.ps1 -TailscaleHttps
~~~

The launcher binds both local processes to loopback, configures Tailscale Serve to forward private HTTPS traffic to Next on port 3000, and prints the private https://...ts.net URL. It also sets the trusted browser origin and Secure session cookies for that URL. Tailscale Serve remains configured for port 3000 after the launcher exits; the existing Node HTTPS launcher can use the same route during rollback.

On the Mac and phone, open the printed HTTPS URL and verify:

- Sign-in succeeds with the same local account.
- Tasks added or changed on one device appear on the other without refreshing.
- The PWA install option is available where the browser supports it.
- Browser reminders can be enabled and a due/overdue task produces at most one reminder per task per local day.
- Calendar and voice show honest unavailable states when their optional services are not configured.

Use the HTTPS URL, not a raw http://100.x.x.x Tailscale address, for PWA installation and browser notifications.

## Optional local services

Set Ollama variables in the same PowerShell window before launching if the local model is installed:

~~~powershell
$env:OLLAMA_URL = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "your-installed-qwen-model"
powershell -ExecutionPolicy Bypass -File .\scripts\start-zenith-python.ps1 -TailscaleHttps
~~~

If Ollama is stopped or the model is released, tasks, Calendar state, planning, memory, voice status, and live sync must continue to work. Configure Whisper and TTS only after the core cutover is stable; the adapter instructions are in the backend guide.

## Rollback

1. Stop the Python launcher with Ctrl+C.
2. Confirm no Python API process is still using the database.
3. Start the existing Node launcher:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-zenith-tailscale-https.ps1
~~~

For local/LAN testing without private HTTPS, use .\scripts\start-zenith-tailscale.ps1 instead. Do not start either Node launcher while the Python launcher is still running.

This cutover does not register a Windows service or Task Scheduler job yet. Keep the launcher window open while verifying the real devices; automatic startup is a later operational-hardening step.
