# Multi-Agent Cover Letter Generation — Architecture

Part of the **Agentic Job Search Copilot**. This document describes the design and implementation of the LLM-powered cover letter pipeline.

---

## Two drafting modes

| Button in Slack | Action ID | Behaviour |
|-----------------|-----------|-----------|
| **Quick Draft** | `drafts_quick` | Single LLM pass via `DraftGenerator`. Fast (~15 s). Best for iterating with feedback. |
| **Generate CL** | `drafts_generate` | Full multi-agent pipeline via `PipelineCoordinator`. Research-backed, tailored, reviewer-gated. |

---

## Pipeline overview

```
Slack "Generate CL"
        │
        ▼
PipelineCoordinator.run()
        │
        ├─ [1] DATA GATHERING  (parallel, no LLM)
        │       ├── CompanyScraper   → company about / careers pages (cached 7 days)
        │       │     domain resolved by _resolve_company_domain():
        │       │       1. job.company_website  (set at ingestion)
        │       │       2. strip job-subdomain prefixes from domain_root (legacy fallback)
        │       │       3. pure ATS domain → None → skip + write vault stub
        │       ├── VaultRetriever   → semantic search over Obsidian vault
        │       └── MemoryStore      → long-term learnings from agent_memories table
        │
        ├─ [2] RESEARCH PHASE
        │       └── ResearcherAgent  (LLM, temp=0.2)
        │           Input:  JD, scraped company data, vault excerpts, profile, memory notes
        │           Output: ResearchBrief
        │                   ├── company_context
        │                   ├── role_themes         (3–5 themes from JD)
        │                   ├── jd_requirements     (hard requirements to address)
        │                   ├── matched_experiences (candidate ↔ JD alignment)
        │                   └── suggested_project   (kit project best matching role)
        │
        ├─ [3] WRITE PHASE
        │       └── WriterAgent  (LLM, temp=0.35)
        │           Input:  ResearchBrief + CoverLetterKit tone/structure + word budget
        │           Output: CoverLetterDraft v1
        │
        └─ [4] REVIEW LOOP  (max PIPELINE_MAX_REVISIONS, default 2)
                ├── HiringManagerAgent  (LLM, temp=0.2)
                │   Input:  draft + ResearchBrief + JD + tone rules
                │   Output: ReviewVerdict (score /10, verdict, feedback, strengths)
                │
                ├── score ≥ PIPELINE_PASS_THRESHOLD (default 7.0)  →  DONE
                └── score < threshold  →  WriterAgent revision round
                                          → back to HiringManagerAgent
```

---

## Agent roles

### ResearcherAgent
**File:** `agentic_jobs/services/agents/researcher.py`

Synthesizes all external data into a structured brief before any writing begins. Data gathering is done by the coordinator in parallel; this agent only reasons over the pre-fetched content.

- Temperature: `0.2` (analytical)
- Context budget: JD (~2 000 chars), company data (~2 500 chars), vault excerpts (~800 chars each, up to 4)
- Key output: `suggested_project` — which project from `cover_letter_kit.yaml` best fits the role

### WriterAgent
**File:** `agentic_jobs/services/agents/writer.py`

Writes in the candidate's voice using tone/structure rules from `cover_letter_kit.yaml`. Constrained to a word budget derived from the DOCX page geometry.

- Temperature: `0.35`
- Word budget: ~400 words

**Budget calculation:**
```
content_height (648pt) / line_height (15pt)     = 43 max lines
43 − 8 reserved (greeting, signoff, blanks)     = 35 usable lines
496.8pt width / (12pt × 0.52 char factor) / 5.5 avg word length = 14.5 words/line
35 × 14.5 × 0.88 safety factor                 = 446 → capped at 400
```

Handles both the initial draft and revision rounds (different system prompt variant for revisions).

### HiringManagerAgent
**File:** `agentic_jobs/services/agents/reviewer.py`

Evaluates the draft as a hiring manager with full context — not just the letter itself.

- Temperature: `0.2`
- Scoring rubric (5 dimensions × 2 points = 10 total):
  1. **Role Alignment** — addresses specific JD requirements
  2. **Company Fit** — demonstrates knowledge of this specific company
  3. **Technical Depth** — concrete work with measurable outcomes
  4. **Tone & Voice** — follows specified tone rules
  5. **Conciseness** — within word budget, no filler
