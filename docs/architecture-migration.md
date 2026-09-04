# Target architecture and migration gates

Zenith is a personal manager, not an autonomous executor. Its target remains a Windows-hosted Python/FastAPI core, SQLite source of truth, a responsive Next.js + TypeScript + Tailwind frontend, private Tailscale access, and optional local Ollama/Qwen. This plan does not redefine the completed Node prototype as the requested final architecture.

## Current evidence

| Requirement | Current implementation | Remaining evidence/work |
| --- | --- | --- |
| Persistent tasks, manual edits, inbox capture, due dates, priorities and projects | Node/SQLite and responsive plain-JavaScript UI; Python auth/task API foundation | Port the UI to the target stack and verify end-to-end parity |
| User/session boundaries and legacy migration | Node implementation; Python compatibility and isolation tests | Complete authentication hardening and deployment checks during cutover |
| Cross-device access | Tailscale startup scripts and Node browser sync tests; Python per-user task events tested with real HTTP streams | Full Python/frontend parity, then Windows/Mac/real-phone verification |
| Google Calendar read access | Node OAuth/read endpoints and UI | Python port, actual OAuth setup and schedule/timezone verification |
| Local Qwen, natural-language commands, unloadable model | Optional Node Ollama adapter, confirmation-required task actions | Python adapter, real model tests and unload/VRAM verification on Windows |
| Speech input and spoken replies | Optional local adapters in the prototype | Python API port and real device microphone/speaker testing |
| Planning intelligence | Node task/calendar views; Python task focus, morning, weekly and completion-summary projections | Python Calendar port, timezone-aware availability/conflict planning and answer-quality checks |
| Persistent context | User-managed SQLite notes; Python CRUD and cross-runtime data compatibility verified | Include it in the Python assistant port and target UI; verify useful ongoing context |
| PWA and reminders | Static service worker/manifest and open-page browser reminders | Next.js PWA integration, installation tests and closed-app delivery decision |
| Core independent of AI | Existing Node operations work without Ollama; Python foundation has no model dependency | Preserve this through every port and verify on the home server |
| No hosted model billing | No paid hosted model calls in the prototype or Python foundation | Keep local-model boundary throughout implementation |

## Sequence: independently testable commits

1. **Python core foundation:** existing SQLite schema, authentication, task CRUD, migrations, completion history and cross-runtime tests. Development path only; Node remains the working service.
2. **Python live task events (implemented in development):** matching `ready`/`tasks_changed` contract, per-user notices, fresh-snapshot reconnects, session expiry/logout checks, bounded subscriber state and disconnect cleanup. Real HTTP stream and broker/ASGI tests pass; full frontend and Tailscale verification remain cutover gates.
3. **Existing core API parity (in progress):** memory and task-only briefing/planning/summary routes are implemented and compared with Node. Next port optional Calendar/Ollama/voice services, with confirmation still required for every model-proposed mutation. Connected Calendar state is retained but explicitly unavailable until its event client exists; do not substitute fake “available” statuses for missing integrations.
4. **Target frontend:** build the Next.js/TypeScript/Tailwind app against the stable API. Preserve quick capture, manual editing, live sync, mobile access, input drafts and error recovery. Keep the API same-origin behind the private entry point.
5. **Windows cutover:** explicit Python/package setup, data backup, startup configuration, private HTTPS/Tailscale routing and rollback instructions. Verify Windows, Mac and phone before retiring the Node runtime. The two implementations are not a permanent dual-server architecture.
6. **Management intelligence and remaining user experience:** calendar-aware availability/conflicts, context-assisted planning, voice usability, PWA installation, notification delivery, and useful briefing/planning/summary flows, tested on the actual setup.

The Node frontend is a transitional reference and rollback path, not a reason to omit Next.js. Likewise, the partial Python backend is not a claim of feature parity. Passing one slice does not complete the full goal.
