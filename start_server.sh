#!/bin/bash

# Start script for Agentic Jobs Platform
# This script sets up environment variables and starts the server

# Database Configuration
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_jobs"

# Slack Configuration
# IMPORTANT: Replace these placeholder values with your actual Slack tokens
# Get your tokens from: https://api.slack.com/apps
export SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-xoxb-replace-with-your-bot-token}"
export SLACK_APP_LEVEL_TOKEN="${SLACK_APP_LEVEL_TOKEN:-xapp-replace-with-your-app-level-token}"
export SLACK_SIGNING_SECRET="${SLACK_SIGNING_SECRET:-replace-with-your-signing-secret}"

# Slack Channels
export SLACK_JOBS_FEED_CHANNEL="${SLACK_JOBS_FEED_CHANNEL:-#jobs-feed}"
export SLACK_JOBS_DRAFTS_CHANNEL="${SLACK_JOBS_DRAFTS_CHANNEL:-#job-drafts}"

# Application Configuration
export ENVIRONMENT="development"
export DEBUG="true"

# Print configuration (hiding sensitive parts)
echo "==================================="
echo "Starting Agentic Jobs Platform"
echo "==================================="
echo "Database: ${DATABASE_URL}"
echo "Slack Bot Token: ${SLACK_BOT_TOKEN:0:10}...${SLACK_BOT_TOKEN: -5}"
echo "Slack App Token: ${SLACK_APP_LEVEL_TOKEN:0:10}...${SLACK_APP_LEVEL_TOKEN: -5}"
echo "Jobs Feed Channel: ${SLACK_JOBS_FEED_CHANNEL}"
echo "Jobs Drafts Channel: ${SLACK_JOBS_DRAFTS_CHANNEL}"
echo "==================================="
echo ""

# Start the server
uvicorn agentic_jobs.main:app --reload --host 0.0.0.0 --port 8000

