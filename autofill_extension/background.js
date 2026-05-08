// Local dev token — must match AUTOFILL_API_TOKEN in .env.
const DEV_TOKEN = "lit-crazy-party-or-a-project";

// Matches the ATS domains in manifest host_permissions.
const AUTOFILL_ATS_SUFFIXES = [
  "greenhouse.io",
  "lever.co",
  "myworkdayjobs.com",
  "ashbyhq.com",
  "smartrecruiters.com",
  "icims.com",
];

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function getSettings() {
  const { apiBaseUrl, apiToken } = await chrome.storage.local.get({
    apiBaseUrl: "http://127.0.0.1:8000/api/v1/autofill",
    apiToken: "",
  });
  return { apiBaseUrl, apiToken: apiToken || DEV_TOKEN };
}

// ---------------------------------------------------------------------------
// API proxy helpers
// ---------------------------------------------------------------------------

async function fetchPayload(humanId) {
  const { apiBaseUrl, apiToken } = await getSettings();
  console.log("[AJP] fetchPayload — apiToken:", JSON.stringify(apiToken), "len:", apiToken.length);
  const url = `${apiBaseUrl.replace(/\/$/, "")}/payload/${encodeURIComponent(humanId)}`;
  const headers = apiToken ? { "X-Autofill-Token": apiToken } : {};
  const response = await fetch(url, { headers });
  if (!response.ok) {
    throw new Error(`Payload fetch failed: ${response.status}`);
  }
  return response.json();
}

async function postAnswers(humanId, fields, jobContext) {
  const { apiBaseUrl, apiToken } = await getSettings();
  const url = `${apiBaseUrl.replace(/\/$/, "")}/answer`;
  const headers = { "Content-Type": "application/json" };
  if (apiToken) headers["X-Autofill-Token"] = apiToken;
  const body = { human_id: humanId, fields, job_context: jobContext || null };
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Answer fetch failed: ${response.status}`);
  }
  return response.json();
}

async function postStatus(update) {
  const { apiBaseUrl, apiToken } = await getSettings();
  const url = `${apiBaseUrl.replace(/\/$/, "")}/status`;
  const headers = { "Content-Type": "application/json" };
  if (apiToken) headers["X-Autofill-Token"] = apiToken;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(update),
  });
  if (!response.ok) {
    throw new Error(`Status post failed: ${response.status}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Session storage — humanId persists across SPA nav and full-page reloads.
// chrome.storage.session is cleared when the browser session ends, so we
// never accidentally autofill a job from a previous session.
// ---------------------------------------------------------------------------

const sessionKey = (tabId) => `autofill_tab_${tabId}`;

async function savePendingHumanId(tabId, humanId) {
  await chrome.storage.session.set({ [sessionKey(tabId)]: humanId });
}

async function loadPendingHumanId(tabId) {
  const result = await chrome.storage.session.get(sessionKey(tabId));
  return result[sessionKey(tabId)] || null;
}

async function clearPendingHumanId(tabId) {
  await chrome.storage.session.remove(sessionKey(tabId));
}

// ---------------------------------------------------------------------------
// broadcastToTab — send autofill:run to every frame in the tab.
// reset=true tells content.js to clear its hasRun guard and processedSelectors,
// which is needed when navigating to a new page or SPA route.
// ---------------------------------------------------------------------------

function broadcastToTab(tabId, humanId, reset = false) {
  chrome.webNavigation.getAllFrames({ tabId }, (frames) => {
    if (chrome.runtime.lastError || !frames) return;
    for (const frame of frames) {
      chrome.tabs.sendMessage(
        tabId,
        { type: "autofill:run", humanId, reset },
        { frameId: frame.frameId },
        () => void chrome.runtime.lastError,
      );
    }
  });
}

// ---------------------------------------------------------------------------
// Navigation listeners — re-trigger autofill after SPA pushState and
// full-page reloads (multi-step forms that do a hard navigation per step).
//
// Safe on initial load: onCompleted fires before document_idle, so
// chrome.storage.session has no humanId yet when the first page loads.
// The handler finds nothing and returns. humanId is saved only after the
// content script broadcasts autofill:broadcast. Subsequent navigations on
// the same tab then find the saved humanId and re-trigger correctly.
// ---------------------------------------------------------------------------

function isAtsDomain(url) {
  try {
    const { hostname } = new URL(url);
    return AUTOFILL_ATS_SUFFIXES.some(
      (suffix) => hostname === suffix || hostname.endsWith(`.${suffix}`),
    );
  } catch {
    return false;
  }
}

async function handleNavigation(details) {
  if (details.frameId !== 0) return;
  if (!isAtsDomain(details.url)) return;

  const humanId = await loadPendingHumanId(details.tabId);
  if (!humanId) return;

  // Give the SPA 800ms to render the new view before scanning for fields.
  setTimeout(() => broadcastToTab(details.tabId, humanId, true), 800);
}

chrome.webNavigation.onHistoryStateUpdated.addListener(handleNavigation);
chrome.webNavigation.onCompleted.addListener(handleNavigation);

// Release session storage when the tab closes.
chrome.tabs.onRemoved.addListener((tabId) => clearPendingHumanId(tabId));

// ---------------------------------------------------------------------------
// Message router
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "autofill:payload") {
    fetchPayload(message.humanId)
      .then((data) => sendResponse({ ok: true, data }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type === "autofill:answer") {
    postAnswers(message.humanId, message.fields, message.jobContext)
      .then((data) => sendResponse({ ok: true, data }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type === "autofill:status") {
    postStatus(message.payload)
      .then((data) => sendResponse({ ok: true, data }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  // Relay banner and panel messages from iframes to the top frame (frameId 0)
  // so the user always sees status in the visible page, not buried in an iframe.
  if (message?.type === "autofill:banner_update" || message?.type === "autofill:show_panel") {
    const tabId = sender?.tab?.id;
    if (tabId) {
      chrome.tabs.sendMessage(tabId, message, { frameId: 0 }, () => void chrome.runtime.lastError);
    }
    return false;
  }
  // Top frame detected the autofill hash. Save the humanId to session storage
  // so navigation listeners can find it later, then broadcast to all frames.
  if (message?.type === "autofill:broadcast") {
    const tabId = sender?.tab?.id;
    const { humanId } = message;
    if (tabId && humanId) {
      savePendingHumanId(tabId, humanId).then(() => {
        broadcastToTab(tabId, humanId, false);
      });
    }
    sendResponse({ ok: true });
    return false;
  }
  return false;
});
