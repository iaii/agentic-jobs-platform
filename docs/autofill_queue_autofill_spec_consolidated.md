# Autofill Pipeline — Consolidated Design

Part of the **Agentic Job Search Copilot**. This document describes the implemented Queue + Autofill architecture: the Python orchestrator, Chrome extension, local API bridge, and Slack controls.

---

## Objectives

- Queue and run autofill tasks from Slack with clear per-application controls.
- Open apply pages in the background and pre-fill identity fields; never auto-submit.
- Phase 1: identity + compliance selections (EEO/veteran), dates, signature.
- Phase 2: document uploads and multi-page navigation.

---

## Architecture

### Components

**Orchestrator** (`agentic_jobs/services/autofill/orchestrator.py`)
- Builds per-application `AUTOFILL_SUMMARY` JSON (identity, links, file paths, mode).
- Validates domain against `AUTOFILL_ALLOWED_DOMAINS` and the trust whitelist before sending any data.
- Creates `AutofillTask` rows and manages the task state machine.
- Opens ATS tabs with the `#ajp_autofill=<HUMAN_ID>` fragment (background on macOS via `open -g`).
- Posts progress updates to the application's Slack thread and to `AUTOFILL_OPS_CHANNEL`.

**Browser extension** (`autofill_extension/`)
- MV3 content script detects the `#ajp_autofill` fragment.
- Fetches payload via `GET /api/v1/autofill/payload/{human_id}`.
- Fills form fields; calls `POST /api/v1/autofill/answer` for LLM-assisted field matching.
- Reports status (`in_progress` / `ready` / `blocked` / `failed`) via `POST /api/v1/autofill/status`.
- Respects `mode` field: `autofill` fills and reports; `open_tabs` shows a banner only.

**Local HTTP API** (`agentic_jobs/api/v1/autofill.py`)
- `GET /api/v1/autofill/payload/{human_id}` — returns application payload (guarded by `X-Autofill-Token`).
- `POST /api/v1/autofill/status` — receives status updates from extension; transitions `AutofillTask`, notifies Slack.
- `POST /api/v1/autofill/answer` — LLM answers specific form fields from profile data.
- All endpoints are active only when `AUTOFILL_ENABLED=true`.

---

## Slack controls

| Button | Where | Behaviour |
|--------|-------|-----------|
| **Queue Application** | Manage modal | Creates `AutofillTask(status=QUEUED)`, writes summary. Does not open tabs. |
| **Autofill Application** | Manage modal | Queues (if needed) then starts immediately. Opens the apply tab. |
| **Run N queued** | Tracker header | Starts all tasks currently in `QUEUED` status. |

---

## Task state machine

```
QUEUED
  │  (user clicks "Autofill Application" or auto-start)
  ▼
IN_PROGRESS
  │
  ├──▶  READY    (form filled, awaiting manual submit)
  ├──▶  BLOCKED  (CAPTCHA / unsupported field — user action needed)
  ├──▶  FAILED   (error during fill or network issue)
  └──▶  SKIPPED  (domain not allowed / profile missing)
```

---

## Phase 1 — Identity, compliance, dates, signature

- **Identity**: first/last name, email, phone, base location; address fields where present.
- **EEO/Veteran/Disability**: radio/checkbox/selection by label text; default to "Prefer not to say" unless `config/fake_profile.yaml` or DB profile opts in.
- **Dates of employment**: day/month/year combos and single text inputs; ISO `YYYY-MM-DD` fallback.
- **Signature**: fill full name into fields labelled "Signature" or "Type your full name".
- **Never auto-submit**: stop before the final submit button.
- **Slack updates**: post `in_progress` on start; `ready` or `blocked` with reason on completion.
- **Sites**: Greenhouse and Workday (contact fields); Lever in progress.

## Phase 2 — Documents and navigation

- **Assisted Upload** (default): highlight file inputs and display the suggested file path.
- **Cover letter text**: paste finalized markdown into text areas where ATS supports it.
- **Next/Continue**: detect and click navigation buttons conservatively; wait for DOM stabilisation; stop one click before final submit.
- **Status detail**: surface missed fields by label/section name only — no PII in Slack.
- **Concurrency**: `AUTOFILL_MAX_CONCURRENCY` (default 3) controls parallel tasks.

## Later phases

- Workday login / profile creation with OS keychain credentials; pause for OTP/CAPTCHA.
- Additional ATS: Ashby, iCIMS, SmartRecruiters.
- `selector_hash` tracking to group and diagnose form structure changes.
- Safari/Firefox parity.

---

## Field-mapping heuristics

- Match by label text (case/space-insensitive) and associated input via `for` / `aria-labelledby` / DOM proximity.
- Radio/checkbox selection by normalised label text; fire `change` / `input` / `blur` events.
- Date controls: try single text inputs first; fall back to day/month/year triplets.
- Signature: locate text inputs with labels matching "Signature" or "Type your full name".

**Per-ATS starting points:**
- **Greenhouse**: existing identity selectors; EEO sections via label text; LinkedIn/GitHub link fields.
- **Lever**: similar to Greenhouse; slightly different attribute names.
- **Workday**: contact/address fields; skip login/profile creation until Phase 2.

---

## Security and privacy

- Only operate on domains in `AUTOFILL_ALLOWED_DOMAINS` or the trust whitelist.
- Sensitive values are redacted from all logs and Slack messages; only field labels/section names are surfaced.
- Assisted Upload is the default — no direct file-input assignment from content scripts.
- Final submit button is never clicked automatically.
- Extension communicates only with `127.0.0.1`; no remote network access.

---

## Configuration

```bash
AUTOFILL_ENABLED=true
AUTOFILL_WS_PORT=8765
AUTOFILL_MAX_CONCURRENCY=3
AUTOFILL_OPS_CHANNEL=C...          # Slack channel ID for ops updates
AUTOFILL_ALLOWED_DOMAINS=boards.greenhouse.io,jobs.lever.co
AUTOFILL_ASSISTED_UPLOAD=true
AUTOFILL_CL_PDF_ENABLED=true       # render cover letter to PDF on queue
AUTOFILL_FAKE_PROFILE_PATH=config/fake_profile.yaml
AUTOFILL_API_TOKEN=                # shared secret for extension API calls (optional)
```
