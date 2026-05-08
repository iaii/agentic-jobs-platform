# MVPart5 — LLM Cover Letter Kit Design

Goal: define how the cover-letter generator always uses Apoorva's preferred voice, projects, and structure **before** wiring up any new Slack actions. This document explains the artifacts, data structures, and touch points we need so every prompt consistently injects the provided “LLM cover letter kit.”

---

## 1. Requirements Recap

* The model runs locally (Llama 3.1 8B Instruct via Ollama/vLLM).
* Every generation must embed the provided kit (profile snapshot, project blurbs, tone/style guardrails, template, tailoring checklist, do/don’t list).
* No em dashes or semicolons in the output.
* Cover letters default to 3–5 short paragraphs (unless future overrides).
* Slot-based output contract from `CODEx_INSTRUCTIONS.md` stays authoritative.

---

## 2. Artifact & Storage Plan

| Artifact | Purpose | Format | Path |
| --- | --- | --- | --- |
| `cover_letter_kit.yaml` | Canonical kit shared with the model | YAML → Pydantic dataclasses | `agentic_jobs/profile/cover_letter_kit.yaml` |
| `style_kit.py` | Loader + typed objects the rest of the app can consume | Python module | `agentic_jobs/services/llm/style_kit.py` |
| `prompt_builder.py` | Transforms job/profile data + kit into JSON payload expected by MVPart5 | Python module | `agentic_jobs/services/llm/prompt_builder.py` |

### YAML schema overview

```yaml
profile:
  bio: "Soon-to-be new grad..."
  technical_strengths:
    languages: [...]
    frontend: [...]
    backend: [...]
    data_infra: [...]
    ml_adjacent: [...]
  work_style: [...]
projects:
  - key: fashion_app
    name: "my fashion app"
    short_name: "my fashion app"
    summary: "..."
    talking_points: [...]
  - key: fitness_app
    ...
tone:
  overall: [...]
  voice: [...]
  dislikes: [...]
structure:
  greeting: "Dear Hiring Manager,"
  paragraphs: [...]
tailoring_checklist:
  - "Identify 1–2 concrete product areas..."
dos:
  - "Reference only known projects"
donts:
  - "No em dashes"
```

*YAML holds only user-specific facts & rules. Runtime metadata (e.g., target company) gets injected later by `prompt_builder` so we do not duplicate job data.*

---

## 3. Loader + Typed Access

`agentic_jobs/services/llm/style_kit.py` responsibilities:

1. Define `@dataclass`/`pydantic` models that mirror the YAML schema (`ProfileSnapshot`, `ProjectCard`, `ToneRules`, `StructureTemplate`, etc.).
2. Provide `load_cover_letter_kit(path: Path | None = None) -> CoverLetterKit` that:
   * Reads YAML once at startup (cache in module-level variable).
   * Validates schema and raises a descriptive error if required sections are missing.
   * Normalizes formatting constraints (e.g., enforce lowercase for `projects[*].key`).
3. Expose convenience helpers used by prompt assembly, e.g.:
   * `get_project_by_theme(theme: Literal["visual","health","automation"])`.
   * `render_template_sections()` returning the default section order.

This module stays pure (no DB, no Slack) so it can be unit-tested easily.

---

## 4. Prompt Assembly Plan

`agentic_jobs/services/llm/prompt_builder.py` orchestrates runtime inputs:

```
Job metadata (DB)
└── title, company, location, url, jd_text, requirements
Application metadata (DB)
└── app_id, canonical id, slack thread info
Profile tables (DB)
└── identity, skills, links, facts, files
Cover letter kit (YAML)
└── voice/style/template/tailoring
Slot hints per JD (new module) 
└── extracted keywords, tone sample, summary
```

### Steps

1. **JD summarization** (TBD heuristics for MVP). Provide:
   * `summary` (2–3 sentences)
   * `bullets` (key requirements/responsibilities)
   * `phrases` for mirroring 5–10% of language
   * `tone_sample` (short snippet) — optional now but keep field for parity with CODEx spec.

2. **Slot targeting**
   * Determine best project overlap using kit mapping (`fashion_app` ↔ consumer/visual, etc.).
   * Map JD keywords to kit-defined `role_alignment_targets`.
   * Fill `impact_picks`, `plan_hints`, and `stack_focus` based on job vs skills.

