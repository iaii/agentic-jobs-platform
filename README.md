# Agentic Jobs Platform

A fully autonomous job-application pipeline — built on FastAPI, PostgreSQL, and Slack — that discovers jobs from multiple sources, scores and delivers them to you in real time, lets you track applications through a Slack-native interface, and drafts personalised cover letters using a multi-agent LLM workflow, down to auto-filling the ATS form.

<img width="600" height="338" alt="output" src="https://github.com/user-attachments/assets/fb69ebe1-d8c0-4c1f-b97d-287024180a66" />

---

## Table of Contents

1. [Architecture overview](#architecture-overview)
2. [Discovery pipeline](#discovery-pipeline)
3. [Trust & domain review](#trust--domain-review)
4. [Slack integration](#slack-integration)
5. [Cover letter drafting (LLM)](#cover-letter-drafting-llm)
6. [Application tracking](#application-tracking)
7. [Autofill pipeline](#autofill-pipeline)
8. [Database schema](#database-schema)
9. [Scheduler & cron](#scheduler--cron)
10. [Configuration reference](#configuration-reference)
11. [Setup & quick-start](#setup--quick-start)

---

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FastAPI application                           │
│                                                                      │
│  /api/v1/discover   /api/v1/slack   /api/v1/drafts   /api/v1/autofill│
└───────────┬────────────────┬────────────────┬──────────────┬─────────┘
            │                │                │              │
     Discovery           Slack Socket      LLM agents    Autofill
     orchestrator         Mode loop         pipeline      API + ext
            │                │
     ┌──────┴──────┐   ┌─────┴──────────┐
     │ Adapters    │   │ Digest / Tracker│
     │ Greenhouse  │   │ Actions         │
     │ GitHub x2   │   │ Workflows       │
     │ Universal   │   └─────────────────┘
     └──────┬──────┘
            │
     PostgreSQL (SQLAlchemy, scoped_session)
```

The server starts three long-running background tasks:

| Task | What it does |
|------|-------------|
| **Scheduler** | Triggers discovery cycles on a configurable PT schedule (default: every 3 h, 07:00–23:00) |
| **Socket Mode listener** | Maintains a persistent Slack WebSocket connection; dispatches button/modal events to action handlers instantly |
| **Vault refresh** | On startup, re-embeds any modified sections of a local Obsidian vault for retrieval during cover-letter generation |

---

## Discovery pipeline

### SourceAdapter protocol

Every source implements the `SourceAdapter` async protocol defined in `services/discovery/base.py`:

```python
async def discover()            → Sequence[str]    # seed org slugs
async def list_jobs(slug)       → Sequence[JobRef] # lightweight job refs
async def fetch_job_detail(ref) → JobDetail        # full HTML / metadata
def    canonical_id(ref)        → str              # stable dedup key
```

`JobRef` carries: title, company, location, detail URL, source label, and arbitrary metadata that flows through to the `JobSource` row in the database.

The orchestrator runs every adapter in sequence, enforces two dedup windows, and writes `Job`, `JobSource`, and `TrustEvent` rows for every new posting.

---

### Greenhouse adapter

```
Sitemap → FrontierOrg list → JSON board endpoint → fallback HTML parse
```

1. **Frontier seeding**: fetches `sitemap_index.xml`, extracts org slugs, upserts `FrontierOrg` rows (unique on `source` + `org_slug`).
2. **Batch selection**: picks up to `MAX_ORGS_PER_RUN` orgs ordered by `(priority DESC, last_crawled_at ASC NULLS FIRST)` and not currently `muted_until`.
3. **Job listing**: `GET /org_slug/embed/job_board/json` → falls back to HTML parsing (`<div class="opening">`) if the endpoint returns 4xx/5xx.
4. **Detail fetch**: full HTML from the job board page; LD+JSON `application/ld+json` block used to extract the canonical company name.
5. **Rate limiting**: `AsyncRateLimiter` leaky-bucket at `REQUESTS_PER_MINUTE`.
6. **Robots.txt**: parsed and respected; any disallowed path is silently skipped.
7. **Canonical ID**: `GH:<job_id>`.

---

### GitHub JSON adapters

Two adapters — **Simplify** (`SIMPLIFY:<sha1>`) and **New-Grad-2026** (`NEWGRAD2026:<sha1>`) — share the same implementation, differing only in config:

- **Multi-URL fallback**: iterates `SIMPLIFY_POSITIONS_URLS` / `NEW_GRAD_2026_URLS` until one returns HTTP 200.
- **Schema flexibility**: supports four JSON shapes emitted by these repos over time:
  ```
  { "listings": [...] }
  { "positions": [...] }
  { "companies": [{ "roles": [...] }] }
  raw list
  ```
- **Recency filter**: drops postings older than `GITHUB_MAX_AGE_DAYS` (default 3); understands ISO-8601 strings, Unix epoch integers, and `YYYY-MM-DD` date strings.
- **Company inference**: extracts company from the job URL path when the feed omits it (Greenhouse/Lever/Workday slug patterns).
- **No frontier**: these adapters set `uses_frontier = False`; the orchestrator skips the DB frontier for them.

---

### Universal ATS adapter

YAML-driven; targets arbitrary career portals without code changes.  Configure in `config/universal_sites.yaml`:

```yaml
sites:
  - site_slug: apple
    display_name: Apple Careers
    feeds:
      - feed_slug: corporate
        parser: workday
        options:
          host: jobs.apple.com
          tenant: apple
          site: en-us

  - site_slug: meta
    display_name: Meta Careers
    feeds:
      - site_url: https://www.metacareers.com/jobsearch/   # auto-detect
```

**Auto-detection**: when only `site_url` is given, the adapter fetches the page once, then `ParserDetector` pattern-matches the HTML/JS to identify the underlying ATS (Lever, Workday, Ashby, SmartRecruiters, iCIMS, Oracle Cloud) and stores the resolved config for the run.

**Parser interface**: each parser implements `list_jobs() → [ParsedJob]` with fields: `job_id`, `title`, `location`, `detail_url`, `posted_at`.

`crawl_interval_minutes` on a feed overrides the global scheduler cadence per site.  `UNIVERSAL_MAX_AGE_DAYS` (default 7) drops stale postings when the feed exposes `posted_at`.

---

### Orchestrator

`services/discovery/orchestrator.py` runs the full loop:

```
for each adapter:
  1. discover()  →  seed / update FrontierOrg (if uses_frontier)
  2. select batch  from frontier (MAX_ORGS_PER_RUN)
  3. for each org:
       list_jobs()  →  [JobRef]
       for each ref:
         _is_relevant_role()    ← title keyword filter
         _job_seen_recently()   ← 30-day canonical-ID dedup
         _hash_seen_recently()  ← 30-day SHA-1 content dedup
         fetch_job_detail()     → normalize → extract requirements
         trust evaluation       → TrustEvent row
         persist Job + JobSource
  4. update last_crawled_at; set muted_until if feed specifies interval
  5. return DiscoverySummary
```

**Deduplication detail**:
- Canonical-ID window: `canonical_id` seen in a `JobSource` row with `scraped_at >= now() - 30 days`.
- Content-hash window: SHA-1 of `title.lower() + company.lower() + jd_text + location + url + job_id` seen in the same window.
- Adapter failures (4xx, timeouts) are logged as warnings; the orchestrator continues with remaining sources.

---

### Normalization

`services/sources/normalize.py`:

| Step | Detail |
|------|--------|
| HTML → text | Custom `_HTMLStripper` inserts newlines at `<p>`, `<div>`, `<h*>`, `<li>` boundaries |
| Requirements extraction | `_RequirementExtractor` pulls `<li>` items; falls back to paragraphs containing "require"/"must"/"responsible" |
| Hash | SHA-1 of normalised components |

---

### Job filtering

`config/job_filters.yaml` — evaluated before any network call:

```yaml
adapters:
  greenhouse: true
  simplify: true
  newgrad2026: true
  universal: true

filters:
  include_keywords: [software engineer, swe, new grad, ...]
  exclude_keywords:  [manager, director, senior, ...]
```

A job must contain **at least one** include keyword and **zero** exclude keywords in its title (case-insensitive substring match).  Point `JOB_FILTER_CONFIG_PATH` at a different YAML to switch profiles without touching code.

---

## Trust & domain review

Every newly discovered domain goes through a trust check before its jobs appear in a digest.

### TrustEvent scoring

`services/trust/evaluator.py` records a `TrustEvent` row per domain per run:

```
score   : 0–100
signals : [{signal: "host", value: "boards.greenhouse.io"}, ...]
verdict : AUTO_SAFE | NEEDS_HUMAN_APPROVAL | REJECT
```

Known ATS hosts (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS, Oracle Cloud) are auto-whitelisted in `services/trust/whitelist.py` and always resolve to `AUTO_SAFE`.

### Domain review workflow

```
Discovery: unknown domain found
    │
    ▼
DomainReview(status=PENDING) created
    │
    ▼
Scheduler posts "Needs Review" card in SLACK_JOBS_FEED_CHANNEL
  [Approve]  [Reject]  [Mute]
    │
    ▼  (user clicks Approve)
Whitelist row inserted, DomainReview.status = APPROVED
Future jobs from this domain flow into digests normally
```

`MUTED` domains are suppressed for a configurable `muted_until` duration before re-surfacing.

---

## Slack integration

### Channel layout

| Channel env var | Purpose |
|-----------------|---------|
| `SLACK_JOBS_FEED_CHANNEL` | Digest posts + domain review cards |
| `SLACK_JOBS_DRAFTS_CHANNEL` | Per-application threads; cover letter collaboration |
| `SLACK_JOBS_TRACKER_CHANNEL` | Pinned master tracker view |
| `SLACK_JOBS_ARCHIVE_CHANNEL` | Final outcomes (accepted / rejected) |

### Socket Mode

`services/slack/socket.py` maintains a persistent WebSocket via `slack_sdk.socket_mode.aiohttp.SocketModeClient`.

- **Immediate ACK**: every incoming envelope is acknowledged before any processing — prevents the 3-second Slack timeout.
- **Background dispatch**: each event handler is spawned as an `asyncio.Task` so the ACK loop is never blocked.
- **Routing**: `events_api` payloads → `_process_event()`; `block_actions` / `view_submission` payloads → `_process_interaction()`.
- **Session management**: one `aiohttp.ClientSession` per handler call, closed in `finally`.

### Digest workflow

```
Scheduler calls collect_digest_rows()
  └─ Query jobs since last_posted_at (DigestLog dedup)
  └─ score_job(): 0.3 baseline
                  + 0.2 for title match
                  + 0.15 for new-grad tag
                  + 0.2 for location preference
                  + 0.15 for remote flag
                  (capped at 1.0)
  └─ Sort DESC, take top DIGEST_BATCH_SIZE (default 20)
  └─ Render blocks: title · company · location · score · source label
                    [Open JD]  [Save to Tracker]
  └─ Post to SLACK_JOBS_FEED_CHANNEL
  └─ Insert DigestLog rows (unique on job_id + digest_date)

Collect needs-review candidates
  └─ For each new domain: check auto-whitelist → Whitelist → DomainReview
  └─ Create DomainReview(PENDING) for genuinely unknown domains
  └─ Post review cards immediately after digest
```

### Save to Tracker flow

1. User clicks **Save to Tracker** on any digest card.
2. `handle_save_to_tracker()`:
   - Resolves the `Job` by UUID or canonical ID embedded in the button payload.
   - Checks `canonical_job_id` uniqueness (one application per job).
   - Generates `APP-{YYYY}-{NNN}` human ID.
   - Creates `Application` row (`status=QUEUED`, `stage=INTERESTED`, score recorded).
   - Writes JD snapshot to `artifacts/{human_id}/jd.md`.
   - Posts application card in `SLACK_JOBS_DRAFTS_CHANNEL` with buttons:
     - **Quick Draft** — single LLM pass
     - **Generate CL** — full multi-agent pipeline (research → write → review → revise)
     - **Finalize Draft** — lock version, enable autofill
   - Refreshes the pinned master tracker.

### Master tracker

`services/slack/tracker.py` maintains a paginated pinned message in `SLACK_JOBS_TRACKER_CHANNEL`.

- Up to 100 rows across 4 pages (25 rows each).
- Each page is stored as a `TrackerView` row holding the Slack `message_ts` so it can be updated in place.
- Header shows stage counts: Interested · CL In Progress · CL Finalized · Submitted · Interviewing.
- Clicking a row opens a **Manage** modal with: stage selector, control buttons (Generate CL / Finalize / Queue Autofill), JD snapshot, latest cover-letter preview.
- Refreshed on every `save_to_tracker`, `stage_select`, `finalize_draft`, or autofill status change.

---

## Cover letter drafting (LLM)

### Profile kit

All personalisation is driven by `agentic_jobs/profile/cover_letter_kit.yaml`.  Key sections:

```yaml
profile:
  bio: …
  background: […]
  technical_strengths: {category: [skills]}

experience:
  - key: job-key
    title: …
    bullets: […]
    themes: [visual, automation, health]   # used for theme matching

projects:
  - key: project-key
    name: …
    talking_points: […]
    themes: [application domains]

tone:
  overall:  [voice guidelines]
  dislikes: [what to avoid]
  likes:    [preferred phrasing]

structure:
  greeting: "Dear Hiring Manager,"
  opener_guidance: …
  plan:
    bullets: [pair during onboarding, …]
  stack_guidance: …
  close_guidance: …

tailoring_checklist: [things to verify per JD]
dos:   […]
donts: […]
```

### Prompt builder

`services/llm/prompt_builder.py` constructs the full prompt payload:

1. Load kit + application job + feedback history + `AgentMemory` learning notes.
2. **Theme matching**: scan JD for domain keywords (health, automation, fintech…) → pick the matching `project` entry.
3. **Role targets**: grep JD for backend/APIs/SQL/React/Python → cap at 4; fall back to `STACK_DEFAULTS`.
4. **Stack composition**: from profile skills or defaults.
5. Build `DraftContext`:
   ```
   role:         {title, company, location, targets[]}
   project_card: {name, short_name, summary, talking_points, themes}
   profile:      {identity, links, skills, stack, projects}
   note:         latest user feedback (if any)
   learning:     top 3 recent AgentMemory notes
   tone_rules:   kit.tone
   structure:    kit.structure
   ```

### LLM runner

`services/llm/runner.py` supports multiple backends, configured by `LLM_BACKEND`:

| Backend | Endpoint | Auth |
|---------|----------|------|
| `lmstudio` / `ollama` | `LLM_ENDPOINT_URL` (OpenAI-compatible `/v1/chat/completions`) | `LLM_API_KEY` / `OLLAMA_API_KEY` |
| `qwen` | DashScope API | `LLM_API_KEY` |
| `mock` | In-process stub | — |

- **Retries**: 3 attempts, exponential backoff, triggered on HTTP 429 and 500–504.
- **Timeout**: `LLM_TIMEOUT_SECONDS` (default 120).
- **User message cap**: `LLM_MAX_USER_MSG_CHARS` (default 12 000) — long JDs are truncated before the call.
- **Response parsing**: strips markdown code fences if the model wraps JSON.
- Returns `LlmResponse(version, cover_letter_md, sections_used, provenance)`.

### Multi-agent pipeline (`Generate CL`)

`services/agents/coordinator.py` orchestrates four specialised agents:

```
Phase 1 — Data gathering (parallel)
  CompanyScraper   → fetch careers page + about page
                     (CompanyCache TTL=7 days; domain → scraped_data JSONB)
  VaultRetriever   → semantic search Obsidian vault for company insights
                     (VaultEmbedding table; re-indexed on startup if stale)
  Memory loader    → AgentMemory records for this application

Phase 2 — Research synthesis
  ResearcherAgent  → LLM call: scraped pages + vault matches
                   → ResearchBrief {insights, tone_suggestions}

Phase 3 — Writing
  WriterAgent      → LLM call: brief + JD + profile + tone rules
                   → CoverLetterDraft {text, sections, metadata}

Phase 4 — Review loop
  HiringManagerAgent → LLM call: draft + full context
                     → ReviewVerdict {score/10, feedback, revision_suggestions}
  if score < PIPELINE_PASS_THRESHOLD (default 7.0)
  and revisions < PIPELINE_MAX_REVISIONS (default 2):
    WriterAgent regenerates with feedback → loop back to review

Phase 5 — Persistence
  Save draft as   artifacts/{human_id}/cl-v{N}.md
  Create PipelineRun row (mode, status, agent_log JSONB, final_score, revision_count)
  Post progress updates + final draft to Slack thread
```

### Quick Draft (`Quick Draft`)

Single LLM pass via `DraftGenerator`:

1. Load profile bundle + kit.
2. Fetch `ApplicationFeedback` history (ordered, all roles).
3. Fetch top 3 `AgentMemory` learning notes.
4. Call `generate_cover_letter()`.
5. Persist `artifacts/{human_id}/cl-v{N}.md`.
6. Store `ApplicationFeedback(role=SYSTEM, author=generator, text=cover_letter_md)`.

### Feedback-driven regeneration

1. User drops a note in the Slack thread (or uses the feedback input in the Manage modal).
2. Note stored as `ApplicationFeedback(role=USER, text=note)`.
3. Next Generate/Quick Draft call includes the full feedback history in the prompt payload.
4. New version is persisted as `cl-v{N+1}.md`.

### Finalize & DOCX export

- **Finalize**: sets `Application.stage = COVER_LETTER_FINALIZED`, posts confirmation.
- **DOCX**: `services/documents/docx_renderer.py` converts the markdown to a `python-docx` document: Calibri 12 pt, 1" margins, 1.15 line spacing, bold headings, proper bullet lists.  Written to `artifacts/{human_id}/cover-letter.docx` and stored as an `Artifact(type=COVER_LETTER_FINAL_PDF)`.

---

## Application tracking

### Human-readable IDs

```
Format:  APP-{YYYY}-{NNN}
Example: APP-2026-012

Query: SELECT MAX(human_id) WHERE human_id LIKE 'APP-{year}-%'
Next:  parse sequence, increment, zero-pad to 3 digits
```

### Stage state machine

```
INTERESTED
    │  (click Generate CL / Quick Draft)
    ▼
COVER_LETTER_IN_PROGRESS
    │  (click Finalize Draft)
    ▼
COVER_LETTER_FINALIZED
    │  (autofill completes or manual submit)
    ▼
SUBMITTED
    │
    ▼
INTERVIEWING
    │
    ├──▶  ACCEPTED  (posted to archive channel)
    └──▶  REJECTED  (posted to archive channel)
```

Each stage transition calls `apply_stage()` in `services/applications/stage.py`, which updates both `Application.stage` and the corresponding `ApplicationStatus`, then triggers any side effects (archive post, tracker refresh).

### Artifacts

Every application accumulates files under `artifacts/{human_id}/`:

| File | ArtifactType |
|------|-------------|
| `jd.md` | `JD_SNAPSHOT` |
| `cl-v1.md`, `cl-v2.md`, … | `COVER_LETTER_VERSION` |
| `cover-letter.docx` | `COVER_LETTER_FINAL_PDF` |
| `autofill_summary.json` | `AUTOFILL_SUMMARY` |

---

## Autofill pipeline

### Flow

```
1. Finalize Draft → auto-queues AutofillTask (if AUTOFILL_ENABLED=true)
                    or user clicks "Queue Autofill" manually

2. Backend (services/autofill/orchestrator.py):
   - Validate domain in AUTOFILL_ALLOWED_DOMAINS
   - Load ProfileIdentity (DB) or fall back to AUTOFILL_FAKE_PROFILE_PATH YAML
   - Select resume variant matching role keywords
   - Render cover-letter DOCX → PDF (if AUTOFILL_CL_PDF_ENABLED)
   - Build autofill_summary.json with application metadata + file paths
   - Create AutofillTask(status=QUEUED)
   - Post confirmation to SLACK_JOBS_DRAFTS_CHANNEL thread

3. Start (user clicks "Autofill" or auto-start):
   - AutofillTask → IN_PROGRESS
   - Open JD URL appended with #ajp_autofill=APP-YYYY-NNN
   - Post ops notification to AUTOFILL_OPS_CHANNEL

4. Browser extension (autofill_extension/content.js):
   - Detects #ajp_autofill fragment
   - GET /api/v1/autofill/payload/{human_id}  (X-Autofill-Token header)
   - Enumerates form fields (inputs, selects, radios, textareas) + labels
   - POST /api/v1/autofill/answer → LLM answers fields from profile
   - Fills supported ATS forms (Greenhouse, Workday; extensible)
   - POST /api/v1/autofill/status to report in_progress / ready / blocked / failed

5. Backend status handler:
   - Transitions AutofillTask state
   - Posts progress to Slack thread + ops channel
   - Records final_url on completion
```

### AutofillTask status enum

| Status | Meaning |
|--------|---------|
| `QUEUED` | Created, waiting for extension to start |
| `IN_PROGRESS` | Extension is actively filling the form |
| `READY` | Form filled, awaiting submit or user confirmation |
| `BLOCKED` | Human action needed (CAPTCHA, custom field) |
| `FAILED` | Error during filling or submission |
| `SKIPPED` | Domain not allowed or profile missing |

### Extension ATS support

| ATS | Fields auto-filled |
|-----|-------------------|
| **Greenhouse** | first/last name, email, phone, location, LinkedIn, GitHub, resume highlight |
| **Workday** | first/last name, email, phone, city, postal code, resume highlight |
| Additional ATS | extend `autofill_extension/content.js` |

The `/api/v1/autofill/answer` endpoint calls the LLM with a system prompt that strictly limits answers to provided profile data ("Do not invent. Match select options exactly."), so there is no hallucinated data risk.

---

## Database schema

All models are in `agentic_jobs/db/models.py`.

### Core tables

| Table | PK | Notable columns |
|-------|----|----------------|
| `jobs` | UUID | `title`, `company`, `jd_text`, `requirements[]`, `canonical_job_id`, `content_hash`, `domain_root` |
| `job_sources` | UUID | `job_id FK`, `source_type (enum)`, `raw_payload`, `canonical_hash`, `scraped_at` |
| `applications` | UUID | `human_id`, `job_id FK`, `status (enum)`, `stage (enum)`, `score`, `slack_channel_id`, `slack_thread_ts` |
| `artifacts` | UUID | `application_id FK`, `type (enum)`, `uri` |
| `application_feedback` | UUID | `application_id FK`, `role (enum)`, `author`, `text` |
| `autofill_tasks` | UUID | `application_id FK`, `status (enum)`, `mode (enum)`, `domain_root`, `payload_path`, `final_url` |
| `pipeline_runs` | UUID | `application_id FK`, `mode (enum)`, `status (enum)`, `agent_log JSONB`, `final_score`, `revision_count` |
| `frontier_orgs` | UUID | `source`, `org_slug`; unique `(source, org_slug)`, `priority`, `last_crawled_at`, `muted_until` |
| `trust_events` | UUID | `domain_root (indexed)`, `score`, `signals JSONB`, `verdict (enum)` |
| `domain_reviews` | UUID | `domain_root (unique)`, `status (enum)`, `muted_until`, `resolved_at` |
| `whitelist` | `domain_root` (PK) | `company_name`, `ats_type`, `approved_by`, `approved_at` |
| `digest_logs` | UUID | `job_id FK`, `digest_date`, `slack_channel_id`, `slack_message_ts`; unique `(job_id, digest_date)` |
| `tracker_views` | UUID | `view_type (unique)`, `slack_channel_id`, `slack_message_ts` |

### Profile tables

| Table | Notes |
|-------|-------|
| `profile_identities` | One row per user identity |
| `profile_links` | `identity_id FK (unique)` — linkedin, github, portfolio |
| `profile_facts` | `identity_id FK (unique)` — `skills JSONB`, `tools JSONB`, education |
| `profile_files` | `identity_id FK (unique)` — `resume_variants JSONB` |

### Intelligence tables

| Table | Notes |
|-------|-------|
| `agent_memories` | `application_id FK (nullable)`, `memory_type` (SHORT_TERM/LONG_TERM), `category` (STYLE_PREFERENCE/COMPANY_INSIGHT/FEEDBACK_PATTERN), `expires_at` |
| `vault_embeddings` | `(file_path, heading)` unique; `embedding (vector)`, `file_hash` — backing store for Obsidian vault semantic search |
| `company_cache` | `domain (unique)`, `scraped_data JSONB`, `ttl_hours` (default 168 = 7 days) |

---

## Scheduler & cron

`services/scheduler/cron.py`:

```
start_scheduler()
  └─ asyncio background loop
       ├─ _next_run_time(now_pt)   ← aligns to DISCOVERY_INTERVAL_HOURS
       │   boundaries within       ←  SCHEDULER_WINDOW_START/END_HOUR_PT
       ├─ sleep until next run
       ├─ _run_discovery_cycle()
       │    ├─ instantiate adapters
       │    ├─ run_discovery() per adapter
       │    ├─ collect + post digest
       │    └─ collect + post domain-review cards
       ├─ _memory_assess_job()      ← every MEMORY_ASSESSMENT_INTERVAL_DAYS
       │    └─ batch recent feedback to LLM → extract long-term learnings
       │       stored as AgentMemory(LONG_TERM) for future drafts
       └─ _refresh_vault_embeddings() (startup only)
            └─ re-embed stale vault sections (hash comparison)
```

Default window: 07:00–23:00 PT, 3-hour intervals → runs at 07:00, 10:00, 13:00, 16:00, 19:00, 22:00.

---

## Configuration reference

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/agentic_jobs` | SQLAlchemy connection string |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `DEBUG` | `false` | Enable debug logging |

### Discovery

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCOVERY_BASE_URL` | `https://boards.greenhouse.io` | Greenhouse base for robots + sitemap |
| `DISCOVERY_SITEMAP_URL` | `https://boards.greenhouse.io/sitemap_index.xml` | Greenhouse sitemap |
| `DISCOVERY_INTERVAL_HOURS` | `3` | Hours between discovery cycles |
| `MAX_ORGS_PER_RUN` | `100` | Frontier batch size |
| `REQUESTS_PER_MINUTE` | `60` | Rate limit for HTTP |
| `REQUEST_TIMEOUT_SECONDS` | `5` | Per-request timeout |
| `ALLOWED_DOMAINS` | `boards.greenhouse.io,raw.githubusercontent.com,github.com` | Greenhouse adapter robots allowlist |
| `ENABLE_GREENHOUSE` | `true` | Toggle Greenhouse adapter |
| `GITHUB_MAX_AGE_DAYS` | `3` | Drop GitHub listings older than N days |
| `SIMPLIFY_POSITIONS_URLS` | (multiple) | Comma-separated fallback URLs |
| `NEW_GRAD_2026_URLS` | (multiple) | Comma-separated fallback URLs |
| `UNIVERSAL_MAX_AGE_DAYS` | `7` | Drop universal-adapter jobs older than N days |
| `JOB_FILTER_CONFIG_PATH` | `config/job_filters.yaml` | Title filter + adapter toggle config |
| `UNIVERSAL_SITES_CONFIG_PATH` | `config/universal_sites.yaml` | Universal adapter feed definitions |

### Slack

| Variable | Required | Description |
|----------|---------- |-------------|
| `SLACK_BOT_TOKEN` | ✅ | `xoxb-…` Bot User OAuth Token |
| `SLACK_APP_LEVEL_TOKEN` | ✅ | `xapp-…` App-Level Token (Socket Mode) |
| `SLACK_SIGNING_SECRET` | ✅ | Request signature verification |
| `SLACK_JOBS_FEED_CHANNEL` | ✅ | Digest + domain review |
| `SLACK_JOBS_DRAFTS_CHANNEL` | ✅ | Application threads |
| `SLACK_JOBS_TRACKER_CHANNEL` | ✅ | Pinned master tracker |
| `SLACK_JOBS_ARCHIVE_CHANNEL` | ✅ | Final outcomes |

### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BACKEND` | `lmstudio` | `mock` / `lmstudio` / `ollama` / `qwen` |
| `LLM_MODEL_NAME` | `local-model` | Model identifier sent in API requests |
| `LLM_ENDPOINT_URL` | `http://localhost:1234/v1/chat/completions` | OpenAI-compatible endpoint |
| `LLM_TIMEOUT_SECONDS` | `120` | Request timeout |
| `LLM_MAX_USER_MSG_CHARS` | `12000` | Truncate long JDs before sending |
| `LLM_API_KEY` | — | Bearer token (DashScope or any OpenAI-style provider) |
| `OLLAMA_API_KEY` | — | Bearer token for Ollama Cloud |

### Scheduler

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULER_WINDOW_START_HOUR_PT` | `7` | Start hour (PT) |
| `SCHEDULER_WINDOW_END_HOUR_PT` | `23` | End hour (PT) |
| `DIGEST_BATCH_SIZE` | `20` | Max jobs per digest |
| `MEMORY_ASSESSMENT_INTERVAL_DAYS` | `3` | Long-term memory consolidation cadence |

### Autofill

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOFILL_ENABLED` | `false` | Enable autofill system |
| `AUTOFILL_WS_PORT` | `8765` | WebSocket port |
| `AUTOFILL_MAX_CONCURRENCY` | `3` | Parallel autofill tasks |
| `AUTOFILL_OPS_CHANNEL` | — | Slack channel for ops updates |
| `AUTOFILL_ALLOWED_DOMAINS` | — | Comma-separated allowed ATS hosts |
| `AUTOFILL_ASSISTED_UPLOAD` | `true` | Highlight file inputs instead of auto-uploading |
| `AUTOFILL_CL_PDF_ENABLED` | `true` | Render cover letter to PDF before autofill |
| `AUTOFILL_FAKE_PROFILE_PATH` | `config/fake_profile.yaml` | Fallback profile when DB has no rows |
| `AUTOFILL_API_TOKEN` | — | Shared secret for `/autofill/payload` + `/autofill/status` endpoints |

### Vault / Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_PATH` | — | Path to Obsidian vault (optional) |
| `EMBEDDING_MODEL_NAME` | `nomic-embed-text-v1.5` | Embedding model |
| `EMBEDDING_ENDPOINT_URL` | `http://localhost:1234/v1/embeddings` | LM Studio embeddings endpoint |
| `VAULT_TOP_K` | `5` | Top-K results per semantic search query |
| `VAULT_LINK_DEPTH` | `1` | Wikilink traversal depth |

### Multi-agent pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPELINE_PASS_THRESHOLD` | `7.0` | HiringManager score (out of 10) required to accept draft |
| `PIPELINE_MAX_REVISIONS` | `2` | Maximum Writer→HiringManager revision loops |
| `SCRAPER_RATE_LIMIT` | `5` | Concurrent company scrape requests |
| `SCRAPER_TIMEOUT_SECONDS` | `10` | Per-request timeout for company scraper |
| `COMPANY_CACHE_TTL_HOURS` | `168` | CompanyCache TTL (7 days) |

---

## Setup & quick-start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp env_template.sh env_local.sh
# Edit env_local.sh with your DATABASE_URL, Slack tokens, and LLM settings
source env_local.sh
```

Minimum required vars: `DATABASE_URL`, `SLACK_BOT_TOKEN`, `SLACK_APP_LEVEL_TOKEN`, `SLACK_SIGNING_SECRET`, the four Slack channel IDs.

### 3. Start the server

```bash
./start_server.sh
# or:
uvicorn agentic_jobs.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Verify

```bash
# Health
curl http://localhost:8000/healthz

# Trigger a manual discovery run
curl -X POST http://localhost:8000/api/v1/discover/run \
  -H 'content-type: application/json' -d '{}'
# → {"orgs_crawled":3,"jobs_seen":42,"jobs_inserted":18,"domains_scored":6}
```

### 5. Detect a careers-page parser (optional)

```bash
python -m agentic_jobs.scripts.detect_site https://www.metacareers.com/jobsearch/
# Prints inferred parser + options — paste into universal_sites.yaml
```

### 6. Run the test suite

```bash
pytest -q
```

| Test module | Coverage |
|-------------|---------|
| `tests/discovery/test_frontier_greenhouse.py` | Frontier seeding, dedup, trust events |
| `tests/discovery/test_github_adapter.py` | Fallback URLs, schema variants, age filter |
| `tests/sources/test_normalize.py` | HTML normalization and hashing |
| `tests/slack/test_actions_save_to_tracker.py` | Application creation, ID generation |
| `tests/slack/test_digest_render.py` | Digest block structure |
| `tests/slack/test_workflows.py` | End-to-end workflow integration |

### 7. Configure the Slack app

See `SLACK_SETUP.md` for the full App Manifest, OAuth scopes, Socket Mode setup, and ngrok / production URL configuration.

---

### Notes

- **Restart required** after changing `.env` — pydantic-settings caches values at import time.
- Set `ENABLE_GREENHOUSE=false` and `GITHUB_MAX_AGE_DAYS=7` for local dev without Greenhouse access.
- The autofill browser extension lives under `autofill_extension/` — load as an unpacked extension from `chrome://extensions` and configure the local API URL + token from the extension options page.
- `config/fake_profile.yaml` is the fallback autofill profile when no `ProfileIdentity` rows exist in the database.
