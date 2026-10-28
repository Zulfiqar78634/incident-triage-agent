# IncidentIQ local demonstration

This upgrade has not been browser-tested or live-Gemini-tested by the assistant. Run the checks below before presenting. The spatial visualization uses CSS perspective and transforms, not WebGL or discovered infrastructure topology. It needs no external CDN. The existing start endpoint is synchronous: the UI shows a waiting state and then renders recorded results, not invented real-time progress.

## Windows PowerShell

Use a new directory so you do not overwrite earlier local edits:

```powershell
git clone https://github.com/Zulfiqar78634/incident-triage-agent.git incidentiq-demo
cd incidentiq-demo
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1
```

Enter a NEW Gemini key only in local `.env`; do not reuse a disclosed key and never commit it. Leave the key blank for offline mock mode. Restart the server after changing `.env`. Model availability depends on your account; change GEMINI_MODEL if needed. Environment variables already set in the shell take precedence over `.env`.

Open http://127.0.0.1:8000 and hard-refresh with Ctrl+F5. Start ALERT-001. Inspect model execution events, evidence, and the approval gate. Try approval and rejection on separate fresh runs. Confidence in mock mode is fixed rule output, not measured accuracy. A Gemini error creates an llm_failed event and escalates to a ticket; it does not silently fall back to mock mode.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.eval
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/model
```

Expected: health returns ok; model endpoint shows configuration without secrets; a new run records llm_completed for successful diagnosis or llm_failed for model/validation failures. A configured model alone is not proof of a successful model call. Existing observability/remediation tools remain demo integrations; no real production restart is established by this visualization.

Known scope: runtime.py still exports its original helper functions for compatibility; the LLM adapter now imports independent helpers from context.py to remove the circular dependency. Existing run-to-completion and approval policy should be reviewed separately before any production integration. This is a local demonstration, not a hardened multi-user operations service.
