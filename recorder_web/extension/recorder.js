// Recorder window controller.
//
// Owns the entire session: getDisplayMedia, MediaRecorder, event buffer,
// downloads. Runs inside a real Chrome window (chrome.windows.create with
// type:"popup") opened by background.js when the user clicks the toolbar
// icon. A real window survives the screen-share picker stealing focus.
//
// Why not a browser-action popup? Popups close the moment the picker
// dialog appears, killing the JS context — getDisplayMedia callbacks and
// MediaRecorder both die with it.

const RECORD_FLAG_KEY = "sop_recording_active";

// ── DOM ────────────────────────────────────────────────────────────────
const idleView = document.getElementById("idle-view");
const recView = document.getElementById("recording-view");
const errView = document.getElementById("error-view");
const taskInput = document.getElementById("task-name");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const dismissBtn = document.getElementById("dismiss-btn");
const statusTask = document.getElementById("status-task");
const durationEl = document.getElementById("duration");
const eventCountEl = document.getElementById("event-count");
const errorMsg = document.getElementById("error-msg");

function showView(view) {
  idleView.hidden = view !== "idle";
  recView.hidden = view !== "recording";
  errView.hidden = view !== "error";
}

function fmtDuration(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

function showError(message) {
  errorMsg.textContent = message || "Unknown error";
  showView("error");
}

// ── Session state ──────────────────────────────────────────────────────
let mediaRecorder = null;
let chunks = [];
let stream = null;
let sessionId = null;
let startedAt = null;
let endedAt = null;
let taskName = "";
let events = [];
let tabsSeen = {};
let recording = false;
let durationTimer = null;

function appendEvent(rawEvent, sender) {
  const ts = (rawEvent.ts_client || Date.now()) - startedAt;
  const tab = sender && sender.tab;
  const enriched = {
    id: events.length,
    ts: Math.max(0, ts),
    tab_id: tab ? tab.id : null,
    frame_id: sender ? sender.frameId : 0,
    url: (tab && tab.url) || rawEvent.frame_url || "",
    ...rawEvent,
  };
  delete enriched.ts_client;
  events.push(enriched);
  eventCountEl.textContent = events.length;

  if (tab) {
    if (!tabsSeen[tab.id]) {
      tabsSeen[tab.id] = {
        tab_id: tab.id,
        title: tab.title || "",
        first_url: tab.url || "",
        last_url: tab.url || "",
      };
    } else {
      tabsSeen[tab.id].last_url = tab.url || "";
      if (tab.title) tabsSeen[tab.id].title = tab.title;
    }
  }
}

function appendSystemEvent(kind, payload) {
  const ts = Date.now() - startedAt;
  events.push({ id: events.length, kind, ts: Math.max(0, ts), ...payload });
  eventCountEl.textContent = events.length;
}

// ── Listeners (always registered; only fire when recording === true) ───
chrome.runtime.onMessage.addListener((msg, sender, _sendResponse) => {
  if (!recording) return false;
  if (msg && msg.type === "EVENT" && msg.event) {
    appendEvent(msg.event, sender);
  }
  return false;
});

chrome.tabs.onActivated.addListener(async (info) => {
  if (!recording) return;
  let tab = null;
  try { tab = await chrome.tabs.get(info.tabId); } catch (_) {}
  appendSystemEvent("tab_switched", {
    to_tab: info.tabId,
    to_url: tab ? tab.url : "",
    to_title: tab ? tab.title : "",
  });
});

chrome.webNavigation.onCommitted.addListener((details) => {
  if (!recording || details.frameId !== 0) return;
  appendSystemEvent("navigation", {
    tab_id: details.tabId,
    url: details.url,
    transition: details.transitionType,
  });
});

// Inject the content script into every existing http(s)/file tab. The manifest
// only auto-injects into pages loaded AFTER install, so without this, pre-
// existing tabs never get the listeners.
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

// ── Start ──────────────────────────────────────────────────────────────
startBtn.addEventListener("click", async () => {
  const name = (taskInput.value || "").trim();
  if (!name) { taskInput.focus(); return; }

  startBtn.disabled = true;
  startBtn.textContent = "Choose a window…";

  let chosenStream;
  try {
    // ``getDisplayMedia`` rejects ``min``/``max`` constraints (only plain
    // values and ``exact`` are accepted). The frame-density problem is
    // addressed below via ``videoBitsPerSecond`` on the MediaRecorder.
    chosenStream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 30 },
      audio: false,
    });
  } catch (err) {
    console.error("[recorder] getDisplayMedia failed", err);
    startBtn.disabled = false;
    startBtn.textContent = "Start recording";
    showError(err.message || "Picker was cancelled.");
    return;
  }

  stream = chosenStream;
  taskName = name;
  sessionId = crypto.randomUUID();
  events = [];
  tabsSeen = {};

  for (const track of stream.getTracks()) {
    track.addEventListener("ended", () => {
      // User clicked Chrome's "Stop sharing" pill.
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        try { mediaRecorder.stop(); } catch (_) {}
      }
    });
  }

  const candidates = [
    "video/webm;codecs=vp9,opus",
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
  ];
  const mimeType = candidates.find((m) => MediaRecorder.isTypeSupported(m)) || "";

  chunks = [];
  // ``videoBitsPerSecond: 8 Mbps`` gives the encoder a generous bit budget
  // so VP9 stops aggressively dropping frames during low-motion windows
  // (e.g. cursor settling on a button before a click). Without this, the
  // encoder may go 500+ ms without emitting any frame at all, leaving
  // holes where the most important keyframes should be.
  const recorderOptions = mimeType
    ? { mimeType, videoBitsPerSecond: 8_000_000 }
    : { videoBitsPerSecond: 8_000_000 };
  mediaRecorder = new MediaRecorder(stream, recorderOptions);
  mediaRecorder.addEventListener("dataavailable", (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data);
  });
  mediaRecorder.addEventListener("stop", finalize);

  // Critical: anchor ``startedAt`` to the MediaRecorder ``start`` event,
  // not to the moment we kick off ``getDisplayMedia``. Otherwise event
  // timestamps and the video timeline use different origins — the encoder
  // takes 50–500 ms to warm up between ``start()`` being called and the
  // first frame being captured, so screenshots end up that much *late*
  // (showing what was on screen *after* the click instead of just before).
  await new Promise((resolve, reject) => {
    mediaRecorder.addEventListener("start", () => {
      startedAt = Date.now();
      resolve();
    }, { once: true });
    mediaRecorder.addEventListener("error", (e) => reject(e?.error || e), { once: true });
    mediaRecorder.start(1000);
  });

  // Tell the SW to grant content-script access to chrome.storage.session and
  // flip the recording flag, then inject the content script everywhere.
  try {
    await chrome.runtime.sendMessage({ type: "RECORDING_STARTED" });
  } catch (_) {}
  await chrome.storage.session.set({ [RECORD_FLAG_KEY]: true });
  await injectIntoAllTabs();

  recording = true;
  statusTask.textContent = `Recording: ${taskName}`;
  eventCountEl.textContent = "0";
  durationEl.textContent = "0:00";
  if (durationTimer) clearInterval(durationTimer);
  durationTimer = setInterval(() => {
    durationEl.textContent = fmtDuration(Date.now() - startedAt);
  }, 500);
  showView("recording");
  console.debug("[recorder] recording started", sessionId);
});

