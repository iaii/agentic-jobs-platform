const AUTOFILL_MARKER = "ajp_autofill";

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

let hasRun = false;
let ajpIdCounter = 0;

// Persisted across steps so the step watcher and nav-triggered re-runs can
// reuse the payload without an extra network round-trip.
let currentHumanId = null;
let currentSummary = {};

// Tracks selectors that have already been sent to the LLM so subsequent steps
// only send genuinely new fields.
const processedSelectors = new Set();

// MutationObserver watching for next-step fields after a successful fill.
let stepObserver = null;

// Timer + observer used when the initial page has no fields yet (SPA description
// page — the form hasn't loaded). Cancelled when fields appear or a nav reset arrives.
let noFieldsTimer = null;
let noFieldsObserver = null;

// Maps a synthetic group container selector → its member checkbox descriptors.
// Populated during extractFields; used by applyAnswers to click the right checkbox
// when a named checkbox group is answered as a single radio question.
const checkboxGroupMembers = new Map();

// ---------------------------------------------------------------------------
// Entry point A — top-level frame has the autofill hash fragment.
// Cleans the URL, shows the banner, and asks background to relay the humanId
// to every frame in the tab (including this one and all iframes).
// ---------------------------------------------------------------------------

(function initFromHash() {
  const humanId = extractHumanId();
  if (!humanId) return;
  cleanHash();
  if (window.self === window.top) {
    annotatePage(humanId);
  }
  chrome.runtime.sendMessage({ type: "autofill:broadcast", humanId });
})();

// ---------------------------------------------------------------------------
// Entry point B — background relays autofill:run to every frame.
// reset=true is sent on SPA/full-page navigations; it clears the hasRun guard
// and all per-step state so the new page can be filled from scratch.
// Also handles banner/panel relay messages forwarded by background from iframes.
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "autofill:run") {
    if (message.reset) {
      hasRun = false;
      processedSelectors.clear();
      checkboxGroupMembers.clear();
      if (stepObserver) { stepObserver.disconnect(); stepObserver = null; }
      cancelNoFieldsWait();
    }
    if (hasRun) return false;

    // Skip iframes that have no form fields at all — they will never have
    // anything to fill and would only waste a concurrent LLM call.
    // Top frame always proceeds (it may be waiting for SPA to render the form).
    if (window.self !== window.top) {
      const visibleFields = document.querySelectorAll(
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file])," +
        "textarea, select"
      );
      if (!visibleFields.length) return false;
    }

    hasRun = true;
    currentHumanId = message.humanId;
    fetchPayload(message.humanId);
    return false;
  }
  // Background relays these from iframes so the top-frame banner and review
  // panel reflect what happened inside the embedded form.
  if (message?.type === "autofill:banner_update") {
    updateBanner(message.text, message.color);
    return false;
  }
  if (message?.type === "autofill:show_panel") {
    showReviewPanel(message.filledCount, message.skippedCount);
    return false;
  }
  return false;
});

// ---------------------------------------------------------------------------
// URL utilities
// ---------------------------------------------------------------------------