3. **Style card injection**
   * Compose from kit tone lists (`overall`, `voice`, `likes`, `dislikes`).
   * Append system rules (“short sentences”, “no em dashes”, “no semicolons”, “active voice”).

4. **Payload rendering**
   * Follow JSON contract from instructions exactly.
   * Add `kit_version` (hash of YAML) inside payload for traceability.
   * Return structured object ready for serialization + logging (no JD text in logs — log only IDs).

5. **Validation**
   * Unit test ensures sample job/application results in deterministic payload (fixture).

---

## 5. LLM Runner Abstraction

While Slack integration waits, we still define a lightweight runner:

| Component | Responsibility |
| --- | --- |
| `agentic_jobs/services/llm/runner.py` | Provide `generate_cover_letter(payload: dict, *, seed: Optional[int]) -> LlmResponse` |
| Implementation | Shells out to `ollama run` or hits local HTTP endpoint. Allows swapping in vLLM later by changing settings. |
| Safety | Enforce timeout, capture stderr/stdout, convert to `LlmResponse`. Validate JSON via Pydantic before returning. |

Settings additions:

```
LLM_BACKEND=ollama|http
LLM_MODEL_NAME=llama3.1:8b-instruct
LLM_ENDPOINT_URL=http://localhost:8001/generate  # optional
LLM_TIMEOUT_SECONDS=60
```

---

## 6. API Touch Points (prep for later)

* `/drafts/create`
  1. Fetch Application + Job (+ Profile identity facts).
  2. Call `prompt_builder.build(application_id)` to get payload.
  3. Call `llm.runner.generate(payload)`.
  4. Persist artifact (`type=cover_letter_v1`), with `uri` pointing to local storage (e.g., `artifacts/APP-2025-001/cl-v1.md`).
  5. Return JSON + Slack event (future step).

* `/drafts/feedback`
  * Accept structured slot overrides (e.g., `{"why_company": "Mention the X app"}`).
  * Merge overrides before re-running prompt builder.

These endpoints can be mocked now to verify payload shapes without hitting Slack.

---

## 7. Testing Strategy

1. `tests/llm/test_style_kit.py`
   * Load YAML fixture, assert normalization, no em dashes present, etc.
2. `tests/llm/test_prompt_builder.py`
   * Use fake Job/Application/Profile rows.
   * Validate payload matches schema and includes correct project selection and style card.
3. `tests/llm/test_runner.py`
   * Stub runner (e.g., echo server) to confirm JSON parsing and timeout handling.

All tests run without network by injecting fake runner functions.

---

## 8. Next Steps Checklist

1. Add YAML + loader module + tests.
2. Implement prompt builder w/ heuristics for slot targeting.
3. Add runner abstraction (local stub first).
4. Wire `/drafts/create` to call prompt builder + runner, persist artifact.
5. After payload + LLM validated, hook Slack thread + `#jobs-drafts` UX.

This keeps work sequenced so the LLM voice is dialed in before UI wiring.

---

## 9. Current Implementation Snapshot

* ✅ YAML kit lives at `agentic_jobs/profile/cover_letter_kit.yaml` and loads through `load_cover_letter_kit`.
* ✅ Prompt payloads compiled via `agentic_jobs/services/llm/prompt_builder.build_prompt_payload`.
* ✅ Runner stub (`LLM_BACKEND=mock`) produces deterministic drafts until the local model endpoint is connected.
* ✅ Slack threads now ship with lightweight `Generate draft` and `Finalize draft` buttons; you drop feedback directly in the thread instead of opening a modal.
* ✅ Any human message inside a draft thread is stored as feedback, and generation buttons reuse that history so the LLM mirrors the entire conversation.
* ✅ LLM runner can call Qwen (`LLM_BACKEND=qwen`, DashScope endpoint) or Ollama Cloud (`LLM_BACKEND=ollama`, OpenAI-compatible endpoint) just by updating `.env` with `LLM_ENDPOINT_URL`, `LLM_MODEL_NAME`, and `LLM_API_KEY` (or `OLLAMA_API_KEY`).
* ✅ Style kit now embeds resume-driven context (education, skills card, experience highlights, tone/structure guidance) so the prompt always reflects the latest profile without copy/paste.
* ✅ Finalizing a draft records a learning note so future prompts inherit preferences.
