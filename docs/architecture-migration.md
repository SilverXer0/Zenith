# Target architecture and migration gates

Zenith is a personal manager, not an autonomous executor. Its target remains a Windows-hosted Python/FastAPI core, SQLite source of truth, a responsive Next.js + TypeScript + Tailwind frontend, private Tailscale access, and optional local Ollama/Qwen. This plan does not redefine the completed Node prototype as the requested final architecture.

## Current evidence

| Requirement | Current implementation | Remaining evidence/work |
| --- | --- | --- |
| Persistent tasks, manual edits, inbox capture, due dates, priorities and projects | Node/SQLite and responsive plain-JavaScript UI; Python auth/task API foundation; target Next shell now covers task workflows | Complete remaining panels and verify end-to-end parity |
| User/session boundaries and legacy migration | Node implementation; Python compatibility and isolation tests; target frontend uses the authenticated API boundary | Complete authentication hardening and deployment checks during cutover |
| Cross-device access | Tailscale startup scripts and Node browser sync tests; Python per-user task events tested with real HTTP streams; target frontend consumes the authenticated event stream | Full Python/frontend parity, then Windows/Mac/real-phone verification |
| Google Calendar read access | Node UI; Node and Python OAuth/read APIs with shared SQLite data | Actual Google OAuth on Windows/Tailscale, frontend cutover and schedule/timezone verification |
| Local Qwen, natural-language commands, unloadable model | Node adapter plus Python loopback-only Ollama adapter; both require confirmation for task changes, and Python supports explicit unload | Real Qwen answer-quality and unload/VRAM verification on Windows; target frontend integration |
| Speech input and spoken replies | Optional local adapters in the Node prototype and bounded authenticated Python API | Real Whisper/Windows speech-engine execution and device microphone/speaker testing |
| Planning intelligence | Node and Python task/calendar morning and weekly views; Python task focus and completion summaries | Timezone-aware availability/conflict planning and answer-quality checks |
| Persistent context | User-managed SQLite notes; Python CRUD/cross-runtime compatibility and owner-only assistant context verified | Target UI and real-model usefulness checks |
| PWA and reminders | Static service worker/manifest and open-page browser reminders | Next.js PWA integration, installation tests and closed-app delivery decision |
| Core independent of AI | Existing Node and Python Core operations work without Ollama; assistant failures are isolated | Preserve this through the frontend/cutover and verify on the home server |
| No hosted model billing | No paid hosted model calls in the prototype or Python foundation | Keep local-model boundary throughout implementation |

## Sequence: independently testable commits

1. **Python core foundation:** existing SQLite schema, authentication, task CRUD, migrations, completion history and cross-runtime tests. Development path only; Node remains the working service.
2. **Python live task events (implemented in development):** matching `ready`/`tasks_changed` contract, per-user notices, fresh-snapshot reconnects, session expiry/logout checks, bounded subscriber state and disconnect cleanup. Real HTTP stream and broker/ASGI tests pass; full frontend and Tailscale verification remain cutover gates.
3. **Existing core API parity (in progress):** memory, briefing/planning/summary, optional read-only Calendar, local Ollama, and optional local voice routes are implemented. Calendar uses the shared schema, refreshes access, and contributes events without making task planning depend on Google. The assistant is loopback-only, receives owner-scoped context, produces bounded proposals, requires a separate confirmation request, applies confirmed groups atomically, and can unload a running model. Voice launches trusted local adapters with bounded recordings, outputs, timeouts, and temporary files. Automated model/voice checks use simulators, so real Qwen/VRAM/Whisper/device evidence remains a cutover gate. Next build the target frontend. Do not substitute fake “available” statuses for unavailable integrations.
4. **Target frontend (in progress):** the isolated Next.js/TypeScript/Tailwind shell now preserves setup/sign-in, quick capture, manual editing, live sync, mobile layout, input drafts, error recovery, assistant confirmation, and optional-model controls against the Python API. Add Calendar, context, briefing, voice, PWA, and notification panels in separate commits. Keep the API same-origin behind the private entry point.
5. **Windows cutover:** explicit Python/package setup, data backup, startup configuration, private HTTPS/Tailscale routing and rollback instructions. Verify Windows, Mac and phone before retiring the Node runtime. The two implementations are not a permanent dual-server architecture.
6. **Management intelligence and remaining user experience:** calendar-aware availability/conflicts, context-assisted planning, voice usability, PWA installation, notification delivery, and useful briefing/planning/summary flows, tested on the actual setup.

The Node frontend is a transitional reference and rollback path, not a reason to omit Next.js. Likewise, the partial Python backend is not a claim of feature parity. Passing one slice does not complete the full goal.