function extractHumanId() {
  const fragment = window.location.hash || "";
  if (!fragment.includes(AUTOFILL_MARKER)) return null;
  const pairs = fragment.replace(/^#/, "").split("&");
  for (const pair of pairs) {
    const [key, value] = pair.split("=");
    if (key === AUTOFILL_MARKER) return decodeURIComponent(value || "");
  }
  return null;
}

// Bug 3 fix: only strip the ajp_autofill= segment; preserve any other hash
// fragments the ATS uses for its own routing (e.g. #/apply/personal-info).
function cleanHash() {
  const fragment = window.location.hash.replace(/^#/, "");
  const remaining = fragment
    .split("&")
    .filter((pair) => !pair.startsWith(`${AUTOFILL_MARKER}=`))
    .join("&");
  const newHash = remaining ? `#${remaining}` : "";
  history.replaceState(
    null,
    document.title,
    window.location.pathname + window.location.search + newHash,
  );
}

// ---------------------------------------------------------------------------
// Banner (only meaningful in the top frame — iframes run silently)
// ---------------------------------------------------------------------------

function annotatePage(humanId) {
  const banner = document.createElement("div");
  banner.id = "ajp-autofill-banner";
  banner.textContent = `AJP Autofill scanning fields for ${humanId} …`;
  Object.assign(banner.style, {
    position: "fixed",
    top: "12px",
    right: "12px",
    zIndex: 2147483647,
    padding: "8px 12px",
    background: "#111827",
    color: "white",
    borderRadius: "6px",
    fontFamily: "system-ui, sans-serif",
    fontSize: "13px",
    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
  });
  document.body.appendChild(banner);
}

function updateBanner(text, color = "#111827") {
  if (window.self !== window.top) {
    // Banner lives in the top frame — relay the update through background.
    chrome.runtime.sendMessage({ type: "autofill:banner_update", text, color });
    return;
  }
  const banner = document.getElementById("ajp-autofill-banner");
  if (!banner) return;
  banner.textContent = text;
  banner.style.background = color;
}

// ---------------------------------------------------------------------------
// Payload fetch
// ---------------------------------------------------------------------------

function fetchPayload(humanId) {
  chrome.runtime.sendMessage({ type: "autofill:payload", humanId }, (response) => {
    if (!response?.ok) {
      const err = response?.error || "Payload fetch failed";
      console.error("Autofill payload fetch failed", err);
      sendStatus(humanId, "blocked", err);
      const hint = err.includes("401")
        ? "Autofill blocked — invalid token (check Options page)"
        : err.includes("404")
        ? "Autofill blocked — application not found"
        : `Autofill blocked — ${err}`;
      updateBanner(hint, "#b91c1c");
      return;
    }
    const payload = response.data || {};
    const mode = (payload.mode || "autofill").toLowerCase();
    if (mode === "open_tabs") {
      sendStatus(humanId, "ready", "Tabs opened — manual fill enabled.");
      updateBanner("Tabs opened — manual fill", "#0f766e");
      return;
    }
    currentSummary = payload.summary || {};
    runAutofill(currentSummary, humanId);
  });
}

// ---------------------------------------------------------------------------
// Main autofill flow
// ---------------------------------------------------------------------------

function runAutofill(summary, humanId) {
  updateBanner("Scanning form fields …", "#1d4ed8");

  const fields = extractFields();

  if (!fields.length) {
    // Don't immediately report blocked — on SPA description pages the form
    // hasn't loaded yet. Wait up to 12 s for fields to appear; if nothing
    // shows up by then, only then report blocked.
    if (window.self === window.top) {
      waitForFieldsOrBlock(humanId, summary);
    }
    return;
  }

  sendStatus(humanId, "in_progress", "Extracting fields");
  runAutofillFields(fields, humanId, summary);
}

// Waits for form fields to appear on pages that render the form asynchronously
// (e.g. SPA job-description pages where the form loads after clicking Apply).
// Cancelled by cancelNoFieldsWait() when a nav-reset autofill:run arrives or
// when the MutationObserver finds fields first.
function waitForFieldsOrBlock(humanId, summary) {
  updateBanner("Waiting for form to load …", "#1d4ed8");

  noFieldsTimer = setTimeout(() => {
    if (noFieldsObserver) { noFieldsObserver.disconnect(); noFieldsObserver = null; }
    sendStatus(humanId, "blocked", "No form fields found on this page.");
    updateBanner("Autofill blocked — no fields found", "#b45309");
  }, 12000);

  noFieldsObserver = new MutationObserver(() => {
    const fields = extractFields();
    if (!fields.length) return;
    cancelNoFieldsWait();
    sendStatus(humanId, "in_progress", "Extracting fields");
    runAutofillFields(fields, humanId, summary);
  });
  noFieldsObserver.observe(document.body, { childList: true, subtree: true });
}

function cancelNoFieldsWait() {
  clearTimeout(noFieldsTimer);
  noFieldsTimer = null;
  if (noFieldsObserver) { noFieldsObserver.disconnect(); noFieldsObserver = null; }
}

// ---------------------------------------------------------------------------
// Core fill routine — shared by the initial run and subsequent steps.
// Tracks processed selectors so the step watcher only sends new fields.
// ---------------------------------------------------------------------------

function runAutofillFields(fields, humanId, summary) {
  for (const f of fields) processedSelectors.add(f.selector);

  // Cover-letter fields are pasted verbatim — never sent to the LLM.
  const clFields = fields.filter((f) => f.field_type === "cover_letter");
  const llmFields = fields.filter((f) => f.field_type !== "cover_letter");

  let clFilled = 0;
  if (clFields.length && summary.cover_letter_text) {
    clFilled = fillCoverLetterFields(clFields, summary.cover_letter_text);
  }

  if (!llmFields.length) {
    markFileInputs(summary);
    const total = clFilled;
    sendStatus(humanId, total > 0 ? "ready" : "blocked", `${total} field${total !== 1 ? "s" : ""} filled.`);
    updateBanner(total > 0 ? "Autofill ready — review and submit" : "Autofill: no LLM fields found", total > 0 ? "#15803d" : "#b45309");
    showReviewPanel(total, 0);
    startStepWatcher(humanId, summary);
    return;
  }

  updateBanner(`Found ${llmFields.length} field${llmFields.length !== 1 ? "s" : ""} — asking LLM …`, "#1d4ed8");

  chrome.runtime.sendMessage({ type: "autofill:answer", humanId, fields: llmFields, jobContext: summary.job || null }, (response) => {
    if (!response?.ok) {
      const err = response?.error || "Answer fetch failed";
      console.error("Autofill answer fetch failed", err);
      sendStatus(humanId, "blocked", err);
      const hint = err.includes("503") || err.includes("LLM")
        ? "Autofill blocked — LLM backend unavailable"
        : `Autofill blocked — ${err}`;
      updateBanner(hint, "#b91c1c");
      return;
    }
    const { answers, skipped } = response.data || { answers: {}, skipped: [] };
    const llmFilled = applyAnswers(answers);
    const totalFilled = llmFilled + clFilled;

    markFileInputs(summary);

    if (totalFilled === 0) {
      const reason = skipped.length > 0 ? "LLM skipped all fields — check profile data" : "no answers returned";
      sendStatus(humanId, "blocked", `0 of ${fields.length} fields filled (${reason}).`);
      updateBanner(`Autofill: 0 fields filled — ${reason}`, "#b45309");
    } else {
      sendStatus(humanId, "ready", `${totalFilled} field${totalFilled !== 1 ? "s" : ""} filled; review before submitting.`);
      updateBanner("Autofill ready — review and submit", "#15803d");
    }
    showReviewPanel(totalFilled, skipped.length);

    startStepWatcher(humanId, summary);
  });
}

// Pastes the finalized cover letter into all detected cover-letter textareas.
// Highlighted in purple to distinguish from LLM-filled fields (green).
function fillCoverLetterFields(clFields, coverLetterText) {
  let filled = 0;
  for (const f of clFields) {
    const el = document.querySelector(f.selector);
    if (!el) continue;
    setNativeValue(el, coverLetterText);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
    highlightField(el, "#7c3aed");
    filled++;
  }
  return filled;
}

// Watches for genuinely new form fields after a step is filled.
// Debounced at 600ms to let the ATS finish rendering the next step.
// Filtered by processedSelectors so already-handled fields are skipped.
function startStepWatcher(humanId, summary) {
  if (stepObserver) stepObserver.disconnect();
  let debounceTimer = null;

  stepObserver = new MutationObserver(() => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const allFields = extractFields();
      const newFields = allFields.filter((f) => !processedSelectors.has(f.selector));
      if (newFields.length > 0) {
        runAutofillFields(newFields, humanId, summary);
      }
    }, 600);
  });

  stepObserver.observe(document.body, { childList: true, subtree: true });
}

