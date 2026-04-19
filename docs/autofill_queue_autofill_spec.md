# Autofill Pipeline — Original Specification (Reference)

> **Status: superseded.** This was the initial design document. The implemented architecture is described in [`autofill_queue_autofill_spec_consolidated.md`](autofill_queue_autofill_spec_consolidated.md).

---

## Objectives

- Queue applications after cover letter finalisation and autofill them on trusted ATS pages.
- Open apply pages in the default browser, pre-fill core fields, upload resume/cover letter, and stop one click before submission for user review.
- Provide progress visibility and error notifications in a dedicated Slack channel.
- Enforce strict security boundaries: only whitelisted domains, least-privilege extension, local-only communication.

---

## System overview

| Component | Role |
|-----------|------|
| **Orchestrator** (Python, local) | Coordinates tasks, opens tabs, brokers data to the extension, records artifacts and status |
| **Browser Extension** (Chrome/Edge) | Injects content scripts on whitelisted ATS domains; maps form fields; communicates with Orchestrator over localhost |
| **Slack Integration** | `AUTOFILL_OPS_CHANNEL` for control and alerts; per-application thread updates |
| **Data Layer** | `Application`, `Artifact`, `ProfileIdentity` models; `autofill_tasks` table for durable tracking |
| **Trust Layer** | Domain whitelist (`services/trust/whitelist.py`) constrains where autofill is allowed |

---

## Profile and resume inputs

**Profile source order:**
1. DB profile (`ProfileIdentity` + `ProfileLinks` + `ProfileFacts` + `ProfileFiles`)
2. Fallback: `config/fake_profile.yaml` (never commit with real PII)

**Resume/cover letter files:**
- Resume: `artifacts/profile/resume/latest.pdf` (or any path defined in `ProfileFiles.resume_variants`)
- Cover letter: rendered to PDF at `artifacts/{human_id}/cover-letter/latest.pdf` on queue; pasted as text where ATS supports it
- Multiple resumes: select variant by tag (`default`, `backend`, `ml`); Orchestrator picks `default` unless overridden

**File upload modes:**
- **Assisted Upload** (default): extension highlights upload field and shows suggested file path; user selects manually
- **Automation Mode** (opt-in): Orchestrator launches a controlled Chromium via Playwright/CDP for strict file upload automation; off by default

---

## Core flows

### 1. Queue
- User finalises cover letter → `ApplicationStage.COVER_LETTER_FINALIZED`
- Slack Manage modal shows **Queue for Autofill**
- Orchestrator creates `AutofillTask(status=QUEUED)`, writes `AUTOFILL_SUMMARY`

### 2. Execute
- Orchestrator opens tabs via `webbrowser.open()` (max `AUTOFILL_MAX_CONCURRENCY` at once)
- Extension content script detects form, requests payload via localhost, fills fields, navigates Next/Continue steps, stops before final submit
- Extension posts completion or error event with structured summary

### 3. Track and notify
- Orchestrator persists `AUTOFILL_SUMMARY` artifact
- Posts thread update in application Slack thread + aggregates to `AUTOFILL_OPS_CHANNEL`
- User manually reviews and submits; clicks **Mark Submitted** to update stage

---

## ATS strategy

| ATS | Approach |
|-----|---------|
| **Greenhouse** | Stable `name`/`id` attributes; direct inputs; standard upload widgets |
| **Lever** | Predictable inputs; upload by label; LinkedIn/GitHub link mapping |
| **Workday** | Gated by account creation; detect login gate → fill signup → pause for verification; multi-page Next navigation; stop before submit |
| **Generic** | Label-based matching on whitelisted domains; conservative — fill obvious fields only |

---

## Security and privacy

- Domain gating via `services/trust/whitelist.py` — only whitelisted domains receive payloads
- Extension communicates only with `127.0.0.1`; no remote network requests
- Sensitive field values (EEO, DoB) redacted from all logs and Slack; record labels/sections only
- Credentials for account creation stored in OS Keychain by user; Orchestrator never persists them in the database
- Assisted Upload is default — no programmatic file-input assignment
- Final submit button is never clicked automatically

---

## Configuration

```bash
AUTOFILL_ENABLED=false
AUTOFILL_WS_PORT=8765
AUTOFILL_MAX_CONCURRENCY=3
AUTOFILL_OPS_CHANNEL=C...
AUTOFILL_ALLOWED_DOMAINS=          # comma-separated; falls back to trust whitelist
AUTOFILL_ALLOW_ACCOUNT_CREATION=false
AUTOFILL_ASSISTED_UPLOAD=true
AUTOFILL_AUTOMATION_MODE=false     # Playwright/CDP for strict file uploads
AUTOFILL_CL_PDF_ENABLED=true
```

---

## Phase plan

### Phase 1 (implemented)
Greenhouse + Workday contact fields, queueing from Slack, background tab opening, core identity fill, Assisted Upload, `AUTOFILL_SUMMARY` artifact, Ops channel notifications.

### Phase 2
Durable `autofill_tasks` table, resume/retry logic, structured error reporting, Workday login/signup, Automation Mode for strict uploads, payload/status REST API for extension.

### Phase 3+
Additional ATS (Ashby, iCIMS, SmartRecruiters), EEO opt-in, bulk "Run all queued" action, selector-hash breakage tracking, multi-browser parity.
