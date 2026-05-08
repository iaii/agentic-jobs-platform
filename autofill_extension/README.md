# Agentic Autofill Companion (Chrome Extension)

Browser extension for the **Agentic Job Search Copilot** — bridges the backend autofill API with ATS job application pages in Chrome.

---

## How it works

1. The backend finalizes a cover letter and queues an `AutofillTask`.
2. The backend opens the job URL appended with `#ajp_autofill=APP-YYYY-NNN` in Chrome.
3. The content script detects the fragment, fetches the payload from the local API, and fills the form.
4. Status updates (`in_progress` / `ready` / `blocked` / `failed`) are posted back to the API, which propagates them to Slack.

---

## Features

### Multi-frame support
The extension injects into the top-level frame and all matching iframes (`all_frames: true`). On ATS sites that embed the application form inside an iframe (e.g. Greenhouse embed), the extension detects and fills fields in the correct frame. Frames with no form fields are skipped before making any LLM call.

### SPA navigation
When the backend opens a job description page (not the application form directly), the background service worker listens for `onHistoryStateUpdated` and `onCompleted` events. After the user navigates to the application form, autofill triggers automatically — no re-triggering required.

### Multi-step forms
After filling step 1, a `MutationObserver` watches for new form fields appearing in the DOM (in-place DOM swap between steps). When new fields are detected, the extension fills them automatically, tracking which selectors have already been processed to avoid re-filling.

### Intelligent label resolution
Field labels are resolved through a cascade:
1. `aria-label`
2. `aria-labelledby`
3. `<label for="id">` (nested form elements stripped)
4. Parent `<label>` (direct text nodes preferred to avoid concatenating option text)
5. `placeholder`
6. `previousElementSibling`
7. **Ancestor question walk** — if none of the above yield a meaningful label, the extension walks up the DOM tree to find the question heading, stopping at semantic boundaries or when an ancestor contains more than 3 form fields (past question scope)

### Named checkbox grouping
Many ATS forms (Lever, some SmartRecruiters) render exclusive Yes/No questions as `<input type="checkbox" name="...">` pairs instead of radio buttons. The extension detects these by grouping checkboxes that share a `name` attribute, resolves the question text from their ancestor, and presents them to the LLM as a single radio-style field. The correct checkbox is clicked based on the LLM's answer.

### ARIA radio groups
Custom Yes/No pickers and styled option groups that use `[role="radiogroup"]` containers with `[role="radio"]` / `[role="button"]` children are detected and filled by clicking the matching option.

### Cover letter pasting
Textareas whose label matches cover letter patterns (`"cover letter"`, `"comments"`, `"additional information"`, `"why are you interested"`, etc.) are filled verbatim from the finalized cover letter artifact — bypassing the LLM entirely. Cover letter fields are highlighted in **purple** to distinguish them from LLM-filled fields (green).

### Work experience from resume
When the form contains experience description fields (`"describe your responsibilities"`, `"what did you do"`, etc.), the backend loads `resume_text_path` from the profile and includes it in the LLM prompt. The LLM quotes from the resume verbatim — it does not invent content.

### React / Angular form filling
Values are set using the native prototype setter (`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set`) so React and Angular synthetic event systems see the change and update their internal state. `input`, `change`, and `blur` events are dispatched after each fill.

### Fuzzy select matching
`<select>` fields are matched with a two-pass algorithm:
1. Exact / prefix / substring match
2. Word-overlap fallback (≥60% of answer words must appear in the option) for profile values that differ slightly from option text (e.g. "I am not a protected veteran" → "I am not a veteran")

### Deterministic relocation logic
The backend computes `willing_to_relocate` before the LLM call by comparing the candidate's `base_location` against the job's location using a metro-area lookup table covering 130+ cities. The LLM reads the pre-computed answer — it does not reason about geography.

### Context-aware profile loading
The `/answer` endpoint uses a tiered profile strategy:
- **Basic forms** (name, email, EEO, links): sends only `identity`, `links`, `compliance`, `quick_answers` — keeps the prompt small for local 8B models
- **Experience forms** (describe responsibilities, etc.): also loads the plain-text resume (`resume_text_path`)

Field labels are capped at 60 chars in the backend before the LLM call, regardless of how much context the extension added.

---

## ATS support

| ATS | Standard fields | Yes/No questions | EEO dropdowns | Notes |
|---|---|---|---|---|
| **Greenhouse** | ✓ | ✓ (radio) | ✓ | fieldset/legend supported |
| **Lever** | ✓ | ✓ (named checkbox groups) | ✓ | |
| **Ashby** | ✓ | ✓ | ✓ | aria-labelledby |
| **SmartRecruiters** | ✓ | ✓ | ✓ | |
| **iCIMS** | ✓ | ✓ | ✓ | |
| **Workday** | Partial | ✗ | ✗ | custom web components — planned |

File inputs use **Assisted Upload** — the extension highlights the field with an orange dashed border and shows the suggested file path as a tooltip. **You must attach files manually.** Browsers permanently block content scripts from setting `input[type=file].value` as a security measure.

---

## Setup

### 1. Profile configuration

Copy the template and fill in your details:

```bash
cp config/profile.yaml.example config/profile.yaml
```

Edit `config/profile.yaml` with your real identity, links, compliance answers, and paths.

Add a plain-text copy of your resume at `artifacts/profile/resume/resume.md` — the LLM uses this verbatim for work experience description fields.

`config/profile.yaml` is gitignored — never commit real PII.

### 2. Backend

```bash
source env_local.sh
./start_server.sh
```

Required env vars: `AUTOFILL_ENABLED=true`, `AUTOFILL_API_TOKEN=<your-token>`.

### 3. Load the extension

1. Visit `chrome://extensions`.
2. Enable **Developer Mode**.
3. Click **Load unpacked** and select the `autofill_extension/` folder.

### 4. Configure the extension

Open the extension's **Options** page:
- **API base URL**: `http://127.0.0.1:8000/api/v1/autofill` (default)
- **API token**: value of `AUTOFILL_API_TOKEN` from your environment

---

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/autofill/payload/{human_id}` | Fetch application payload (profile, files, mode, CL text) |
| `POST` | `/api/v1/autofill/status` | Report fill progress |
| `POST` | `/api/v1/autofill/answer` | LLM answers form fields from profile + resume |

All requests require `X-Autofill-Token` header.

---

## Banner status messages

| Banner | Meaning |
|---|---|
| `AJP Autofill scanning fields for X …` | Page loaded, looking for fields |
| `Waiting for form to load …` | No fields yet — watching for SPA to render the form |
| `Found N fields — asking LLM …` | Fields detected, LLM call in progress |
| `Autofill ready — review and submit` | Fields filled successfully |
| `Autofill: 0 fields filled — LLM skipped all fields` | LLM couldn't match any field; check profile data |
| `Autofill blocked — invalid token (check Options page)` | Token mismatch |
| `Autofill blocked — LLM backend unavailable` | LM Studio / LLM endpoint not running |
| `Autofill blocked — no fields found` | Page has no detectable form fields after 12s |

---

## Security

- Extension only communicates with `127.0.0.1` — no remote network access.
- Host permissions are limited to whitelisted ATS domains.
- All LLM outputs are applied via `element.value` — never `innerHTML`.
- Compliance/EEO fields are answered only from the explicit `compliance` section in `profile.yaml`.
- The submit button is never clicked — the user reviews and submits manually.