- Pass threshold: `PIPELINE_PASS_THRESHOLD` (default `7.0`)

---

## Data contracts

**File:** `agentic_jobs/services/agents/schemas.py`

```
ResearchBrief
  company_name, company_domain, company_context
  role_themes:          list[str]
  jd_requirements:      list[str]
  matched_experiences:  list[str]
  vault_excerpts:       list[str]
  memory_notes:         list[str]
  suggested_project:    str

CoverLetterDraft
  version:       int
  content_md:    str
  word_count:    int
  sections_used: list[str]

ReviewVerdict
  score:                    float  (0–10)
  verdict:                  "pass" | "revise"
  overall_impression:       str
  feedback:                 list[str]
  strengths:                list[str]
  areas_for_improvement:    list[str]

PipelineResult
  final_draft:       CoverLetterDraft
  research_brief:    ResearchBrief
  review_history:    list[ReviewVerdict]
  pipeline_run_id:   UUID
  total_duration_ms: int
```

---

## Supporting systems

### Obsidian vault integration
**Files:** `agentic_jobs/services/vault/`

```
VaultParser     → parse all .md files → VaultSection objects
WikilinkGraph   → bidirectional [[wikilink]] adjacency map
VaultEmbedder   → embed sections via /v1/embeddings, store in vault_embeddings table
VaultRetriever  → embed query → cosine similarity → expand with wikilink neighbors
```

- Embedding model: `nomic-embed-text-v1.5` (LM Studio)
- **Wikilink expansion**: when a section matches, `neighbors(depth=VAULT_LINK_DEPTH)` pulls in linked sections for richer context
- **Refresh**: on server startup, `VaultEmbedder.refresh_stale()` uses SHA-1 file hashes to re-embed only changed sections; repeated every 12 h via scheduler

### Company research
**Files:** `agentic_jobs/services/research/`

```
domains.py  → safe URL allowlist, blocked domain list, URL builder
              extract_company_website(html, job_url) → str | None
                tries in order:
                  1. LD+JSON hiringOrganization.url / sameAs
                  2. <meta property="og:url">
                  3. <link rel="canonical">
                  4. external link scan (ATS pages only; skips social/ATS/aggregator)
                  5. subdomain stripping (company-hosted pages; TLD already known)
                never constructs a URL from the company name — no TLD guessing
scraper.py  → CompanyScraper with 10 safety layers (see below)
cache.py    → CompanyResearchCache: DB (runtime) + Obsidian markdown (human browsing)
              write_no_domain_note() → writes a stub vault note when no domain resolves
```

**Scraped data is persisted in two places:**
1. `company_cache` table — used at runtime (7-day TTL, keyed by domain)
2. `{VAULT_PATH}/Agentic Copilot/Company Research/{CompanyName}.md` — human-readable Obsidian copy

**When no company domain can be resolved** (e.g. job sourced from a pure ATS like Greenhouse with no embedded company URL), a stub note is written to the vault instead of silently skipping. The stub records that research was attempted and instructs how to trigger it again once the company website is known.

**Scraper safety layers (in order):**
1. HTTPS-only
2. URL path allowlist (`/about`, `/careers`, `/products`, etc.)
3. Hard domain blocklist (social networks, login pages, etc.)
4. Blocked path segments (`/login`, `/api`, `/admin`, `/checkout`, etc.)
5. Robots.txt compliance (per-domain, cached 1 h)
6. Per-domain rate limiter (`SCRAPER_RATE_LIMIT` req/10 s)
7. Global concurrency cap (semaphore: 4 simultaneous requests)
8. Hard request timeout (`SCRAPER_TIMEOUT_SECONDS`)
9. Max body size (500 KB)
10. Content-type guard (only processes `text/html`)

### Memory system
**Files:** `agentic_jobs/services/memory/`

Two tiers stored in `agent_memories`:

| Tier | Scope | TTL | Source |
|------|-------|-----|--------|
| **Short-term** | Per `application_id` | 7 days | Pipeline runs |
| **Long-term** | Cross-application (`application_id = NULL`) | None | `!remember`, auto-assess, pipeline |

