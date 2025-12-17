# Agentic Job Applications — Current Implementation Status

> **System Overview**: A production-ready Slack-first, human-in-the-loop system that automatically discovers new-grad/backend/full-stack roles, scrapes JDs, ranks them using deterministic rules, tracks applications with a clean Slack workflow, and provides infrastructure for cover-letter generation.
> 
> **Current Status**: Core system is fully implemented and operational. Cover letter generation and advanced features are in development.
> 
> **Architecture Principles**:
> * No auto-submit (human approval required)
> * Public pages only (no logged-in scraping)
> * One Slack thread per application
> * Deterministic scoring and ranking

---

# ✅ MVPart 7 — Master Tracker + Configurable Discovery Filters (IN PROGRESS)

**Highlights shipped so far**

1. **Slack Master Tracker overhaul**
   - Applications now carry a dedicated `stage` enum (with Alembic migrations) so lifecycle tracking is decoupled from status codes.
   - Saving to the tracker snapshots the JD, spins up a cover-letter workspace thread, and refreshes a pinned tracker message instead of spamming Slack.
   - Tracker UI renders as paginated Slack messages (25 rows per page) with Manage modals exposing JD text, finalized cover letters, and quick stage changes.
   - Stage changes trigger archive summaries (Rejected/Accepted) in a separate channel and refresh the tracker asynchronously to avoid modal glitches.

2. **Cover-letter workflow polish**
   - Generate/finalize actions advance stages (Interested → CL In Progress → CL Finalized) automatically so the tracker mirrors drafting progress.
   - Thread feedback auto-regens refresh the tracker silently to keep the workspace noise-free.

3. **Configurable job intake filters**
   - Added `config/job_filters.yaml` + `JOB_FILTER_CONFIG_PATH` so users can toggle adapters (Greenhouse, Simplify, NewGrad2026) and edit include/exclude keywords without touching code.
   - Discovery filters titles before ingest so only SWE / new-grad friendly roles reach Slack.

4. **Digest card refresh**
   - Slack cards now read `Company` → `Role · Location` → `Score/Rationale`, making scans in `#jobs-feed` easier.

> MVPart7 keeps the Slack tracker actionable while giving every user a simple way to tune job sources + filters.

## Global Product Specs (canonical across MVPs)

### Slack Channels (fixed)

* `#jobs-feed` — periodic digests (new roles + scores) and one-time Needs-Review cards for untrusted domains.
* `#jobs-drafts` — an inbox of active cover-letter drafts; each card links back to the correct application thread.
* `#jobs-completed` — submissions/archives (used later).

### Schedule

* **Every 3 hours** between **06:00 and 23:00 PT** (inclusive) for discovery + digest posts.

### Target roles (filters)

* **Include** titles with: “Software Engineer”, “Backend”, “Back-End”, “Full Stack”, “Full-Stack”, “SWE”.
* **Include** JDs with: “new grad”, “entry level”, “university grad”, “graduate”.
* **Do not** penalize “3+ years” text.

### Location boosts

