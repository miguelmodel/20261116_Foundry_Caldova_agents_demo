# Caldova Control Tower

From the repository root, run `.\start.ps1`. The launcher uses `.venv`,
reads `APP_PORT` from `.env` (default `8000`), checks Azure CLI login, and
opens the local UI. Stop it with **Ctrl+C**. No frontend build is required.
If the port is already in use, an earlier instance is probably still running;
open `http://127.0.0.1:8000` instead of starting another one.

The UI starts without `.env` or Azure access. Health failures are reported
without breaking the application, and failed runs finish with `run.error`.
**There is no mock-agent mode.** Configure real resources with the repository's
deployment scripts before running the agents.

## Modes

- **A2A native:** (1) the Coordinator's delegate version (A2A tool only,
  `tool_choice=required`) delegates to the hosted Analyst via Foundry A2A;
  (2) the Coordinator's policy version receives `[PHASE: FINAL]` with the
  Analyst's A2A report and uses File Search. Delegated tool calls are not
  individually streamed; the UI shows the delegation and the returned report.
- **Backend orchestrated:** Coordinator `[PHASE: BRIEF]` → hosted Analyst →
  Coordinator `[PHASE: FINAL]`. Analyst stream events are visible live.

The async SDK client is created with `allow_preview=True` so its
`get_openai_client(agent_name=...)` can address the hosted agent.
Coordinator versions are sent through `extra_body.agent_reference`; a blank
version uses the service's default/latest version.

`GET /api/config`, `GET /api/health`, and `POST /api/run` implement the app
contract. The POST response is SSE, including ISO timestamps, quiet-period
heartbeats, compact `raw` SDK events, and terminal completion/error events.
Responses item calls map to File Search, Web Search, MCP, functions,
Code Interpreter, and A2A; `*_call_output` items (for example
`a2a_preview_call_output`) complete the matching call by `call_id`.
Unknown call types appear as generic functions.

Each run has an OpenTelemetry root span and a 32-character trace ID.
Application Insights export is best effort; a displayed local trace ID does
not imply successful cloud ingestion. Open the project in Foundry, then
**Agents → caldova-coordinator → Traces**, and search trace/response IDs
(allow approximately 2–5 minutes for ingestion).

Model output is HTML-escaped before lightweight Markdown rendering.
Clipboard buttons work on localhost. Keyboard shortcut: **Ctrl+Enter**.

Validation:

```powershell
.\.venv\Scripts\python.exe -m unittest app.backend.test_foundry
.\.venv\Scripts\python.exe -m py_compile app\backend\main.py app\backend\foundry.py app\backend\settings.py app\backend\telemetry.py
```