**`!remember` command**: posting `!remember <note>` in any draft Slack thread saves directly to long-term memory (`source=user_explicit`). Confirmed with `:brain: Remembered: <note>`.

**Auto-assess** (every `MEMORY_ASSESSMENT_INTERVAL_DAYS` days via scheduler):
1. Load all `ApplicationFeedback(role=USER)` since last assessment
2. Filter noise (< 15 chars, common ack phrases)
3. Truncate each note to 200 chars, deduplicate
4. Batch to LLM: "Extract reusable long-term learnings about tone/style/content preferences"
5. Save extracted learnings as `AgentMemory(type=LONG_TERM, source=auto_assessed)`

Last assessment timestamp is derived from the most recent `source=auto_assessed` row — no extra table needed.

### Guardrails
**File:** `agentic_jobs/services/agents/guardrails.py`

Prompt injection detection applied to all scraped web content and user notes before any LLM call. Patterns detected:
- `ignore previous/prior instructions`
- `you are now a`
- `disregard your instructions`
- `system prompt`
- `<system>`, `<user>`, `<assistant>` XML tags

Suspicious content is stripped with a warning log before it reaches any LLM.

---

## New database tables

| Table | Purpose |
|-------|---------|
| `pipeline_runs` | One row per pipeline execution — status, agent log (JSONB), final score, revision count |
| `agent_memories` | Short-term and long-term memory; `NULL application_id` = global/long-term |
| `vault_embeddings` | One row per vault section — embedding vector, file hash for staleness detection |
| `company_cache` | Scraped company data keyed by domain, 7-day TTL |

---

## Configuration

```bash
# Vault
VAULT_PATH=                          # path to Obsidian folder (optional)
EMBEDDING_MODEL_NAME=nomic-embed-text-v1.5
EMBEDDING_ENDPOINT_URL=http://localhost:1234/v1/embeddings
VAULT_LINK_DEPTH=1                   # wikilink expansion hops
VAULT_TOP_K=5                        # top-k semantic matches

# Pipeline
PIPELINE_PASS_THRESHOLD=7.0          # HiringManager score threshold
PIPELINE_MAX_REVISIONS=2             # max write-review cycles

# Memory
MEMORY_ASSESSMENT_INTERVAL_DAYS=3

# Company scraper
SCRAPER_RATE_LIMIT=5                 # requests per 10 s per domain
SCRAPER_TIMEOUT_SECONDS=10
COMPANY_CACHE_TTL_HOURS=168          # 7 days
```

---

## Latency estimates (llama 3.1 8B, local hardware)

| Phase | Time |
|-------|------|
| Data gathering (scrape + vault) | ~5–15 s |
| ResearcherAgent LLM call | ~10–15 s |
| WriterAgent LLM call | ~10–15 s |
| HiringManagerAgent LLM call | ~8–12 s |
| **Best case (pass round 1)** | **~40–55 s** |
| **Typical (1 revision)** | **~65–85 s** |
| **Worst case (2 revisions)** | **~90–115 s** |

Progress messages are posted to the Slack thread at each phase.

---

## File map

```
agentic_jobs/
└── services/
    ├── agents/
    │   ├── base.py          BaseAgent ABC, call_llm() wrapper
    │   ├── schemas.py       ResearchBrief, CoverLetterDraft, ReviewVerdict, PipelineResult
    │   ├── researcher.py    ResearcherAgent
    │   ├── writer.py        WriterAgent + compute_word_budget()
    │   ├── reviewer.py      HiringManagerAgent
    │   ├── coordinator.py   PipelineCoordinator
    │   └── guardrails.py    Prompt injection detection
    ├── vault/
    │   ├── parser.py        VaultParser, VaultSection
    │   ├── graph.py         WikilinkGraph
    │   ├── embedder.py      VaultEmbedder
    │   └── retriever.py     VaultRetriever (cosine sim + graph expansion)
    ├── research/
    │   ├── domains.py       Safe URL allowlist, URL builder, extract_company_website()
    │   ├── scraper.py       CompanyScraper (10 safety layers)
    │   └── cache.py         CompanyResearchCache (DB + Obsidian dual write + no-domain stub)
    └── memory/
        └── store.py         MemoryStore (short/long-term, auto-assess)
```
