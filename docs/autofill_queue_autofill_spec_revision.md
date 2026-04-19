# Autofill Pipeline — Native Messaging Architecture (Reference)

> **Status: superseded.** The implemented architecture uses a local HTTP API bridge (FastAPI) rather than Native Messaging. See [`autofill_queue_autofill_spec_consolidated.md`](autofill_queue_autofill_spec_consolidated.md) for the current design.
>
> This document is kept as a reference for the Native Messaging approach, which may be revisited for credential flows (Workday JIT login) where a direct `stdin`/`stdout` channel to a local Python process avoids mixed-content issues entirely.

---

## Why this approach was explored

The HTTP bridge (`http://127.0.0.1:8000`) can trigger mixed-content warnings when injected into HTTPS ATS pages that disallow non-secure requests. Native Messaging solves this by routing through Chrome's own `stdin`/`stdout` IPC — no network socket involved.

---

## Architecture overview

```
Extension background script
  └─ chrome.runtime.sendNativeMessage("com.agentic_jobs.host", {type: "GET_PAYLOAD"})
        │
        ▼  (Chrome launches the registered Python script)
Python Native Host (native_host.py)
  └─ reads from stdin → calls controller logic → writes JSON to stdout
        │
        ▼
Extension receives payload, fills form
```

---

## Native Host registration

**macOS** — place at `/Library/Google/Chrome/NativeMessagingHosts/com.agentic_jobs.host.json`:

```json
{
  "name": "com.agentic_jobs.host",
  "description": "Agentic Job Search Copilot Autofill Host",
  "path": "/path/to/venv/bin/python",
  "args": ["/path/to/agentic_jobs/services/autofill/native_host.py"],
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://<YOUR-EXTENSION-ID>/"
  ]
}
```

**`manifest.json`** — add `nativeMessaging` permission:

```json
{
  "permissions": ["nativeMessaging", "scripting", "activeTab", "storage"],
  "host_permissions": [
    "https://*.greenhouse.io/*",
    "https://*.lever.co/*",
    "https://*.myworkdayjobs.com/*"
  ]
}
```

---

## Native host stub (`native_host.py`)

```python
import sys, json, struct

def get_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        sys.exit(0)
    length = struct.unpack("=I", raw_length)[0]
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))

def send_message(content):
    encoded = json.dumps(content).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()

if __name__ == "__main__":
    while True:
        try:
            msg = get_message()
            if msg["type"] == "GET_PAYLOAD":
                # payload = controller.get_application_payload(msg["human_id"])
                send_message({"status": "success", "data": {}})
        except Exception as e:
            send_message({"status": "error", "message": str(e)})
```

---

## Local Blob Fetch pattern (file uploads)

Browsers block programmatic assignment to `<input type="file">` from content scripts. The Local Blob Fetch pattern works around this:

1. Orchestrator spins up a temporary HTTP server: `http://127.0.0.1:9999/tmp/<one-time-token>/resume.pdf`.
2. Payload sent to extension includes this URL.
3. Extension `fetch()`es the URL → converts to `Blob` → uses `DataTransfer` to assign to the file input.
4. The one-time token expires after the first request.

This avoids the file picker dialog entirely when the browser permits localhost fetches from content scripts.

---

## React/Angular-compatible field filler

```javascript
const fillField = (input, value) => {
    const descriptor = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value"
    );
    descriptor.set.call(input, value);
    input.dispatchEvent(new Event("input",  { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(new Event("blur",   { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: value[0] }));
    input.dispatchEvent(new KeyboardEvent("keyup",   { bubbles: true, key: value[0] }));
};
```

Setting value via `Object.getOwnPropertyDescriptor` bypasses React's synthetic event system; dispatching the full event suite ensures Angular/Vue change detection fires.

---

## Security notes

- `allowed_origins` in the NativeMessaging manifest must be pinned to your specific extension ID.
- The Python host must not `eval()` anything received from the browser.
- One-time tokens for the Local Asset Server prevent other local processes from fetching profile files.
- Credentials retrieved from OS Keychain must be wiped from extension memory immediately after use — never persisted to `localStorage` or sent to any server.
