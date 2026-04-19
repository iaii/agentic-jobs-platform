# Slack Integration Setup Guide

This guide walks through configuring the Slack app for the **Agentic Job Search Copilot**. The system uses Socket Mode for real-time interaction — no public URL or ngrok required.

## Prerequisites

- PostgreSQL running and `DATABASE_URL` configured
- Python 3.11+ with dependencies installed (`pip install -r requirements.txt`)
- Slack workspace with admin access

---

## Step 1: Create and configure the Slack app

### 1.1 Go to the Slack API dashboard

Visit [https://api.slack.com/apps](https://api.slack.com/apps) and create a new app (or select an existing one).

### 1.2 Enable Socket Mode

1. Click **Socket Mode** in the left sidebar.
2. Toggle **Enable Socket Mode** to **ON**.

Socket Mode maintains a persistent WebSocket connection to Slack, so the server receives interactive component events without exposing a public endpoint.

### 1.3 Collect your tokens

#### Bot User OAuth Token (`SLACK_BOT_TOKEN`)
1. Go to **OAuth & Permissions**.
2. Under **Scopes → Bot Token Scopes**, add:
   - `chat:write` — post messages and interactive components
   - `channels:read` — read channel metadata
   - `groups:read` — read private channel metadata
   - `channels:history` — read message history (digest dedup)
   - `users:read` — resolve user names in application threads
   - `pins:write` — manage the pinned master tracker message
3. Click **Install to Workspace**.
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`).

#### App-Level Token (`SLACK_APP_LEVEL_TOKEN`)
1. Go to **Basic Information → App-Level Tokens**.
2. Click **Generate Token and Scopes**, name it (e.g., "Socket Mode Token"), and add:
   - `connections:write`
   - `authorizations:read`
3. Copy the token (starts with `xapp-`).

#### Signing Secret (`SLACK_SIGNING_SECRET`)
1. Go to **Basic Information → App Credentials**.
2. Copy the **Signing Secret**.

### 1.4 Enable Interactivity

1. Go to **Interactivity & Shortcuts**.
2. Toggle **Interactivity** to **ON**.
3. No Request URL is needed — Socket Mode handles all interactions.

---

## Step 2: Configure environment variables

Copy the template and fill in your values:

```bash
cp env_template.sh env_local.sh
```

Minimum required Slack settings in `env_local.sh`:

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_LEVEL_TOKEN="xapp-..."
export SLACK_SIGNING_SECRET="..."

# Channel IDs (not names — use the ID from the channel URL)
export SLACK_JOBS_FEED_CHANNEL="C..."      # job digests + domain review cards
export SLACK_JOBS_DRAFTS_CHANNEL="C..."    # per-application cover letter threads
export SLACK_JOBS_TRACKER_CHANNEL="C..."   # pinned master tracker view
export SLACK_JOBS_ARCHIVE_CHANNEL="C..."   # accepted / rejected outcomes
```

Load and start:

```bash
source env_local.sh
./start_server.sh
```

---

## Step 3: Verify the connection

### Health check
```bash
curl http://localhost:8000/healthz
# → {"status":"ok"}
```

### Test Slack config
```bash
python3 test_slack_config.py
```
Expected:
```
✅ SLACK_BOT_TOKEN is set: xoxb-...
✅ SLACK_APP_LEVEL_TOKEN is set: xapp-...
✅ SLACK_SIGNING_SECRET is set: ...
✅ All environment variables are properly configured!
```

### Test Slack connection
```bash
python3 test_slack_connection.py
```
Expected:
```
✅ Bot Token is valid!
✅ Socket Mode connection successful!
```

### Server startup logs
```
INFO: Slack socket mode client connected.
INFO: Application startup complete.
INFO: Uvicorn running on http://0.0.0.0:8000
```

---

## Step 4: End-to-end workflow test

### Trigger discovery
```bash
curl -X POST http://localhost:8000/api/v1/discover/run \
  -H 'content-type: application/json' -d '{}'
```

### In Slack
1. Check `SLACK_JOBS_FEED_CHANNEL` for a job digest with **Open JD** and **Save to Tracker** buttons.
2. Click **Save to Tracker** — an application card should appear in `SLACK_JOBS_DRAFTS_CHANNEL` with a cover letter thread.
3. Click **Quick Draft** or **Generate CL** — a draft should appear in the thread within ~15–90 seconds depending on the backend.
4. Unknown domains trigger **Needs Review** cards with **Approve** / **Reject** buttons.
5. Check `SLACK_JOBS_TRACKER_CHANNEL` for the pinned master tracker, which updates automatically.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Socket Mode connection timed out | Verify `SLACK_APP_LEVEL_TOKEN` and that Socket Mode is enabled in the app settings |
| Bot token validation failed | Check token starts with `xoxb-`; reinstall app to workspace; verify scopes |
| Save to Tracker shows error symbol | Check server logs; verify PostgreSQL is running; run `python3 test_slack_config.py` |
| Server won't start | `pkill -f uvicorn`; check `psql -d agentic_jobs -c "SELECT 1;"`; verify `pip install -r requirements.txt` |
| Master tracker not updating | Confirm `SLACK_JOBS_TRACKER_CHANNEL` is set to the channel **ID**, not the name |

---

## Quick reference

```bash
# Start
source env_local.sh && ./start_server.sh

# Stop
pkill -f uvicorn

# Manual discovery trigger
curl -X POST http://localhost:8000/api/v1/discover/run -H 'content-type: application/json' -d '{}'

# Test config
python3 test_slack_config.py && python3 test_slack_connection.py
```

---

## Channel setup summary

| Channel | Suggested name | Purpose |
|---------|---------------|---------|
| Feed | `#jobs-feed` | Digest posts + domain review cards |
| Drafts | `#jobs-drafts` | Per-application threads, cover letter drafts |
| Tracker | `#jobs-tracker` | Pinned master tracker (one message, auto-updated) |
| Archive | `#jobs-archive` | Final outcomes (Accepted / Rejected) |
| Autofill Ops _(optional)_ | `#autofill-ops` | Autofill task progress and errors |
