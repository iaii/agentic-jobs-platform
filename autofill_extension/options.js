const form = document.getElementById("settings-form");
const apiBaseInput = document.getElementById("apiBase");
const apiTokenInput = document.getElementById("apiToken");
const statusEl = document.getElementById("status");

async function loadSettings() {
  const { apiBaseUrl, apiToken } = await chrome.storage.local.get({ apiBaseUrl: "http://127.0.0.1:8000/api/v1/autofill", apiToken: "" });
  apiBaseInput.value = apiBaseUrl;
  apiTokenInput.value = apiToken;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await chrome.storage.local.set({
    apiBaseUrl: apiBaseInput.value.trim() || "http://127.0.0.1:8000/api/v1/autofill",
    apiToken: apiTokenInput.value.trim(),
  });
  statusEl.textContent = "Saved";
  setTimeout(() => (statusEl.textContent = ""), 1500);
});

loadSettings();
