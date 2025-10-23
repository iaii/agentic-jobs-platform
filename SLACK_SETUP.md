# Slack Integration Setup Guide

This guide will help you set up and test your Slack integration for the Agentic Jobs Platform. The system includes interactive components, automated digests, domain review workflows, and application tracking.

## Prerequisites

- PostgreSQL database running (✅ Already set up!)
- Slack workspace with admin access
- Python 3.14 installed (✅ Already installed!)
- Agentic Jobs Platform running (✅ Core system implemented!)

## Step 1: Configure Your Slack App

### 1.1 Go to Slack API Dashboard
Visit: https://api.slack.com/apps

### 1.2 Select Your App
Click on your "Agentic Jobs Platform" app (or create a new one if you haven't yet)

### 1.3 Enable Socket Mode
1. Click on **"Socket Mode"** in the left sidebar
2. Toggle **"Enable Socket Mode"** to **ON**
3. This allows your app to receive events without needing a public URL (no ngrok needed!)
4. **Socket Mode is required** for the Agentic Jobs Platform to receive interactive component events

### 1.4 Get Your Tokens

#### Bot User OAuth Token (SLACK_BOT_TOKEN)
1. Go to **"OAuth & Permissions"** in the left sidebar
2. Under **"Scopes"**, add these Bot Token Scopes:
   - `chat:write` - Post messages and interactive components
   - `channels:read` - View basic channel info
   - `groups:read` - View basic private channel info
   - `channels:history` - Read message history for digests
   - `users:read` - Read user information for application tracking
3. Click **"Install to Workspace"** (or reinstall if already installed)
4. Copy the **"Bot User OAuth Token"** (starts with `xoxb-`)

#### App-Level Token (SLACK_APP_LEVEL_TOKEN)
1. Go to **"Basic Information"** in the left sidebar
2. Scroll down to **"App-Level Tokens"**
3. Click **"Generate Token and Scopes"**
4. Name it: "Socket Mode Token"
5. Add scopes:
   - `connections:write`
   - `authorizations:read`
6. Click **"Generate"**
7. Copy the token (starts with `xapp-`)

#### Signing Secret (SLACK_SIGNING_SECRET)
1. In **"Basic Information"**
2. Under **"App Credentials"**
3. Copy the **"Signing Secret"**

### 1.5 Set Up Interactive Components
1. Go to **"Interactivity & Shortcuts"**
2. Toggle **"Interactivity"** to **ON**
3. For Socket Mode, you don't need a Request URL!
4. **Required for**: "Save to Tracker" buttons, "Approve/Reject" domain review actions
5. Click **"Save Changes"**

## Step 2: Configure Your Local Environment

### 2.1 Copy the Template
```bash
cd /Users/apoorvachilukuri/Projects/job-app/agentic-jobs-platform
cp env_template.sh env_local.sh
```

### 2.2 Edit env_local.sh
Open `env_local.sh` in your favorite editor and replace the placeholder values:

```bash
# Replace these with your actual tokens
export SLACK_BOT_TOKEN="xoxb-your-actual-token-from-step-1.4"
export SLACK_APP_LEVEL_TOKEN="xapp-your-actual-token-from-step-1.4"
export SLACK_SIGNING_SECRET="your-actual-secret-from-step-1.4"
```

### 2.3 Load the Environment Variables
```bash
source env_local.sh
```

## Step 3: Test Your Configuration

### 3.1 Test Environment Variables
```bash
python3 test_slack_config.py
```

Expected output:
```
✅ SLACK_BOT_TOKEN is set: xoxb-12345...67890
✅ SLACK_APP_LEVEL_TOKEN is set: xapp-12345...67890
✅ SLACK_SIGNING_SECRET is set: abc12...xyz89
✅ All environment variables are properly configured!
```

### 3.2 Test Slack Connection
```bash
python3 test_slack_connection.py
```

Expected output:
```
✅ Bot Token is valid!
   Team: Your Workspace
   User: your-bot
   Bot ID: B01234567
   
✅ Socket Mode connection successful!
   Your app can now receive Slack events in real-time!

🎉 All tests passed!
```

## Step 4: Start the Server

### 4.1 Using the Start Script
```bash
./start_server.sh
```

### 4.2 Or Manually
```bash
source env_local.sh
uvicorn agentic_jobs.main:app --reload --host 0.0.0.0 --port 8000
```

### 4.3 Verify the Server is Running
In another terminal:
```bash
curl http://localhost:8000/healthz
```

Expected output:
```json
{"status":"ok"}
```

### 4.4 Check the Logs
You should see:
```
INFO: Started server process
INFO: Waiting for application startup.
INFO: Slack socket mode client connected.
INFO: Application startup complete.
INFO: Uvicorn running on http://0.0.0.0:8000
```

## Step 5: Test the Complete Workflow

### 5.1 Test Job Discovery
```bash
# Trigger discovery to populate jobs
curl -X POST http://localhost:8000/api/v1/discover/run
```

### 5.2 Test Slack Integration
1. **Check for job digests** in your configured `#jobs-feed` channel
2. **Click "Save to Tracker"** on any job posting
3. **The button should work** without showing an error symbol!
4. **Check the thread** - you should see a reply with job details, score, and application ID
5. **Test domain review** - unknown domains should trigger "Needs-Review" cards

### 5.3 Verify Application Tracking
- Applications should be created with human-readable IDs (APP-YYYY-NNN)
- Each application gets its own Slack thread
- Job scoring and rationale should be displayed

## Troubleshooting

### "Socket Mode connection timed out"
- Check your internet connection
- Verify your App-Level Token is correct
- Make sure Socket Mode is enabled in your Slack app settings

### "Bot Token validation failed"
- Verify your Bot Token is correct
- Make sure you've installed the app to your workspace
- Check that the required scopes are added

### "Save to Tracker button shows error"
- Check the server logs for error messages
- Verify the database is running: `ps aux | grep postgres`
- Make sure all environment variables are set: `python3 test_slack_config.py`

### Server won't start
- Kill any existing processes: `pkill -f uvicorn`
- Check the database connection: `psql -d agentic_jobs -c "SELECT 1;"`
- Verify Python dependencies: `pip3 list | grep slack-sdk`

## Quick Reference

### Start the Server
```bash
cd /Users/apoorvachilukuri/Projects/job-app/agentic-jobs-platform
source env_local.sh
./start_server.sh
```

### Stop the Server
Press `Ctrl+C` or:
```bash
pkill -f uvicorn
```

### Test Configuration
```bash
python3 test_slack_config.py
python3 test_slack_connection.py
```

### View Logs
The logs will appear in the terminal where you started the server.

## System Features 🎉

The Agentic Jobs Platform provides:

### ✅ **Automated Job Discovery**
- **Greenhouse integration** with sitemap-based frontier seeding
- **GitHub data sources** (SimplifyJobs, New-Grad-2026) with fallback URLs
- **Rate limiting and politeness** for respectful crawling
- **Deduplication** with 30-day windows for canonical IDs and content hashes

### ✅ **Slack Integration**
- **Interactive components** with "Save to Tracker" and "Open JD" buttons
- **Automated digests** with job scoring and rationale
- **Domain review workflow** for unknown/untrusted sources
- **Application tracking** with one thread per application
- **Socket Mode integration** for real-time event handling

### ✅ **Trust & Security**
- **Domain evaluation** with deterministic scoring
- **Whitelist management** for approved domains
- **Human-in-the-loop** approval for unknown sources
- **No auto-submit** - all applications require human approval

### ✅ **No ngrok Required!**
Thanks to **Socket Mode**, your app connects directly to Slack without needing:
- ❌ ngrok
- ❌ Public URLs  
- ❌ Port forwarding
- ❌ Domain names

Everything runs locally and securely on your machine!