// ---------------------------------------------------------------------------
// Field extraction
// Handles: input/textarea/select (standard), [role="radiogroup"] (ARIA custom
// radio/button groups used by many modern ATS), and cover-letter textareas
// (flagged separately so they get pasted verbatim, not LLM-answered).
// ---------------------------------------------------------------------------

// Labels that identify a "cover letter" textarea — pasted verbatim from summary.
// Covers common ATS patterns: explicit CL fields, generic long-text boxes on
// application forms, and "why are you interested" style questions.
const COVER_LETTER_LABELS = [
  "cover letter",
  "covering letter",
  "letter of interest",
  "letter of motivation",
  "motivation letter",
  "additional comments",
  "additional information",
  "comments",
  "why are you interested",
  "why do you want to work",
  "why do you want to join",
  "tell us about yourself",
  "what makes you a good fit",
  "what interests you",
  "anything else",
];

function isCoverLetterLabel(label) {
  const lower = label.toLowerCase();
  return COVER_LETTER_LABELS.some((pat) => lower.includes(pat));
}

function extractFields() {
  // Reset group tracking — rebuilt fresh on every scan.
  checkboxGroupMembers.clear();

  const fields = [];

  // --- Standard form elements ---
  document.querySelectorAll("input, textarea, select").forEach((el) => {
    const t = (el.type || "").toLowerCase();
    if (t === "hidden" || t === "submit" || t === "button" || t === "file" || t === "image") return;
    if (t === "radio" && el.dataset.ajpGroupSeen) return;

    const selector = buildSelector(el);
    let label = resolveLabel(el);

    // Global fallback: if resolveLabel found nothing, walk up the DOM.
    // This ensures textareas (like "Comments") and unlabelled inputs get
    // a label from their ancestor question heading before we decide field type.
    if (!label) label = resolveQuestion(el);

    let fieldType;
    let options = [];

    if (el.tagName === "SELECT") {
      fieldType = "select";
      options = Array.from(el.options)
        .map((o) => o.text.trim())
        .filter((text) => text && text !== "—" && text !== "-");

    } else if (t === "radio") {
      fieldType = "radio";
      options = resolveRadioGroup(el);
      document.querySelectorAll(`input[type='radio'][name='${CSS.escape(el.name)}']`).forEach((sib) => {
        sib.dataset.ajpGroupSeen = "1";
      });
      // For radio groups the label should be the question, not the first option text.
      // resolveQuestion gives us the question context regardless of nesting depth.
      const question = resolveQuestion(el);
      if (question) label = question;

    } else if (t === "checkbox") {
      fieldType = "checkbox";
      // Enrich generic standalone labels (Yes/No/I agree) with question context
      // so the LLM has context even for checkboxes that can't be grouped.
      if (isGenericLabel(label)) {
        const question = resolveQuestion(el);
        if (question) label = label ? `${question}: ${label}` : question;
      }

    } else if (el.tagName === "TEXTAREA") {
      fieldType = isCoverLetterLabel(label) ? "cover_letter" : "textarea";

    } else {
      fieldType = "text";
    }

    fields.push({ selector, label, field_type: fieldType, options });
  });

  // --- ARIA radio groups ---
  document.querySelectorAll('[role="radiogroup"]').forEach((group) => {
    if (group.dataset.ajpGroupSeen) return;
    group.dataset.ajpGroupSeen = "1";

    const selector = buildSelector(group);
    const label = resolveAriaGroupLabel(group);
    const options = Array.from(
      group.querySelectorAll('[role="radio"], [role="option"], [role="button"]'),
    )
      .map((r) => (r.textContent.trim() || r.getAttribute("aria-label") || "").trim())
      .filter(Boolean);

    if (!options.length) return;
    fields.push({ selector, label, field_type: "radio", options });
  });

  // --- Post-process: merge named checkbox groups into synthetic radio fields ---
  // Many ATS forms (Lever, some SmartRecruiters) render exclusive Yes/No questions
  // as <input type="checkbox" name="card_XXXXX"> pairs. We group them by name so
  // the LLM sees one question with options rather than N standalone checkboxes,
  // then register the members so applyAnswers can click the right one.
  const byName = new Map();
  for (const f of fields) {
    if (f.field_type !== "checkbox") continue;
    const el = document.querySelector(f.selector);
    if (!el?.name) continue;
    if (!byName.has(el.name)) byName.set(el.name, []);
    byName.get(el.name).push({ field: f, el });
  }

  const toRemove = new Set();
  for (const [, group] of byName) {
    if (group.length < 2) continue;

    const question = resolveQuestion(group[0].el);
    if (!question || question.length < 8) continue;

    // Find the nearest shared ancestor containing all group members.
    let container = group[0].el.parentElement;
    while (container && container.tagName !== "BODY") {
      if (group.every(({ el }) => container.contains(el))) break;
      container = container.parentElement;
    }
    if (!container || container.tagName === "BODY") continue;

    const groupSelector = buildSelector(container);
    const options = group.map(({ el, field }) => {
      // Use the raw checkbox value if available, else the last part of the label.
      return (el.value || field.label.split(": ").pop() || field.label).trim();
    }).filter(Boolean);

    checkboxGroupMembers.set(groupSelector, group.map(({ el, field }, i) => ({
      selector: field.selector,
      value: (el.value || options[i] || "").toLowerCase(),
      label: (options[i] || "").toLowerCase(),
    })));

    group.forEach(({ field }) => toRemove.add(field.selector));
    fields.push({ selector: groupSelector, label: question, field_type: "radio", options });
  }

  return fields.filter((f) => !toRemove.has(f.selector));
}

