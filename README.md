# Agentic Jobs Platform — MVPart 2

This repo hosts the FastAPI + SQLAlchemy service that powers seedless job discovery, normalization, dedupe, and trust evaluation for the Agentic Jobs Platform MVP. MVPart 2 adds a production-ready discovery pipeline with multiple source adapters (Greenhouse + open-source GitHub feeds) that ingest job listings directly into Postgres.

---

## Key Features

- **Pluggable discovery adapters** via a shared `SourceAdapter` protocol (`services/discovery/base.py`).
- **Greenhouse frontier crawler** that seeds organization slugs from the public sitemap, respects robots.txt, rate-limits requests, and normalizes canonical IDs (`GH:<job_id>`).
- **GitHub JSON feeds** (SimplifyJobs, New-Grad-2026) with fallback URLs, support for multiple schema variants (`listings`, `positions`, `companies`), and recency filtering (`GITHUB_MAX_AGE_DAYS`).
- **Normalization & dedupe** through `services/sources/normalize.py` and `services/discovery/orchestrator.py`:
  - HTML→text conversion, requirements extraction, SHA-1 hash dedup (30-day window).
  - Canonical ID dedupe (30-day window). GitHub adapters emit `SIMPLIFY:<sha1>` / `NEWGRAD2026:<sha1>` style identifiers.
- **Trust gate v1** stores `TrustEvent` rows with deterministic scores per domain (`services/trust/evaluator.py`).
- **REST endpoint** `POST /api/v1/discover/run` orchestrates all adapters, returns summarized counts, and persists new `Job`, `JobSource`, and `TrustEvent` rows.
- **Extensive test coverage** with local fixtures for sitemap parsing, JSON feeds, dedupe, and multi-adapter orchestration.

---

## Project Structure (selected files)

```
agentic_jobs/
├── api/v1/discover.py            # /discover/run endpoint wiring
├── config.py                     # pydantic settings (env-driven)
├── services/
│   ├── discovery/
│   │   ├── base.py               # SourceAdapter protocol + dataclasses
│   │   ├── green_house_adapter.py
│   │   ├── github_adapter.py     # SimplifyJobs/NewGrad adapters
│   │   ├── orchestrator.py       # Multi-adapter crawl pipeline
│   │   └── rate_limiter.py       # Async rate limiter utility
│   ├── sources/normalize.py      # HTML normalization + hashing
│   └── trust/evaluator.py        # Trust scoring stub (auto-safe)
├── db/models.py                  # SQLAlchemy models (jobs, job_sources, frontier...)
└── ...
tests/
├── discovery/                    # Discovery + adapter-specific tests
├── fixtures/                     # Static sitemap/JSON/job-detail fixtures
└── sources/test_normalize.py     # Normalization helpers
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create and configure `.env`

Copy `.env` (or `.env.example` if you have one) and adjust the values described below. Minimum settings:

```dotenv
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/agentic
ENVIRONMENT=dev
DEBUG=true
DISCOVERY_BASE_URL=https://boards.greenhouse.io
DISCOVERY_SITEMAP_URL=https://boards.greenhouse.io/sitemap_index.xml
MAX_ORGS_PER_RUN=100
REQUESTS_PER_MINUTE=60
REQUEST_TIMEOUT_SECONDS=5
ALLOWED_DOMAINS=boards.greenhouse.io,raw.githubusercontent.com,github.com
ENABLE_GREENHOUSE=true
SIMPLIFY_POSITIONS_URLS=https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json,https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json
NEW_GRAD_2026_URLS=https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/.github/scripts/listings.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/src/data/positions.json,https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/data/positions.json
GITHUB_MAX_AGE_DAYS=3
```

> ℹ️ **Multiple URLs** are comma-separated fallbacks; the adapter uses the first reachable endpoint. Keep the `.github/scripts/listings.json` variant first—both repos currently publish their authoritative listings there.

### 3. Start the API

```bash
uvicorn agentic_jobs.main:app --reload
```

### 4. Trigger discovery manually

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/discover/run \
  -H 'content-type: application/json' \
  -d '{}'
```

On success, you’ll see a JSON summary:

```json
{
  "orgs_crawled": 3,
  "jobs_seen": 42,
  "jobs_inserted": 18,
  "domains_scored": 6
}
```

Job records, sources, and trust events are persisted in Postgres. If an adapter cannot fetch its data (e.g., GitHub 404), the orchestrator logs a warning and continues with the remaining sources.

---

## Configuration Overview

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection string |
| `ENVIRONMENT` / `DEBUG` | General FastAPI runtime toggles |
| `DISCOVERY_BASE_URL` | Base domain for robots + sitemap (Greenhouse) |
| `DISCOVERY_SITEMAP_URL` | Greenhouse sitemap (ignored if `ENABLE_GREENHOUSE=false`) |
| `DISCOVERY_INTERVAL_HOURS` | Intended scheduler cadence (informational) |
| `MAX_ORGS_PER_RUN` | Frontier batch size per run |
| `REQUESTS_PER_MINUTE`, `REQUEST_TIMEOUT_SECONDS` | Adapter politeness defaults |
| `ALLOWED_DOMAINS` | Allowlist for the Greenhouse adapter’s robots enforcement |
| `ENABLE_GREENHOUSE` | `true` to crawl Greenhouse; `false` to skip entirely |
| `SIMPLIFY_POSITIONS_URLS` | Comma-separated fallback URLs for Simplify GitHub JSON feeds |
| `NEW_GRAD_2026_URLS` | Comma-separated fallback URLs for vanshb03 GitHub JSON feeds |
| `GITHUB_MAX_AGE_DAYS` | Drop GitHub listings older than this many days during normalization |

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

## Summary of MVPart 2 Changes

- Added discovery infrastructure (`services/discovery/…` packages) with Greenhouse and GitHub adapters.
- Added `frontier_orgs` table/migration, seed logic, and orchestrator to handle multi-source crawls.
- Implemented normalization, dedupe, and trust gate for inserted jobs.
- Extended `/api/v1/discover/run` to execute all adapters asynchronously and return ingestion metrics.
- Created fixtures and tests covering sitemap parsing, JSON feeds, dedupe, and adapter failovers.
- Documented configuration, runtime instructions, and tests (this README).

Run `curl -X POST http://127.0.0.1:8000/api/v1/discover/run ...` to ingest the latest GitHub + Greenhouse listings into your local DB. Adjust `.env` to target additional sources as new adapters are implemented.
