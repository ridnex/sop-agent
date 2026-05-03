// Background service worker.
//
// Minimal responsibilities:
//   1. On install/startup/boot: grant content scripts access to
//      chrome.storage.session (default access level is TRUSTED_CONTEXTS only,
//      which prevents content scripts from reading the recording flag).
//      THIS IS THE FIX for v0.2's "no click/input events captured" bug.
//   2. Inject the content script into already-open tabs (the manifest's
//      content_scripts directive only fires for pages loaded AFTER install).
//   3. Open the recorder window when the user clicks the toolbar icon
//      (focus the existing window if one is already open).
//
// Everything else — getDisplayMedia, MediaRecorder, event buffering, downloads
// — lives in the recorder window (recorder.js). The window is a real Chrome
// window, so it survives the screen-share picker dialog stealing focus.

const RECORDER_PATH = "recorder.html";
const RECORD_FLAG_KEY = "sop_recording_active";

// ── Listener registration (synchronous, top-level) ───────────────────
chrome.runtime.onInstalled.addListener(() => { bootstrap("onInstalled"); });
chrome.runtime.onStartup.addListener(() => { bootstrap("onStartup"); });

chrome.action.onClicked.addListener(async () => {
  await openRecorderWindow();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "RECORDING_STARTED") {
    // Recorder window tells us recording started — re-inject content scripts
    // so any tab opened between window-open and Start gets listeners too.
    injectIntoAllTabs().catch(() => {});
    try { sendResponse({ ok: true }); } catch (_) {}
  }
  return false;
});

chrome.windows.onRemoved.addListener(async (id) => {
  if (id === recorderWindowId) {
    recorderWindowId = null;
    // Window closed — make sure content scripts stop sending into the void.
    try { await chrome.storage.session.set({ [RECORD_FLAG_KEY]: false }); } catch (_) {}
  }
});

// Run bootstrap on every worker boot so a bare "Reload extension" still
// configures storage access.
bootstrap("worker-boot").catch((e) => console.error("[bg] bootstrap failed", e));

// ── Bootstrap ─────────────────────────────────────────────────────────
async function bootstrap(reason) {
  console.debug("[bg] bootstrap (", reason, ")");
  try {
    await chrome.storage.session.setAccessLevel({
      accessLevel: "TRUSTED_AND_UNTRUSTED_CONTEXTS",
    });
  } catch (e) {
    console.warn("[bg] setAccessLevel failed:", e);
  }
  try { await chrome.storage.session.set({ [RECORD_FLAG_KEY]: false }); } catch (_) {}
  try { await injectIntoAllTabs(); } catch (e) { console.warn("[bg] injectIntoAllTabs", e); }
}

async function injectIntoAllTabs() {
  let tabs = [];
  try { tabs = await chrome.tabs.query({}); } catch (_) { return; }
  for (const tab of tabs) {
    if (!tab.id) continue;
    const url = tab.url || tab.pendingUrl || "";
    if (!/^https?:|^file:/.test(url)) continue;
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        files: ["content_script.js"],
      });
    } catch (_) {
      // Restricted page — silently skip.
    }
  }
}

// ── Recorder window ───────────────────────────────────────────────────
let recorderWindowId = null;

async function openRecorderWindow() {
  if (recorderWindowId !== null) {
    try {
      await chrome.windows.get(recorderWindowId);
      await chrome.windows.update(recorderWindowId, { focused: true });
      return;
    } catch (_) {
      recorderWindowId = null;
    }
  }
  const win = await chrome.windows.create({
    url: chrome.runtime.getURL(RECORDER_PATH),
    type: "popup",
    width: 380,
    height: 560,
  });
  recorderWindowId = win.id;
}