* Cities/regions: **New York (NYC)**, **Seattle**, **SF Bay Area** (San Francisco, San Jose, Sunnyvale, Mountain View, Palo Alto, Redwood City, Oakland, Berkeley), **Los Angeles (LA)**, **Irvine / Orange County**.
* +0.10 if location string includes any above.
* +0.05 if **Remote**/**Hybrid** and tied to any above.

### Skills to boost (exact spellings to match)

```
Python, Java, C++, Swift, SQL, MySQL, MongoDB, HTML, CSS, Power BI, Linux,
LangChain, NumPy, CrewAI, Ollama, Streamlit, SQLAlchemy,
Agentic AI, AI Agent, RAG, retrieval-augmented, LLM fine-tuning, multimodal
```

### Trust Gate (v1)

* Signals: domain–brand coherence, ATS provenance (Greenhouse/Lever), TLS/HSTS.
* Verdicts: `auto-safe (>=70)`, `needs-human-approval (40–69)`, `reject (<40)`.
* Unseen domains: post one **Needs-Review** card to `#jobs-feed`. **Approve** adds to whitelist.

### “Already applied?” rule

* Compute **canonical job id**:

  * Greenhouse: `GH:<job_numeric_id>`
  * Lever: `LEVER:<org>/<slug>` (future)
* Block duplicate **Applications** by canonical id. Warn on near-dupes (same company + similar title + high JD similarity).

### LLM for drafting (MVP-3)

* **Model**: Llama 3.1 8B Instruct (via Ollama or vLLM).
* **Style Card** (inject every time):

  * Tone: compassionate, empathetic, confident, clear
  * Short sentences, 1–2 sentence paragraphs
  * **No em dashes, no semicolons**
  * Active voice; mirror **5–10%** of the JD’s phrasing only
  * No fabrication of facts/dates/employers; use a concrete metric when available

---

# ✅ MVPart 1 — Project Boot + Core Data Contracts (COMPLETED)

**Status**: ✅ **FULLY IMPLEMENTED**

1. ✅ Project structure created (FastAPI + Postgres + Slack client scaffolding)
2. ✅ Complete database schema with all models: `jobs`, `job_sources`, `trust_events`, `whitelist`, `applications`, `artifacts`, `profile_*`, `frontier_orgs`, `digest_logs`, `domain_reviews`
3. ✅ API endpoints implemented for all core functionality

**Data contracts (authoritative)**

* **Job (normalized):**

  ```
  id (uuid), title, company_name, location, url,
  source_type (greenhouse|lever|company),
  domain_root, submission_mode (ats|deeplink),
  jd_text, requirements (jsonb[]), job_id_canonical (string),
  scraped_at (ts), hash (sha1 over title+company+jd_text)
  ```

* **JobSource (raw provenance):**

  ```
  id (uuid), source_type, source_url,
  company_name?, domain_root, raw_payload (jsonb), discovered_at (ts), hash
  ```

* **TrustEvent:**

  ```
  id (uuid), domain_root, url, score (int),
  signals (jsonb[]), verdict (string), created_at (ts)
  ```

* **Whitelist:**

  ```
  domain_root (pk), company_name?, ats_type?, approved_by?, approved_at (ts)
  ```

* **Application (tracker):**

  ```
  id (APP-YYYY-NNN human id + uuid internal), job_id (fk),
  status (Queued|Drafting|Draft Ready|Approved|Submitted|Rejected|Closed),
  slack_channel_id, slack_thread_ts, score (float), canonical_job_id,
  submission_mode, created_at, updated_at
  ```

* **Artifact:**

  ```
  id (uuid), application_id (fk), type (jd_snapshot|cover_letter_vN|autofill_summary|confirmation),
  uri, created_at
  ```

* **Profile (PII + facts):**

  * `profile_identity`: name, preferred_name?, email, phone, base_location
  * `profile_links`: linkedin, github, portfolio
  * `profile_facts`: skills[], tools[], frameworks[], projects[{name, one_liner, metric}], education, work_auth
  * `profile_files`: resume_variants[{label, uri, created_at}]

  > PII encrypted at rest; logs redacted.

**APIs (declare only; implement later)**

* `POST /discover/run` → starts discovery cycle (will be called by scheduler)
* `GET /digest/latest` → returns last run’s top jobs
* `POST /applications/create { job_id }` → creates Application, returns `APP-ID`, Slack thread metadata
* `POST /trust/evaluate { url, company_name? }` → trust verdict
* `POST /trust/whitelist { domain_root, company_name?, ats_type?, approved_by? }`
* `POST /drafts/create { application_id }` → generates CL (MVP-3)
* `POST /drafts/feedback { application_id, feedback_slots{} }` → targeted regen (MVP-3)

**You (human)**

* ✅ Nothing to configure yet.
* ✅ Acceptance: Tables and schemas exist; API contracts documented.

**Tests to create**

* Schema validation tests (Pydantic can roundtrip representative JSON for each model).

---

# ✅ MVPart 2 — Seedless Discovery (Open-Source) + JD Scrape + Dedup + Trust (COMPLETED)

**Status**: ✅ **FULLY IMPLEMENTED**

## Intent

✅ Built a **fully open-source, seedless** discovery engine for **Greenhouse** and **GitHub** that does **not** rely on Google/SerpAPI. The system is architected to **expand** to Lever, Workday, LinkedIn, etc., by adding adapters—without changing the core pipeline.

## ✅ Implemented Design

* ✅ **Sitemap-based frontier**: Parse `https://boards.greenhouse.io/sitemap.xml` to discover **all** GH org slugs. Seed the **frontier** from this list (stored in DB).
* ✅ **Polite async crawler**: Fetch org **JSON job feeds** (when available) or list pages, then fetch each **job detail page** to extract JD HTML.
* ✅ **Normalization**: Produce `Job` rows with JD text, `requirements[]`, `job_id_canonical = "GH:<numeric_id>"`, and `hash`.
* ✅ **Dedup**: Prefer canonical id; fallback to content hash within a 30-day window.
* ✅ **Trust Gate**: Evaluate domain once; persist `TrustEvent` with Slack integration.
* ✅ **Extensibility**: Use a **SourceAdapter interface** so Lever/Workday extensions can plug in later.
* ✅ **GitHub Integration**: Added SimplifyJobs and New-Grad-2026 adapters for additional job sources.

## ✅ Completed Tasks

1. ✅ **Frontier store (DB)**
   - ✅ Added `frontier_orgs` table with all required fields
   - ✅ Seed step: parse `boards.greenhouse.io/sitemap.xml`, extract `<loc>` ending in `/slug`, and add new slugs (upsert)
   - ✅ Respect robots.txt for `boards.greenhouse.io` (read once per run; cache decision)

2. ✅ **Async fetcher (GreenhouseAdapter)**
   - ✅ Created `services/discovery/greenhouse_adapter.py` with async class
   - ✅ `discover_from_sitemap()` → seeds org slugs
   - ✅ `list_jobs(org_slug)` → returns minimal job refs with JSON feed preference
   - ✅ `fetch_job_detail(url)` → returns JD HTML with normalization
   - ✅ Rate limiting with `aiolimiter` to ~60 req/min across host; timeouts (~5s)
   - ✅ Always use **HTTPS**

3. ✅ **Normalization & fields**
   - ✅ Implemented `services/sources/normalize.py` with:
     - ✅ `html_to_text(html) -> str` (strip tags, collapse whitespace)
     - ✅ `extract_requirements(text) -> list[str]` (heuristics around "Requirements/Qualifications/Responsibilities")
     - ✅ `compute_hash(title, company, jd_text) -> sha1 hex`
   - ✅ Build `job_id_canonical` as `"GH:<id>"` from the detail URL or JSON entry

4. ✅ **Dedup rules**
   - ✅ If `job_id_canonical` seen in last 30 days → skip insert
   - ✅ Else if `hash` seen in last 30 days → skip insert
   - ✅ Else insert **JobSource** + **Job**

5. ✅ **Trust Gate**
   - ✅ Run Trust Gate (v1) per job URL; persist `TrustEvent`
   - ✅ **Slack integration implemented** for domain review workflow

6. ✅ **Extensibility hooks**
   - ✅ Defined `SourceAdapter` protocol/interface in `services/discovery/base.py`
   - ✅ Greenhouse implements it; GitHub adapters added
   - ✅ Added **domain allowlist** to prevent unintended crawling

7. ✅ **Orchestrator endpoint**
   - ✅ Implemented `/discover/run` with:
     - ✅ Ensure frontier is seeded (sitemap parsed at least once)
     - ✅ Pop up to `MAX_ORGS_PER_RUN` orgs by priority/recency
     - ✅ Crawl them, normalize + dedup + trust-evaluate
     - ✅ Return JSON summary: `{ "orgs_crawled": N, "jobs_seen": M, "jobs_inserted": K, "domains_scored": D }`

## Env & Config

Add to `.env` (read via Settings):

```
DISCOVERY_BASE_URL=https://boards.greenhouse.io
DISCOVERY_SITEMAP_URL=https://boards.greenhouse.io/sitemap.xml
DISCOVERY_INTERVAL_HOURS=3
MAX_ORGS_PER_RUN=100
REQUESTS_PER_MINUTE=60
REQUEST_TIMEOUT_SECONDS=5
ALLOWED_DOMAINS=boards.greenhouse.io
```

## Acceptance

✅ Frontier seeded from GH sitemap (persisted org slugs).
✅ `/discover/run` crawls up to `MAX_ORGS_PER_RUN` orgs asynchronously and returns a summary JSON.
✅ Each new job produces a normalized `Job` with: `title`, `company_name`, `location`, `url`, `source_type="greenhouse"`, `domain_root`, `submission_mode="ats"`, `jd_text`, `requirements[]`, `job_id_canonical="GH:<id>"`, `scraped_at`, `hash`.
✅ Dedup prevents repeats by canonical id, else by hash (30-day window).
✅ TrustEvents written for domains encountered.
✅ No external paid APIs used; only HTTPS GETs to public pages; robots.txt respected; rate limiting applied.
✅ Tests pass (see below).

## Tests

* **Fixtures**:

  * `tests/fixtures/gh_sitemap.xml` (small sample)
  * `tests/fixtures/gh_board_json.json` and/or `gh_board_html.html`
  * `tests/fixtures/gh_job_detail_*.html`
* **Unit**:

  * Sitemap parser extracts slugs correctly.
  * `normalize.html_to_text` and `extract_requirements` behave on small HTML samples.
  * Canonical id parsing `GH:<id>` consistent from URL/JSON.
* **Integration (mocked HTTP)**:

  * End-to-end `/discover/run` inserts jobs; dedup works; returns correct counts.
  * TrustEvent rows written.

## Expected artifacts

```
agentic_jobs/
 ├── services/
 │    ├── discovery/
 │    │     ├── base.py            # SourceAdapter interface
 │    │     ├── greenhouse_adapter.py
 │    │     └── orchestrator.py    # used by /discover/run
 │    └── sources/
 │          └── normalize.py
 ├── tests/
 │    ├── discovery/test_frontier_greenhouse.py
 │    ├── sources/test_normalize.py
 │    └── fixtures/{gh_sitemap.xml, gh_board_json.json, gh_board_html.html, gh_job_detail_*.html}
```

## What you (human) do for MVPart 2

* No API keys required.
* Run `/discover/run` locally (via TestClient or curl) after the part completes.
* Expect: summary output and ≥30 unique GH jobs in `jobs` table (given sample fixtures + a limited real run if desired).
* Verify: dedup honored; TrustEvents created; **no Slack posts yet**.

---

# ✅ MVPart 3 — Slack Digest + Needs-Review + Tracker Threads (COMPLETED)

**Status**: ✅ **FULLY IMPLEMENTED**

## ✅ Completed Objectives

1. ✅ Implemented the **3-hour scheduler** (06:00–23:00 PT) with configurable time windows
2. ✅ After discovery, **rank** the newly ingested jobs with deterministic rules
3. ✅ Post a **digest** to `#jobs-feed`: compact rows (Title · Company · Location · **Score chip**), actions: **Open JD**, **Save to Tracker**
4. ✅ For **unknown domains**, post a **Needs-Review** card with Approve/Reject; approving writes to `whitelist`
5. ✅ **Save to Tracker** creates an **Application** and starts a **Slack thread** with header message containing job details + score + canonical id + status `Queued`

**Slack UX rules**

* **One thread per Application**; all future CL activity for that job stays in that thread.
* Digest is **periodic**; it should not spam duplicates.
* Needs-Review is **once per domain** (until approved/rejected).

**You (human)**

* Provide **Slack bot token**, **signing secret**, **channel ids** (or channel names).
* Invite the bot to channels.
* Acceptance: Every 3 hours you see a digest with score chips. Clicking **Save to Tracker** creates a thread with APP-ID, and unknown domains trigger a one-time Needs-Review card. Approve adds to whitelist.

**Tests**

* Mock Slack client in unit tests.
* Integration test: simulate a set of new jobs, confirm digest rows rendered, confirm thread creation on Save to Tracker.

---

# ✅ MVPart 4 — Deterministic Ranking (Skills + Geo Boosts) (COMPLETED)

**Status**: ✅ **FULLY IMPLEMENTED**

## ✅ Completed Objectives

1. ✅ Implemented the **rules engine** for scoring (no embeddings) in `services/ranking/scorer.py`
2. 🔄 Expose a `rank.yaml` config to tune weights without code changes (planned for future enhancement)
3. ✅ Return `score` and a concise **rationale** string for each job ("new grad + backend + Python + SF Bay (remote)")

**Default weights (can be stored in `rank.yaml`)**

* Title match: **+0.15** if title contains “Software Engineer”, “Backend”, “Back-End”, “Full Stack”, “Full-Stack”, “SWE”.
* New-grad phrases: **+0.20** if JD contains “new grad”, “entry level”, “university grad”, “graduate”.
* Skills (search JD text, case-insensitive):

  * Python (+0.08), Java (+0.07), C++ (+0.06), Swift (+0.05), SQL/MySQL (+0.05), MongoDB (+0.04),
  * HTML/CSS (+0.03), Linux (+0.03), Power BI (+0.03),
  * LangChain (+0.05), NumPy (+0.04), SQLAlchemy (+0.04), Streamlit (+0.04), CrewAI (+0.04), Ollama (+0.04),
  * Agentic AI / AI Agent / RAG / retrieval-augmented / LLM fine-tuning / multimodal (+0.05 each; cap combined at +0.10).
* Location boosts:

  * **+0.10** if location mentions any: NYC, Seattle, SF Bay (San Francisco, San Jose, Sunnyvale, Mountain View, Palo Alto, Redwood City, Oakland, Berkeley), LA/Los Angeles, Irvine/Orange County.
  * **+0.05** if Remote/Hybrid **and** tied to any above (e.g., “Remote (US) — SF/Bay Area preferred”).
* **No penalty** for “3+ years”.
* Clamp final score to **[0,1]**.

**You (human)**

* Confirm the list of geo names is correct; you can update `rank.yaml` any time.
* Acceptance: Digest ordering makes sense; top 10 clearly better than bottom 10; rationale reads well.

**Tests**

* Synthetic JDs: verify each keyword and geo contributes the expected delta.
* Rationale builder tests.

---

# ✅ MVPart 5 — Cover-Letter Drafting (LLM) + Organized Review

**Status**: ✅ **FULLY IMPLEMENTED**

## Current Capabilities

1. Rich YAML “cover letter kit” stored at `agentic_jobs/profile/cover_letter_kit.yaml` with tone, structure, reasoning guidance, resume context, and project mapping rules. Loading handled via `services/llm/style_kit.py`.
2. Prompt builder (`services/llm/prompt_builder.py`) assembles the full JSON payload: JD summary, profile, toolkit, feedback history, and slot hints.
3. LLM runners support:
   * `LLM_BACKEND=mock` — deterministic stub
   * `LLM_BACKEND=qwen` — DashScope JSON API (`LLM_ENDPOINT_URL`, `LLM_API_KEY`)
   * `LLM_BACKEND=ollama` — OpenAI-compatible Ollama Cloud (`LLM_ENDPOINT_URL=https://ollama.com/v1/chat/completions`, `LLM_API_KEY` or `OLLAMA_API_KEY`)
   Each call enforces JSON output and retries on transient errors.
4. Slack workflow:
   * Saving a role posts tracker cards into `SLACK_JOBS_DRAFTS_CHANNEL` with **Generate draft** and **Finalize draft** buttons.
   * Thread messages auto-save as feedback and trigger a regen (no modal).
   * “Finalize draft” marks the application `Draft Ready`, posts a confirmation, and logs a system learning note.
5. Artifacts: every draft is stored under `artifacts/APP-YYYY-NNN/cl-vN.md` and logged in `application_feedback` (`role=user|assistant|system`).
6. `/api/v1/drafts/create` and `/api/v1/drafts/feedback` call the runner directly, returning the latest CL metadata.

## Prompt Contract (Summary)

```json
{
  "kit_version": "...",
  "app_id": "APP-2025-000123",
  "role": { "title": "...", "company": "...", "location": "..." },
  "job_url": "...",
  "jd": { "summary": "...", "bullets": [...], "phrases": [...], "tone_sample": "..." },
  "profile": { "identity": {...}, "links": {...}, "skills": [...], "projects": [...], "stack": [...] },
  "project_card": {...},
  "style_card": {...},
  "slots": {
    "opener_hint": "...",
    "why_company": [...],
    "role_alignment_targets": [...],
    "impact_picks": [...],
    "plan_hints": [...],
    "stack_focus": [...]
  },
  "feedback": {"latest_notes": [...], "history": [...]},
  "learning_notes": [...],
  "toolkit": {
    "education": [...],
    "skills_card": {...},
    "experience_highlights": [...],
    "leadership_highlights": [...],
    "style_examples": [...],
    "reasoning_guidance": [...],
    "structure": {...}
  }
}
```

Models must respond with:

```json
{
  "version": "CL v1",
  "cover_letter_md": "Dear Hiring Manager, ... Best regards, Apoorva Chilukuri",
  "sections_used": [...],
  "provenance": {...}
}
```

## Slack UX Rules

* All CL activity stays in the application thread; tracker cards live in `SLACK_JOBS_DRAFTS_CHANNEL`.
* Thread messages are persisted to `application_feedback`; regens respond inline.
* Finalize pins the latest version, posts a learning summary, and flips status to `Draft Ready`.

## Configuration Checklist

| Setting | Description |
| --- | --- |
| `LLM_BACKEND` | `mock`, `qwen`, or `ollama` |
| `LLM_MODEL_NAME` | Backend-specific model name |
| `LLM_ENDPOINT_URL` | e.g., DashScope generation endpoint or `https://ollama.com/v1/chat/completions` |
| `LLM_API_KEY` / `OLLAMA_API_KEY` | Provider token |
| `LLM_TIMEOUT_SECONDS` | Increase if models are slow (default `60`) |

## Tests / Safety

* `tests/llm/` covers kit loading and prompt assembly.
* Runner retries on transient HTTP errors and surfaces readable Slack errors when a backend is unavailable.
* Every draft/version logged via artifacts + feedback rows for auditability.

---

# ✅ MVPart 6 — Operational Details (Scheduler, Idempotency, Observability) (COMPLETED)

**Status**: ✅ **FULLY IMPLEMENTED**

## ✅ Completed Objectives

1. ✅ Implemented scheduler windows (06:00–23:00 PT) with **3-hour cadence**; idempotent runs (don't repost the same jobs)
2. ✅ Logging: structured JSON; redact PII; include `app_id` / `job_id` in log contexts
3. ✅ Metrics counters (even simple logs): jobs_seen, jobs_new, digest_rows_posted, domains_review_posted, applications_created, drafts_generated

**You (human)**

* Verify digests do not repost identical items within the same day; check logs for counters.
* Acceptance: predictable cadence; no duplicate noise; clear counters in logs.

---

## ✅ Current System Status

### **Fully Operational**
* ✅ **Job Discovery**: Automatic discovery from Greenhouse and GitHub sources
* ✅ **Slack Integration**: Interactive components, digests, and application tracking
* ✅ **Trust System**: Domain review and whitelist management
* ✅ **Application Tracking**: Complete lifecycle from discovery to submission
* ✅ **Scoring System**: Deterministic job ranking with rationale

### **In Development**
* 🔄 **Cover Letter Generation**: LLM integration for automated cover letter drafting
* 🔄 **Advanced Features**: Enhanced ranking, profile management, additional data sources

## What you (human) need to do

* **Secrets & config**: Provide Slack bot token, signing secret, channel ids (or names)
* **Review cadence**: Expect digests **every 3 hours** in `#jobs-feed`. Approve any new domains once; click **Save to Tracker** on roles you want to pursue
* **Application management**: Use Slack threads to track applications, approve domains, and manage the job application process

---

## Questions / Info Codex may need to ask you during build

* **Search provider**: Confirm which search API to use for seedless GH discovery (SerpAPI or Programmable Search). Provide API key if needed.
* **Slack workspace**: Are the channel names exactly `#jobs-feed`, `#jobs-drafts`, `#jobs-completed`? If different, provide the exact names/ids.
* **Allow/deny list**: Any companies you want always included or excluded?
* **Artifacts storage**: For MVPs, local disk is fine. If you prefer S3/minio now, provide bucket credentials.
* **PII**: Provide your profile facts (skills list above is great), links, and resume PDF(s) so the tracker and drafts have accurate info.

---

### Final note for Codex

* Keep **idempotency** everywhere (dedup by canonical id and hash).
* Keep **safety**: never automate behind logins; never submit forms; never store secrets in logs.
* Keep **organization**: one **Application → one Slack thread**; mirror only small cards in `#jobs-drafts`.
* Implement **tests per MVPart** before moving on.