function buildSelector(el) {
  if (!el.dataset.ajpId) {
    el.dataset.ajpId = `ajp-${ajpIdCounter++}`;
  }
  return `[data-ajp-id='${el.dataset.ajpId}']`;
}

// Strips required-field markers (✱ * † etc.) and caps at 120 chars so
// ATS forms that concatenate option descriptions into the label don't
// blow up the LLM prompt.
function cleanLabel(text) {
  return text
    .replace(/[✱*†‡§¶]+/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .substring(0, 120);
}

function resolveLabel(el) {
  if (el.getAttribute("aria-label")) return cleanLabel(el.getAttribute("aria-label").trim());
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const labelEl = document.getElementById(labelledBy);
    if (labelEl) return cleanLabel(labelEl.textContent.trim());
  }
  if (el.id) {
    try {
      const labelFor = document.querySelector(`label[for='${CSS.escape(el.id)}']`);
      if (labelFor) {
        const clone = labelFor.cloneNode(true);
        clone.querySelectorAll("input, select, textarea, option").forEach((n) => n.remove());
        const text = cleanLabel(clone.textContent.trim());
        if (text) return text;
      }
    } catch (_) {}
  }
  const parentLabel = el.closest("label");
  if (parentLabel) {
    // Prefer direct text nodes — avoids concatenating nested UI feedback text
    // (e.g. "Current location" + "No location found. Try entering a different
    // location Loading" from nested error/hint spans inside the same <label>).
    const directText = Array.from(parentLabel.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent.trim())
      .filter(Boolean)
      .join(" ")
      .trim();
    if (directText) return cleanLabel(directText);

    // Fallback: clone and strip nested form elements.
    const clone = parentLabel.cloneNode(true);
    clone.querySelectorAll("input, select, textarea, option").forEach((n) => n.remove());
    const text = cleanLabel(clone.textContent.trim());
    if (text) return text;
  }
  if (el.placeholder) return cleanLabel(el.placeholder.trim());
  const prev = el.previousElementSibling;
  if (prev && prev.tagName !== "INPUT" && prev.tagName !== "SELECT") {
    const text = cleanLabel(prev.textContent.trim());
    if (text) return text;
  }
  return "";
}

