# LLM Cover Letter Kit — Design & Implementation

Part of the **Agentic Job Search Copilot**. This document describes the profile kit system that ensures every generated cover letter uses the correct voice, projects, and structure.

---

## Overview

The cover-letter kit is a YAML file (`agentic_jobs/profile/cover_letter_kit.yaml`) that acts as the single source of truth for all personalisation. The prompt builder reads it at runtime and injects the relevant sections into every LLM call, so the model always knows the candidate's voice, preferred projects, tone rules, and structural preferences.

---

## Artifacts

| File | Purpose |
|------|---------|
| `agentic_jobs/profile/cover_letter_kit.yaml` | Canonical kit — profile, projects, tone, structure, dos/don'ts |
| `agentic_jobs/services/llm/style_kit.py` | Typed loader (`CoverLetterKit`, `ProjectCard`, `ToneRules`, etc.) |
| `agentic_jobs/services/llm/prompt_builder.py` | Assembles runtime job/profile data + kit into the LLM prompt payload |

---

## YAML schema

```yaml
profile:
  bio: "…"
  background: […]
  technical_strengths:
    languages: […]
    frontend:  […]
    backend:   […]
    data_infra: […]
    ai_llm:    […]
  work_style: […]

education: […]

skills:
  languages: […]
  backend:   […]
  frontend:  […]
  data_tools: […]
  ai_llm:    […]
  dev_habits: […]

experience:
  - key: job-key
    title: "…"
    summary: "…"
    bullets: […]
    themes: [visual, automation, health]   # used for theme matching

projects:
  - key: project-key
    name: "…"
    short_name: "…"
    summary: "…"
    talking_points: […]                    # metrics / achievements
    themes: […]                            # application domains

tone:
  overall:  […]   # voice guidelines
  voice:    […]   # specific phrases / attitudes
  dislikes: […]   # what to avoid (e.g. em dashes, semicolons)
  likes:    […]   # preferred phrasing patterns

structure:
  greeting: "Dear Hiring Manager,"
  opener_guidance: "…"
  impact:
    label: "Impact Examples"
    samples: […]
  plan:
    label: "First 30 Days Plan"
    bullets: […]
  stack_guidance: "…"
  close_guidance: "…"
  signoff: "Sincerely,"

tailoring_checklist: […]   # per-JD verification points
dos:   […]
donts: […]
style_examples: […]
reasoning_guidance: […]

learning:
  max_recent_notes: 3       # max AgentMemory notes injected per call
```

The kit holds only user-specific facts and rules. Runtime metadata (company, role, JD text) is injected by `prompt_builder` at call time — job data is never duplicated into the YAML.

---

## Loader (`style_kit.py`)

`load_cover_letter_kit(path)` reads the YAML once at startup, validates the schema via Pydantic models, and caches the result. Convenience helpers:

- `get_project_by_theme(theme)` — returns the `ProjectCard` whose `themes` list matches the given keyword
- `render_template_sections()` — returns the default section order

The module has no database or Slack dependencies and is fully unit-testable in isolation.

---

## Prompt builder (`prompt_builder.py`)

Combines runtime inputs into a structured `DraftContext` payload:

**Inputs:**
```
Job (DB)          → title, company, location, url, jd_text, requirements
Application (DB)  → human_id, stage, slack thread
Profile tables    → identity, links, facts, files
Cover letter kit  → voice, tone, structure, projects
Feedback history  → ApplicationFeedback rows (ordered, all roles)
AgentMemory       → top 3 recent long-term learning notes
```

**Steps:**
1. **Theme matching** — scan JD for domain keywords (health, automation, fintech, …) → select matching `ProjectCard`
2. **Role targets** — grep JD for backend/APIs/SQL/React/Python/etc. → cap at 4; fall back to `STACK_DEFAULTS`
3. **Stack composition** — from profile skills or defaults
4. **Tone/style injection** — compose from `kit.tone` lists + system rules (no em dashes, no semicolons, active voice)
5. **Build `DraftContext`:**
   ```
   role:         {title, company, location, targets[]}
   project_card: {name, short_name, summary, talking_points, themes}
   profile:      {identity, links, skills, stack, projects}
   note:         latest user feedback (if any)
   learning:     top 3 AgentMemory notes
   tone_rules:   kit.tone
   structure:    kit.structure
   ```

---

## LLM runner (`runner.py`)

`generate_cover_letter(payload)` supports multiple backends:

| `LLM_BACKEND` | Endpoint | Notes |
|---------------|----------|-------|
| `lmstudio` / `ollama` | `LLM_ENDPOINT_URL` | OpenAI-compatible `/v1/chat/completions` |
| `qwen` | DashScope API | `LLM_API_KEY` in Bearer header |
| `mock` | In-process stub | Returns deterministic output for tests |

- **Retries**: 3 attempts, exponential backoff on HTTP 429 and 500–504
- **Timeout**: `LLM_TIMEOUT_SECONDS` (default 120 s)
- **User message cap**: `LLM_MAX_USER_MSG_CHARS` (default 12 000) — long JDs truncated before sending
- **Response parsing**: strips markdown code fences if the model wraps JSON

Returns `LlmResponse(version, cover_letter_md, sections_used, provenance)`.

---

## API endpoints

### `POST /drafts/create`
1. Fetch `Application` + `Job` + profile identity.
2. `prompt_builder.build_prompt_payload(application_id)` → payload.
3. `llm.runner.generate(payload)` → `LlmResponse`.
4. Persist artifact (`type=COVER_LETTER_VERSION`) at `artifacts/{human_id}/cl-v{N}.md`.
5. Post draft to Slack thread.

### `POST /drafts/feedback`
1. Accept user note.
2. Store as `ApplicationFeedback(role=USER, text=note)`.
3. Re-run `prompt_builder` with updated feedback history → regenerate.
4. New version persisted as `cl-v{N+1}.md`.

---

## Testing

| Test | What it checks |
|------|---------------|
| `tests/llm/test_style_kit.py` | YAML fixture loads, schema validates, normalization rules applied |
| `tests/llm/test_prompt_builder.py` | Fake Job/Application/Profile rows produce deterministic payload with correct project selection and tone card |
| `tests/llm/test_runner.py` | Stub runner confirms JSON parsing, timeout handling, retry logic |

All tests run without network access via injected mock runner functions.

---

## Implementation status

- YAML kit loads through `load_cover_letter_kit` with full schema validation
- Prompt payloads compiled via `build_prompt_payload` with theme matching, role targets, and memory injection
- LLM backends: `mock` (tests), `lmstudio`/`ollama` (local), `qwen` (DashScope cloud)
- Slack threads ship with **Quick Draft**, **Generate CL**, and **Finalize Draft** buttons
- Human thread messages are stored as feedback and included in the next generation call automatically
- Finalizing a draft triggers DOCX export and long-term memory note extraction
- Style kit embeds education, skills, experience highlights, and tone rules — always reflects the latest profile YAML
