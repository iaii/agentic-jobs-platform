# Agentic Jobs Platform — Production Ready

This repo hosts the FastAPI + SQLAlchemy service that powers the complete Agentic Jobs Platform. The system provides seedless job discovery, normalization, deduplication, trust evaluation, Slack integration, and application tracking for automated job application management.

---

## Key Features

### 🔍 **Job Discovery & Processing**
- **Pluggable discovery adapters** via a shared `SourceAdapter` protocol (`services/discovery/base.py`)
- **Greenhouse frontier crawler** that seeds organization slugs from the public sitemap, respects robots.txt, rate-limits requests, and normalizes canonical IDs (`GH:<job_id>`)
- **GitHub JSON feeds** (SimplifyJobs, New-Grad-2026) with fallback URLs, support for multiple schema variants (`listings`, `positions`, `companies`), and recency filtering (`GITHUB_MAX_AGE_DAYS`)
- **Normalization & dedupe** through `services/sources/normalize.py` and `services/discovery/orchestrator.py`:
  - HTML→text conversion, requirements extraction, SHA-1 hash dedup (30-day window)
  - Canonical ID dedupe (30-day window). GitHub adapters emit `SIMPLIFY:<sha1>` / `NEWGRAD2026:<sha1>` style identifiers

### 🛡️ **Trust & Security**
- **Trust gate v1** stores `TrustEvent` rows with deterministic scores per domain (`services/trust/evaluator.py`)
- **Domain review system** with Slack-based approval workflow for unknown domains
- **Whitelist management** for approved domains and companies

### 📱 **Slack Integration**
- **Interactive Slack components** with "Save to Tracker" and "Open JD" buttons
- **Automated digest posting** with job scoring and rationale
- **Needs-Review cards** for unknown domains requiring human approval
- **Application thread management** with one thread per application
- **Socket Mode integration** for real-time Slack event handling

### 📊 **Application Tracking**
- **Complete application lifecycle** from discovery to submission tracking
- **Human-readable application IDs** (APP-YYYY-NNN format)
- **Job scoring system** with deterministic rules for title, location, and skills matching
- **Artifact management** for cover letters, JD snapshots, and confirmations
- **Profile management** system for user identity, skills, and project information

### 🔄 **Automation & Scheduling**
- **3-hour discovery cycles** with configurable time windows (06:00-23:00 PT)
- **Automatic job ingestion** from multiple sources
- **Slack digest posting** with ranked job listings
- **Domain review automation** for new/untrusted sources

---

## Project Structure

```
agentic_jobs/
├── api/v1/                       # REST API endpoints
│   ├── discover.py               # Job discovery orchestration
│   ├── applications.py           # Application management
│   ├── slack_actions.py          # Slack interactive components
│   ├── trust.py                  # Trust evaluation endpoints
│   ├── drafts.py                 # Cover letter generation (stub)
│   └── feedback.py               # Feedback system (stub)
├── config.py                     # Pydantic settings (env-driven)
├── core/enums.py                 # Application enums and constants
├── db/
│   ├── models.py                 # SQLAlchemy models (complete schema)
│   └── session.py                # Database session management
├── services/
│   ├── discovery/                # Job discovery system
│   │   ├── base.py               # SourceAdapter protocol
│   │   ├── greenhouse_adapter.py # Greenhouse crawler
│   │   ├── github_adapter.py     # GitHub JSON feed adapters
│   │   ├── orchestrator.py       # Multi-adapter orchestration
│   │   └── rate_limiter.py       # Async rate limiting
│   ├── slack/                    # Slack integration
│   │   ├── client.py             # Slack API client
│   │   ├── actions.py            # Interactive component handlers
│   │   ├── digest.py             # Digest message formatting
│   │   ├── socket.py             # Socket Mode integration
│   │   └── workflows.py          # Workflow automation
│   ├── ranking/scorer.py         # Job scoring system
│   ├── sources/normalize.py      # HTML normalization + hashing
│   ├── trust/evaluator.py        # Trust scoring system
│   └── scheduler/cron.py         # Scheduled task management
└── schemas/                      # Pydantic schemas (future)
tests/
├── discovery/                    # Discovery system tests
├── slack/                       # Slack integration tests
├── sources/                     # Normalization tests
└── fixtures/                    # Test data and mocks
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create and configure environment

Copy the environment template and configure your settings:

```bash
cp env_template.sh env_local.sh
```

Edit `env_local.sh` with your configuration:

```bash
# Database Configuration
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_jobs"
export ENVIRONMENT="development"
export DEBUG="true"

# Discovery Configuration
export DISCOVERY_BASE_URL="https://boards.greenhouse.io"
export DISCOVERY_SITEMAP_URL="https://boards.greenhouse.io/sitemap_index.xml"
export MAX_ORGS_PER_RUN="100"
export REQUESTS_PER_MINUTE="60"
export REQUEST_TIMEOUT_SECONDS="5"
export ALLOWED_DOMAINS="boards.greenhouse.io,raw.githubusercontent.com,github.com"
export ENABLE_GREENHOUSE="true"
export GITHUB_MAX_AGE_DAYS="3"