function resolveRadioGroup(radioEl) {
  if (!radioEl.name) return [];
  const group = document.querySelectorAll(`input[type='radio'][name='${CSS.escape(radioEl.name)}']`);
  return Array.from(group)
    .map((r) => resolveLabel(r) || r.value)
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// Question context resolution
// resolveQuestion walks up the DOM without a fixed depth cap. It stops at
// semantic boundaries (form/body/main etc.) or when an ancestor contains
// more than 3 form fields — meaning we've gone past a single question's scope.
// This way it works for any nesting depth without a magic number.
// ---------------------------------------------------------------------------

function isGenericLabel(text) {
  if (!text) return true;
  const lower = text.toLowerCase().trim();
  if (lower.length < 4) return true;
  return ["yes", "no", "true", "false", "i agree", "agree", "accept",
          "select", "choose", "other", "none", "n/a", "ok", "skip"].includes(lower);
}

// Gets text from an element after stripping nested form/interactive children.
function getTextFromEl(el) {
  const clone = el.cloneNode(true);
  clone.querySelectorAll("input, select, textarea, button, option, script, style").forEach((n) => n.remove());
  return cleanLabel(clone.textContent.trim());
}

// Walks up from el looking for the question text that contextualises this field.
function resolveQuestion(el) {
  const STOP_TAGS = new Set(["FORM", "BODY", "HTML", "ARTICLE", "MAIN", "NAV", "HEADER", "FOOTER", "ASIDE"]);
  const QUESTION_TEXT_TAGS = new Set(["P", "H1", "H2", "H3", "H4", "H5", "H6", "LEGEND", "LABEL", "SPAN", "DIV", "LI"]);
  let node = el.parentElement;
  let depth = 0;

  while (node) {
    depth++;
    if (STOP_TAGS.has(node.tagName)) break;
    const role = node.getAttribute("role") || "";
    if (["main", "region", "banner", "navigation"].includes(role)) break;

    // Past the first two levels, if this ancestor contains more than 3 form
    // fields we have gone past the scope of a single question — stop here.
    if (depth > 2) {
      const inputCount = node.querySelectorAll("input:not([type=hidden]), select, textarea").length;
      if (inputCount > 3) break;
    }

    // fieldset / legend — highest priority
    if (node.tagName === "FIELDSET") {
      const legend = node.querySelector(":scope > legend");
      if (legend) {
        const t = cleanLabel(legend.textContent.trim());
        if (t.length >= 4) return t;
      }
    }

    // aria-labelledby on the ancestor itself
    const labelledBy = node.getAttribute("aria-labelledby");
    if (labelledBy) {
      const labelEl = document.getElementById(labelledBy);
      if (labelEl && !labelEl.contains(el)) {
        const t = cleanLabel(labelEl.textContent.trim());
        if (t.length >= 4) return t;
      }
    }

    // Children of this ancestor that precede our subtree — look for headings/paragraphs
    for (const child of node.children) {
      if (child.contains(el) || child === el) break;
      if (child.querySelector("input:not([type=hidden]), select, textarea")) continue;
      if (QUESTION_TEXT_TAGS.has(child.tagName)) {
        const t = getTextFromEl(child);
        if (t.length >= 8) return t;
      }
    }

    // Preceding siblings of this node
    let prev = node.previousElementSibling;
    while (prev) {
      if (!prev.querySelector("input:not([type=hidden]), select, textarea")) {
        const t = getTextFromEl(prev);
        if (t.length >= 8) return t;
      }
      prev = prev.previousElementSibling;
    }

    node = node.parentElement;
  }

  return "";
}

// Resolves the question label for a [role="radiogroup"] container by checking
// aria-labelledby, aria-label, then nearby sibling/parent text.
function resolveAriaGroupLabel(group) {
  const labelledBy = group.getAttribute("aria-labelledby");
  if (labelledBy) {
    const el = document.getElementById(labelledBy);
    if (el) return el.textContent.trim();
  }
  if (group.getAttribute("aria-label")) return group.getAttribute("aria-label").trim();
  // Look for a preceding sibling or parent heading that acts as the question.
  const prev = group.previousElementSibling;
  if (prev) {
    const text = prev.textContent.trim();
    if (text) return text;
  }
  const parent = group.parentElement;
  if (parent) {
    // Take only the text directly in the parent, not inside the group itself.
    const clone = parent.cloneNode(true);
    clone.querySelectorAll('[role="radiogroup"]').forEach((n) => n.remove());
    const text = clone.textContent.trim();
    if (text) return text;
  }
  return "";
}

// ---------------------------------------------------------------------------
// Applying answers
// Bug 2 fix: use the native prototype setter so React/Angular synthetic event
// systems see the value change and update their internal state.
// ---------------------------------------------------------------------------

function setNativeValue(el, value) {
  const proto =
    el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
  const nativeSetter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (nativeSetter) {
    nativeSetter.call(el, value);
  } else {
    el.value = value;
  }
}

function applyAnswers(answers) {
  let filled = 0;
  for (const [selector, value] of Object.entries(answers)) {
    if (!value || typeof value !== "string") continue;
    const el = document.querySelector(selector);
    if (!el) continue;

    let ok = false;
    if (checkboxGroupMembers.has(selector)) {
      // Synthetic radio group backed by named checkboxes (e.g. Lever Yes/No questions).
      ok = applyCheckboxGroup(selector, value);
    } else if (el.getAttribute("role") === "radiogroup") {
      ok = applyAriaRadioGroup(el, value);
    } else if (el.tagName === "SELECT") {
      ok = applySelect(el, value);
    } else if ((el.type || "").toLowerCase() === "radio") {
      ok = applyRadioGroup(el.name, value);
    } else if ((el.type || "").toLowerCase() === "checkbox") {
      ok = applyCheckbox(el, value);
    } else {
      setNativeValue(el, String(value));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
      ok = true;
    }

    if (ok) {
      highlightField(el, "#15803d");
      filled++;
    }
  }
  return filled;
}

function applySelect(el, value) {
  const lower = value.toLowerCase().trim();
  const options = Array.from(el.options);

  // Pass 1: exact / prefix / substring checks
  let match = options.find(
    (o) =>
      o.text.toLowerCase().trim() === lower ||
      o.value.toLowerCase().trim() === lower ||
      o.text.toLowerCase().trim().startsWith(lower) ||
      lower.startsWith(o.text.toLowerCase().trim()) ||
      o.text.toLowerCase().trim().includes(lower),
  );

  // Pass 2: word-overlap fallback — handles profile values that differ slightly
  // from option text (e.g. "I am not a protected veteran" → "I am not a veteran").
  // Picks the HIGHEST-scoring option above 60% threshold, not just the first.
  // Without sorting, "I am a veteran" (67%) would beat "I am not a veteran" (83%)
  // simply because it appears earlier in the options list.
  if (!match) {
    const answerWords = lower.split(/\s+/);
    const threshold = Math.ceil(answerWords.length * 0.6);
    const scored = options
      .map((o) => {
        const optWords = new Set(o.text.toLowerCase().trim().split(/\s+/));
        const score = answerWords.filter((w) => optWords.has(w)).length;
        return { o, score };
      })
      .filter(({ score }) => score >= threshold)
      .sort((a, b) => b.score - a.score);
    match = scored[0]?.o;
  }

  if (!match) return false;
  el.value = match.value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
  return true;
}

function applyRadioGroup(groupName, value) {
  if (!groupName) return false;
  const lower = value.toLowerCase().trim();
  const radios = document.querySelectorAll(`input[type='radio'][name='${CSS.escape(groupName)}']`);
  for (const radio of radios) {
    const label = resolveLabel(radio) || radio.value;
    if (label.toLowerCase().trim() === lower || radio.value.toLowerCase().trim() === lower) {
      if (!radio.checked) radio.click();
      radio.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
      return true;
    }
  }
  return false;
}

function applyCheckbox(el, value) {
  const lower = value.toLowerCase().trim();
  const shouldCheck = lower === "yes" || lower === "true" || lower === "checked";
  if (el.checked !== shouldCheck) el.click();
  return true;
}

// Handles [role="radiogroup"] containers — clicks the child option whose
// text matches the answer. Used for custom Yes/No pickers and styled option
// groups that don't use <input type="radio"> elements.
function applyAriaRadioGroup(container, value) {
  const lower = value.toLowerCase().trim();
  const options = container.querySelectorAll('[role="radio"], [role="option"], [role="button"]');
  for (const opt of options) {
    const text = (opt.textContent.trim() || opt.getAttribute("aria-label") || "").toLowerCase().trim();
    if (text === lower || text.startsWith(lower) || lower.startsWith(text)) {
      opt.click();
      opt.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
      return true;
    }
  }
  return false;
}

// Clicks the checkbox in a named group whose value/label matches the LLM answer.
function applyCheckboxGroup(groupSelector, value) {
  const members = checkboxGroupMembers.get(groupSelector);
  if (!members) return false;
  const lower = value.toLowerCase().trim();
  for (const { selector, value: memberValue, label: memberLabel } of members) {
    const cb = document.querySelector(selector);
    if (!cb) continue;
    const cbText = memberValue || memberLabel;
    if (cbText === lower || cbText.startsWith(lower) || lower.startsWith(cbText)) {
      if (!cb.checked) cb.click();
      cb.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// File upload annotation
// ---------------------------------------------------------------------------

function markFileInputs(summary) {
  const resumePath = summary.resume_path || "";
  const clPath = summary.cover_letter_pdf || "";
  document.querySelectorAll("input[type='file']").forEach((input) => {
    const name = (input.name || input.id || "").toLowerCase();
    if (resumePath && (name.includes("resume") || name.includes("cv"))) {
      markUploadInput(input, resumePath);
    } else if (clPath && (name.includes("cover") || name.includes("letter"))) {
      markUploadInput(input, clPath);
    } else if (resumePath) {
      markUploadInput(input, resumePath);
    }
  });
}

// ---------------------------------------------------------------------------
// Review panel
// ---------------------------------------------------------------------------

function showReviewPanel(filledCount, skippedCount) {
  if (window.self !== window.top) {
    // Panel must appear in the top frame so the user sees it above the iframe.
    chrome.runtime.sendMessage({ type: "autofill:show_panel", filledCount, skippedCount });
    return;
  }
  const existing = document.getElementById("ajp-review-panel");
  if (existing) existing.remove();

  const panel = document.createElement("div");
  panel.id = "ajp-review-panel";
  Object.assign(panel.style, {
    position: "fixed",
    bottom: "20px",
    right: "20px",
    zIndex: 2147483647,
    padding: "16px 20px",
    background: "#111827",
    color: "white",
    borderRadius: "8px",
    fontFamily: "system-ui, sans-serif",
    fontSize: "13px",
    maxWidth: "320px",
    boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
  });

  const heading = document.createElement("strong");
  heading.textContent = "AJP Autofill — Review before submitting";
  heading.style.display = "block";
  heading.style.marginBottom = "8px";

  const summary = document.createElement("p");
  summary.textContent = `${filledCount} field${filledCount !== 1 ? "s" : ""} filled · ${skippedCount} skipped`;
  summary.style.margin = "0 0 12px 0";
  summary.style.color = "#9ca3af";

  const note = document.createElement("p");
  note.textContent = "Please review all filled values and manually attach any files before submitting.";
  note.style.margin = "0 0 12px 0";
  note.style.lineHeight = "1.4";

  const btn = document.createElement("button");
  btn.textContent = "Got it — I'll review and submit";
  Object.assign(btn.style, {
    background: "#15803d",
    color: "white",
    border: "none",
    borderRadius: "4px",
    padding: "8px 12px",
    cursor: "pointer",
    fontSize: "13px",
    width: "100%",
  });
  btn.addEventListener("click", () => panel.remove());

  panel.appendChild(heading);
  panel.appendChild(summary);
  panel.appendChild(note);
  panel.appendChild(btn);
  document.body.appendChild(panel);
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function highlightField(field, color = "#2563eb") {
  if (!field || !field.style) return;
  field.style.outline = `2px solid ${color}`;
}

function markUploadInput(input, suggestedPath) {
  input.style.outline = "2px dashed #d97706";
  input.setAttribute("data-ajp-suggested-path", suggestedPath || "");
  if (suggestedPath && !input.dataset.ajpTooltipAttached) {
    input.dataset.ajpTooltipAttached = "true";
    input.addEventListener("focus", () => {
      input.title = `Suggested file: ${suggestedPath}`;
    });
  }
}

function sendStatus(humanId, status, message) {
  chrome.runtime.sendMessage({
    type: "autofill:status",
    payload: { human_id: humanId, status, message },
  });
}