// ── Stop ───────────────────────────────────────────────────────────────
stopBtn.addEventListener("click", () => {
  stopBtn.disabled = true;
  stopBtn.textContent = "Saving…";
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop(); // → finalize()
  } else {
    finalize();
  }
});

dismissBtn.addEventListener("click", () => {
  showView("idle");
  startBtn.disabled = false;
  startBtn.textContent = "Start recording";
});

// ── Finalize ───────────────────────────────────────────────────────────
function tsForFilename(ms) {
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-` +
    `${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`
  );
}

function safeTaskName(s) {
  return (s || "untitled").replace(/[^a-zA-Z0-9_\-]+/g, "_").slice(0, 60);
}

async function finalize() {
  recording = false;
  endedAt = Date.now();
  await chrome.storage.session.set({ [RECORD_FLAG_KEY]: false });
  if (durationTimer) { clearInterval(durationTimer); durationTimer = null; }

  const blob = new Blob(chunks, {
    type: (mediaRecorder && mediaRecorder.mimeType) || "video/webm",
  });
  const videoUrl = URL.createObjectURL(blob);
  const folder = `recording_${safeTaskName(taskName)}_${tsForFilename(startedAt)}`;

  const manifest = {
    session_id: sessionId,
    task_name: taskName,
    started_at: new Date(startedAt).toISOString(),
    ended_at: new Date(endedAt).toISOString(),
    duration_ms: endedAt - startedAt,
    video_file: "recording.webm",
    events_file: "events.json",
    video_mime: blob.type,
    video_size_bytes: blob.size,
    extension_version: chrome.runtime.getManifest().version,
    user_agent: navigator.userAgent,
    capture_surface: "user_choice",
    viewport: { w: window.screen.width, h: window.screen.height },
    device_pixel_ratio: window.devicePixelRatio || 1,
    tabs_seen: Object.values(tabsSeen),
  };

  const eventsBundle = {
    session_id: sessionId,
    task_name: taskName,
    started_at: manifest.started_at,
    ended_at: manifest.ended_at,
    events,
  };

  const eventsUrl = URL.createObjectURL(
    new Blob([JSON.stringify(eventsBundle, null, 2)], { type: "application/json" }),
  );
  const manifestUrl = URL.createObjectURL(
    new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }),
  );

  try {
    await chrome.downloads.download({ url: videoUrl, filename: `${folder}/recording.webm`, saveAs: false });
    await chrome.downloads.download({ url: eventsUrl, filename: `${folder}/events.json`, saveAs: false });
    await chrome.downloads.download({ url: manifestUrl, filename: `${folder}/manifest.json`, saveAs: false });
  } catch (err) {
    console.error("[recorder] download failed", err);
    showError("Download failed: " + (err.message || String(err)));
  }

  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }

  setTimeout(() => {
    URL.revokeObjectURL(videoUrl);
    URL.revokeObjectURL(eventsUrl);
    URL.revokeObjectURL(manifestUrl);
    window.close();
  }, 4000);
}

// ── Boot ───────────────────────────────────────────────────────────────
chrome.storage.session.set({ [RECORD_FLAG_KEY]: false });
showView("idle");
console.debug("[recorder] window ready");