# GitHub Data Sources (comma-separated fallback URLs)
export SIMPLIFY_POSITIONS_URLS="https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json"
export NEW_GRAD_2026_URLS="https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/.github/scripts/listings.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/src/data/positions.json"

# Slack Integration (required for full functionality)
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_APP_LEVEL_TOKEN="xapp-your-app-token"
export SLACK_SIGNING_SECRET="your-signing-secret"
export SLACK_JOBS_FEED_CHANNEL="#jobs-feed"
export SLACK_JOBS_DRAFTS_CHANNEL="#jobs-drafts"

# Scheduler Configuration
export SCHEDULER_WINDOW_START_HOUR_PT="7"
export SCHEDULER_WINDOW_END_HOUR_PT="23"
export DIGEST_BATCH_SIZE="20"
```

> ℹ️ **Multiple URLs** are comma-separated fallbacks; the adapter uses the first reachable endpoint. Keep the `.github/scripts/listings.json` variant first—both repos currently publish their authoritative listings there.

### 3. Load environment and start the API

```bash
# Load your environment variables
source env_local.sh

# Start the server
./start_server.sh
# OR manually:
# uvicorn agentic_jobs.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Test the system

#### Health Check
```bash
curl http://localhost:8000/healthz
```

#### Trigger Discovery
```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/discover/run \
  -H 'content-type: application/json' \
  -d '{}'
```

On success, you'll see a JSON summary:

```json
{
  "orgs_crawled": 3,
  "jobs_seen": 42,
  "jobs_inserted": 18,
  "domains_scored": 6
}
```

#### Test Slack Integration
```bash
# Test configuration
python3 test_slack_config.py

# Test connection
python3 test_slack_connection.py
```

Job records, sources, and trust events are persisted in Postgres. If an adapter cannot fetch its data (e.g., GitHub 404), the orchestrator logs a warning and continues with the remaining sources.

---

## Configuration Overview

### Core Settings
| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy connection string | `postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_jobs` |
| `ENVIRONMENT` | Runtime environment | `development` |
| `DEBUG` | Enable debug mode | `false` |

### Discovery Settings
| Variable | Description | Default |
| --- | --- | --- |
| `DISCOVERY_BASE_URL` | Base domain for robots + sitemap (Greenhouse) | `https://boards.greenhouse.io` |
| `DISCOVERY_SITEMAP_URL` | Greenhouse sitemap (ignored if `ENABLE_GREENHOUSE=false`) | `https://boards.greenhouse.io/sitemap_index.xml` |
| `MAX_ORGS_PER_RUN` | Frontier batch size per run | `100` |
| `REQUESTS_PER_MINUTE` | Rate limiting for HTTP requests | `60` |
| `REQUEST_TIMEOUT_SECONDS` | HTTP request timeout | `5` |
| `ALLOWED_DOMAINS` | Allowlist for the Greenhouse adapter's robots enforcement | `boards.greenhouse.io,raw.githubusercontent.com,github.com` |
| `ENABLE_GREENHOUSE` | `true` to crawl Greenhouse; `false` to skip entirely | `true` |
| `GITHUB_MAX_AGE_DAYS` | Drop GitHub listings older than this many days | `3` |

### Data Sources
| Variable | Description | Default |
| --- | --- | --- |
| `SIMPLIFY_POSITIONS_URLS` | Comma-separated fallback URLs for Simplify GitHub JSON feeds | Multiple SimplifyJobs URLs |
| `NEW_GRAD_2026_URLS` | Comma-separated fallback URLs for vanshb03 GitHub JSON feeds | Multiple New-Grad-2026 URLs |

### Slack Integration
| Variable | Description | Required |
| --- | --- | --- |
| `SLACK_BOT_TOKEN` | Bot User OAuth Token (starts with `xoxb-`) | ✅ |
| `SLACK_APP_LEVEL_TOKEN` | App-Level Token (starts with `xapp-`) | ✅ |
| `SLACK_SIGNING_SECRET` | Signing Secret for request verification | ✅ |
| `SLACK_JOBS_FEED_CHANNEL` | Channel for job digests | ✅ |
| `SLACK_JOBS_DRAFTS_CHANNEL` | Channel for cover letter drafts | ✅ |

### Scheduler Settings
| Variable | Description | Default |
| --- | --- | --- |
| `SCHEDULER_WINDOW_START_HOUR_PT` | Start hour for discovery (PT timezone) | `7` |
| `SCHEDULER_WINDOW_END_HOUR_PT` | End hour for discovery (PT timezone) | `23` |
| `DIGEST_BATCH_SIZE` | Number of jobs per digest | `20` |

For local development without Greenhouse access, set `ENABLE_GREENHOUSE=false` and rely solely on the GitHub adapters.

---

## Discovery Architecture

### SourceAdapter protocol

Every adapter implements `services/discovery/base.SourceAdapter`:

