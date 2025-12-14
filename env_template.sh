#!/bin/bash

# Slack Environment Variables Template
# Copy this file to 'env_local.sh' and replace the placeholder values with your actual tokens
# Then run: source env_local.sh

# =================================
# REQUIRED: Get these from https://api.slack.com/apps
# =================================

# Bot User OAuth Token (starts with xoxb-)
# Found in: OAuth & Permissions → Bot User OAuth Token
export SLACK_BOT_TOKEN="xoxb-your-actual-bot-token-here"

# App-Level Token (starts with xapp-)
# Found in: Basic Information → App-Level Tokens
export SLACK_APP_LEVEL_TOKEN="xapp-your-actual-app-level-token-here"

# Signing Secret
# Found in: Basic Information → Signing Secret
export SLACK_SIGNING_SECRET="your-actual-signing-secret-here"

# =================================
# OPTIONAL: Customize these channels
# =================================

export SLACK_JOBS_FEED_CHANNEL="#jobs-feed"
export SLACK_JOBS_DRAFTS_CHANNEL="#job-drafts"
export LLM_BACKEND="mock"
export LLM_MODEL_NAME="llama3.1:8b-instruct"
export LLM_TIMEOUT_SECONDS="60"
export LLM_ENDPOINT_URL=""
export LLM_API_KEY=""

# =================================
# DATABASE CONFIGURATION
# =================================

export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_jobs"

# =================================
# APPLICATION CONFIGURATION
# =================================

export ENVIRONMENT="development"
export DEBUG="true"

echo "✅ Environment variables loaded!"
echo "Run: python3 test_slack_config.py to verify your configuration"
