# Agentic Autofill Companion (Chrome Extension)

Browser extension for the **Agentic Job Search Copilot** — bridges the backend autofill API with ATS job application pages in Chrome.

---

## How it works

1. The backend finalizes a cover letter and queues an `AutofillTask`.
2. The Copilot backend opens the job URL appended with `#ajp_autofill=APP-YYYY-NNN`.
3. The content script detects this fragment, fetches the payload from the local API, and fills the form.
4. Status updates (`in_progress` / `ready` / `blocked` / `failed`) are posted back to the API, which propagates them to Slack.

---

## Current ATS support

| ATS | Fields filled |
|-----|--------------|
| **Greenhouse** | First/last name, email, phone, base location, LinkedIn URL, GitHub URL; resume input highlighted for manual upload |
| **Workday** | First/last name, email, phone, city, postal code; file inputs highlighted for manual upload |
| Others | Add per-ATS logic by extending `content.js` |

All file inputs use **Assisted Upload** by default — the extension highlights the field and shows the suggested file path rather than programmatically assigning it (browsers block direct file-input assignment from content scripts).

---

## Setup

### 1. Ensure the backend is running

```bash
source env_local.sh
./start_server.sh
```

`AUTOFILL_ENABLED=true` must be set in your environment.

### 2. Load the extension

1. Visit `chrome://extensions`.
2. Enable **Developer Mode** (top right).
3. Click **Load unpacked** and select the `autofill_extension/` folder from the repo.

### 3. Configure the extension

Open the extension's **Options** page and set:

- **API base URL**: `http://127.0.0.1:8000/api/v1/autofill` (default)
- **API token**: value of `AUTOFILL_API_TOKEN` from your environment (leave blank if unset)

---

## API endpoints used

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/v1/autofill/payload/{human_id}` | Fetch application payload (profile, files, mode) |
| `POST` | `/api/v1/autofill/status` | Report fill progress and final URL |
| `POST` | `/api/v1/autofill/answer` | Ask the LLM to answer specific form fields from profile data |

All requests require the `X-Autofill-Token` header when `AUTOFILL_API_TOKEN` is set.

---

## Payload `mode` field

| Mode | Behaviour |
|------|-----------|
| `autofill` | Fill all detected fields, post status updates |
| `open_tabs` | Display info banner only; do not fill |

---

## Security model

- Extension only communicates with `127.0.0.1` — no remote network access.
- Host permissions are limited to whitelisted ATS domains (Greenhouse, Lever, Workday, Ashby, SmartRecruiters, iCIMS).
- The `/autofill/answer` LLM call uses only provided profile data; the system prompt explicitly forbids inventing values.
- Sensitive compliance fields (EEO, veteran status) are never autofilled unless the profile explicitly opts in.
- Final submit button is never clicked — the user reviews and submits manually.

---

## Extending to new ATS

Add a new handler block in `content.js` that:
1. Detects the ATS (by hostname or DOM marker).
2. Maps profile fields to the correct input selectors.
3. Dispatches `input`, `change`, and `blur` events after filling (required for React/Angular forms).

React/Angular-compatible fill helper is already in `content.js` — use `fillField(input, value)`.