- `discover()` → seeds organization slugs (Greenhouse) or pseudo-slugs (GitHub).
- `list_jobs(org_slug)` → returns lightweight `JobRef` objects (title, location, canonical ID stub, detail URL, metadata).
- `fetch_job_detail(job_ref)` → retrieves full HTML (Greenhouse) or synthesizes HTML from JSON (GitHub).
- `canonical_id(job_ref)` → deterministic canonical identifier used for dedupe.
- `job_source_type` / `submission_mode` → persisted on `Job`/`JobSource`.
- `uses_frontier` → toggle to skip the DB frontier (GitHub adapters set this `False`).

The orchestrator loops through each adapter, enforces dedupe windows, creates `Job`, `JobSource`, `TrustEvent`, and updates the summary counts.

### Dedup logic

1. If canonical ID exists in the last 30 days → skip.
2. Else if SHA-1 hash (title + company + JD) exists in the last 30 days → skip.
3. Otherwise, insert new `Job` + `JobSource`, create `TrustEvent`.

### GitHub adapters

- Request each URL listed in `SIMPLIFY_POSITIONS_URLS` / `NEW_GRAD_2026_URLS` until one returns HTTP 200.
- Support the following shapes:
  - `{ "listings": [ ... ] }`
  - `{ "positions": [ ... ] }`
  - `{ "companies": [{ ... "roles": [ ... ] }] }`
  - Raw `list` of dicts
- Extract relevant fields (company, title, url, location, requirements) and `date_posted`/`posted`/`timestamp` information (ISO 8601, numeric epoch, or simple date formats).
- Filter by `GITHUB_MAX_AGE_DAYS` (skips stale listings while still storing detection metadata).
- Generate synthetic JD HTML with sections for company, location, requirements, etc.

---

## Database Outputs

- **`jobs`**: normalized job data (title, company, JD text, requirements[], canonical ID, hash, domain root).
- **`job_sources`**: raw payload + metadata (source type, domain, canonical hash).
- **`trust_events`**: stubbed trust result per domain (auto-safe for downstream scoring).
- **`frontier_orgs`**: persisted Greenhouse slug frontier (not used by GitHub adapters).

All tables are defined in `agentic_jobs/db/models.py`. Alembic migration `alembic/versions/4dd2f4e2a91b_add_frontier_orgs.py` adds the frontier table introduced in MVPart 2.

---

## Testing

Run the full suite:

```bash
pytest -q
```

Notable coverage:

| Test module | Purpose |
| --- | --- |
| `tests/discovery/test_frontier_greenhouse.py` | Frontier seeding, multi-adapter orchestration, dedupe, trust events |
| `tests/discovery/test_github_adapter.py` | Fallback URLs, listings schema support, old-job filtering |
| `tests/sources/test_normalize.py` | HTML normalization and hashing |

Fixtures in `tests/fixtures` include static examples for sitemap, JSON feeds, HTML detail pages, etc.

---

## Operational Notes

- **Restart required** after changing `.env`; `pydantic-settings` caches values in `agentic_jobs.config.settings`.
- **Logging**: Uvicorn prints adapter warnings when a source skips due to 4xx/5xx errors.
- **Network access**: Crawlers rely on outbound HTTPS access to `boards.greenhouse.io` and `raw.githubusercontent.com`. If blocked, adapters raise `DiscoveryError` and the orchestrator skips them.
- **Limiting scope**: Adjust `MAX_ORGS_PER_RUN` (Greenhouse) and `GITHUB_MAX_AGE_DAYS` (GitHub) for local experimentation.

---

## Current Implementation Status

### ✅ **Fully Implemented**
- **Complete database schema** with all models (Job, JobSource, Application, Artifact, TrustEvent, etc.)
- **Discovery system** with Greenhouse and GitHub adapters
- **Job normalization and deduplication** with 30-day windows
- **Trust evaluation system** with domain scoring
- **Slack integration** with interactive components and Socket Mode
- **Job scoring system** with deterministic rules
- **Application tracking** with human-readable IDs
- **Scheduler system** with configurable time windows
- **API endpoints** for discovery, applications, trust, and Slack actions

### 🔄 **Partially Implemented**
- **Cover letter generation** (stubs exist, LLM integration pending)
- **Feedback system** (stubs exist, full implementation pending)
- **Profile management** (database models exist, API endpoints pending)

### 🚧 **Future Enhancements**
- **Advanced LLM integration** for cover letter generation
- **Enhanced ranking system** with configurable weights
- **Profile management API** for user data
- **Additional data sources** (Lever, Workday, etc.)

## Quick Start

1. **Set up environment**: Copy `env_template.sh` to `env_local.sh` and configure
2. **Start database**: Ensure PostgreSQL is running
3. **Load environment**: `source env_local.sh`
4. **Start server**: `./start_server.sh`
5. **Test discovery**: `curl -X POST http://127.0.0.1:8000/api/v1/discover/run`
6. **Configure Slack**: Follow `SLACK_SETUP.md` for full integration

The system is production-ready for job discovery, scoring, and Slack-based application tracking. Cover letter generation and advanced features are in development.
