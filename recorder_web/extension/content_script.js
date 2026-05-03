// Content script — runs in every frame of every page at document_start.
// Captures DOM events and forwards them to the background service worker.
//
// Design notes:
// - Listeners use { capture: true, passive: true } so they fire before page
//   handlers and never block scrolling/typing.
// - Selector + xpath built from event.composedPath()[0] so shadow-DOM hosts
//   work correctly (Salesforce Lightning, YouTube, Stripe Checkout, etc.).
// - Per-element `input` events are debounced (400 ms) so we emit one event
//   carrying the *resulting* text, not one per keystroke.
// - The script auto-disables if it can't reach chrome.runtime (page reloaded,
//   extension updated, etc.) — silent failure is preferred over noisy errors.

(() => {
  if (window.__sopRecorderInjected) return;
  window.__sopRecorderInjected = true;

  const RECORD_FLAG_KEY = "sop_recording_active";
  // null = "we haven't read the flag yet" — events get queued, not dropped.
  // Once the storage callback resolves we either flush (if recording) or drop.
  // Critical on iframe-heavy pages (SAP Workzone, Salesforce) where each
  // frame's content script races user clicks against the async storage read.
  let recording = null;
  const pending = [];
  const PENDING_CAP = 200; // safety: don't grow unbounded if storage never answers

  // Tiny breadcrumb so you can confirm in DevTools (F12 → Console) that the
  // listener is alive on a given page. If you record a workflow and don't
  // see this line, the script wasn't injected on that tab.
  console.debug("[SOP Recorder] content script ready on", location.href);

  function dispatch(payload) {
    try {
      chrome.runtime.sendMessage({
        type: "EVENT",
        event: {
          ...payload,
          frame_url: location.href,
          is_top_frame: window === window.top,
          ts_client: Date.now(),
        },
      });
    } catch (_) {
      // Extension reloaded or context invalidated — stop trying.
      recording = false;
    }
  }

  function flushPending() {
    if (recording === true) {
      for (const p of pending) dispatch(p);
    }
    pending.length = 0;
  }

  // ── Mirror the recording flag from chrome.storage.session ──
  function syncFlag() {
    try {
      chrome.storage.session.get(RECORD_FLAG_KEY, (v) => {
        recording = !!(v && v[RECORD_FLAG_KEY]);
        flushPending();
      });
    } catch (_) {
      recording = false;
      pending.length = 0;
    }
  }
  syncFlag();
  try {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "session" && RECORD_FLAG_KEY in changes) {
        recording = !!changes[RECORD_FLAG_KEY].newValue;
        flushPending();
      }
    });
  } catch (_) {}

  // ── Selector helpers ────────────────────────────────────────────────
  function cssSelector(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) {
        const cls = Array.from(cur.classList).slice(0, 2).map(CSS.escape).join(".");
        if (cls) part += `.${cls}`;
      }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(
          (c) => c.tagName === cur.tagName,
        );
        if (sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(" > ");
  }

  function xpathOf(el) {
    if (!el || el.nodeType !== 1) return "";
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      let idx = 1;
      let sib = cur.previousElementSibling;
      while (sib) {
        if (sib.tagName === cur.tagName) idx++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(`${cur.tagName.toLowerCase()}[${idx}]`);
      cur = cur.parentElement;
    }
    return "/" + parts.join("/");
  }

  // ── Sensitive-field detection ───────────────────────────────────────
  // Heuristic. Catches password / credit-card / OTP / SSN / token inputs
  // before their value ever leaves the page. False positives are fine
  // (we redact a benign field), false negatives are the risk we minimise.
  const SENSITIVE_AUTOCOMPLETE = new Set([
    "current-password", "new-password", "one-time-code",
    "cc-number", "cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year",
    "cc-name",
  ]);
  const SENSITIVE_NAME_RE = /pass(word|wd|code)?|pwd|secret|token|otp|cvv|cvc|ssn|credit.?card|cc.?num/i;

  function isSensitive(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName ? el.tagName.toLowerCase() : "";
    if (tag === "input" && (el.type || "").toLowerCase() === "password") return true;
    const get = (a) => (el.getAttribute ? (el.getAttribute(a) || "") : "");
    const ac = get("autocomplete").trim().toLowerCase();
    if (ac && SENSITIVE_AUTOCOMPLETE.has(ac)) return true;
    const probe = `${get("name")} ${get("id")} ${get("aria-label")} ${get("placeholder")}`;
    if (SENSITIVE_NAME_RE.test(probe)) return true;
    return false;
  }

  // ── Ancestor-label inference ────────────────────────────────────────
  // The literal click target is often a bare <img>/<svg>/<span> with no
  // semantic info. Real label lives on an ancestor (e.g. <a aria-label="…">
  // wrapping a tile icon, or a sibling caption span). Walk up to find it.
  const ANCESTOR_WALK_LIMIT = 8;
  const _trim = (s) => (s == null ? "" : String(s)).trim();

  // Generic phrases SAP/Salesforce/Workzone put on wrapper anchors. They
  // win the priority chain but are useless as labels — skip them and keep
  // walking so we can find the actual tile name.
  const GENERIC_LABELS = new Set([
    "click to open", "open", "open link", "link",
    "click here", "click", "more", "menu", "button",
    "image", "icon", "see more", "view", "view more",
  ]);
  const _isGeneric = (s) => GENERIC_LABELS.has(s.toLowerCase());

  function inferLabel(el) {
    // Walk ancestors looking for explicit semantic labels only — never fall
    // back to ancestor innerText, because innerText pulls in ALL descendant
    // text (including sibling widgets like "No images yet" empty-state
    // placeholders that have nothing to do with the click target).
    //
    // Priority on each level:
    //   1. IMG alt (highest signal: built specifically for icons)
    //   2. aria-label
    //   3. aria-labelledby → resolved text
    //   4. title
    //   5. innerText of <a>/<button>, only if short (<= 80 chars)
    //
    // Generic wrappers ("Click to open", "Open", etc.) are skipped at first
    // sight, but remembered as a last-resort fallback if nothing else
    // surfaces.
    let cur = el, depth = 0;
    let genericFallback = "";
    while (cur && cur.nodeType === 1 && depth < ANCESTOR_WALK_LIMIT) {
      if (cur.tagName === "IMG") {
        const alt = _trim(cur.getAttribute("alt"));
        if (alt && !_isGeneric(alt)) return alt.slice(0, 200);
      }
      const aria = cur.getAttribute && _trim(cur.getAttribute("aria-label"));
      if (aria) {
        if (!_isGeneric(aria)) return aria.slice(0, 200);
        if (!genericFallback) genericFallback = aria;
      }
      const lblBy = cur.getAttribute && _trim(cur.getAttribute("aria-labelledby"));
      if (lblBy) {
        const id = lblBy.split(/\s+/)[0];
        const ref = id ? cur.ownerDocument && cur.ownerDocument.getElementById(id) : null;
        const t = ref ? _trim(ref.innerText || ref.textContent) : "";
        if (t && !_isGeneric(t)) return t.slice(0, 200);
      }
      const title = cur.getAttribute && _trim(cur.getAttribute("title"));
      if (title && !_isGeneric(title)) return title.slice(0, 200);
      if (cur.tagName === "A" || cur.tagName === "BUTTON") {
        const t = _trim(cur.innerText || cur.textContent);
        if (t && t.length <= 80 && !_isGeneric(t)) return t.slice(0, 200);
      }
      cur = cur.parentElement;
      depth++;
    }
    return genericFallback ? genericFallback.slice(0, 200) : "";
  }

  function closestAnchor(el) {
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < ANCESTOR_WALK_LIMIT) {
      if (cur.tagName === "A" && cur.getAttribute) {
        return {
          href: cur.getAttribute("href") || "",
          text: _trim(cur.innerText || cur.textContent).slice(0, 200),
        };
      }
      cur = cur.parentElement;
      depth++;
    }
    return null;
  }

  function nearestRole(el) {
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < ANCESTOR_WALK_LIMIT) {
      const r = cur.getAttribute && _trim(cur.getAttribute("role"));
      if (r) return r;
      cur = cur.parentElement;
      depth++;
    }
    return "";
  }

  function describeTarget(el) {
    if (!el || el.nodeType !== 1) return {};
    const rect = el.getBoundingClientRect();
    const sensitive = isSensitive(el);
    const rawValue = "value" in el ? String(el.value || "") : "";
    const ownText = _trim(el.innerText || el.textContent).slice(0, 200);
    const ownAria = el.getAttribute ? _trim(el.getAttribute("aria-label")) : "";
    const ownRole = el.getAttribute ? _trim(el.getAttribute("role")) : "";
    const ownAlt = el.tagName === "IMG" && el.getAttribute ? _trim(el.getAttribute("alt")) : "";
    // Inferred label only kicks in when the element itself has no text/aria.
    const inferred = (ownText || ownAria || ownAlt) ? "" : inferLabel(el);
    const anchor = closestAnchor(el);
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : "",
      text: ownText,
      role: ownRole || nearestRole(el),
      aria_label: ownAria,
      alt: ownAlt || undefined,
      inferred_label: inferred || undefined,
      link_href: anchor ? anchor.href : undefined,
      link_text: anchor && anchor.text ? anchor.text : undefined,
      placeholder: el.getAttribute && (el.getAttribute("placeholder") || ""),
      value: sensitive ? "" : rawValue.slice(0, 500),
      value_length: sensitive ? rawValue.length : undefined,
      is_sensitive: sensitive || undefined,
      input_type: el.getAttribute && (el.getAttribute("type") || ""),
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }

  function targetOf(e) {
    const path = typeof e.composedPath === "function" ? e.composedPath() : [];
    return path.find((n) => n && n.nodeType === 1) || e.target;
  }

  function viewport() {
    return {
      w: window.innerWidth,
      h: window.innerHeight,
      scroll_x: window.scrollX || window.pageXOffset || 0,
      scroll_y: window.scrollY || window.pageYOffset || 0,
    };
  }

  function send(payload) {
    if (recording === false) return;
    if (recording === null) {
      // Queue while the storage flag read is still in flight.
      if (pending.length < PENDING_CAP) pending.push(payload);
      return;
    }
    dispatch(payload);
  }

  // ── Listeners ───────────────────────────────────────────────────────
  document.addEventListener(
    "click",
    (e) => {
      const t = targetOf(e);
      send({
        kind: "click",
        x: e.clientX,
        y: e.clientY,
        button: e.button === 2 ? "right" : e.button === 1 ? "middle" : "left",
        selector: cssSelector(t),
        xpath: xpathOf(t),
        target: describeTarget(t),
        viewport: viewport(),
      });
    },
    { capture: true, passive: true },
  );

  // Per-element input debounce: emit the resulting text once typing pauses.
  // For sensitive fields (password / cc / otp / etc.) we redact the value
  // at source — the secret never leaves the page.
  const inputTimers = new WeakMap();
  document.addEventListener(
    "input",
    (e) => {
      const t = targetOf(e);
      if (!t) return;
      const prev = inputTimers.get(t);
      if (prev) clearTimeout(prev);
      const timer = setTimeout(() => {
        inputTimers.delete(t);
        const sensitive = isSensitive(t);
        const rawValue = "value" in t ? String(t.value || "") : (t.textContent || "");
        send({
          kind: "input",
          selector: cssSelector(t),
          xpath: xpathOf(t),
          value: sensitive ? "" : rawValue,
          value_length: sensitive ? rawValue.length : undefined,
          is_sensitive: sensitive || undefined,
          input_type: t.getAttribute ? t.getAttribute("type") || "" : "",
          target: describeTarget(t),
          viewport: viewport(),
        });
      }, 400);
      inputTimers.set(t, timer);
    },
    { capture: true, passive: true },
  );

  // Special keys only — typing into fields is captured by `input`.
  const SPECIAL_KEYS = new Set([
    "Enter", "Tab", "Escape", "Backspace", "Delete",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
  ]);

  document.addEventListener(
    "keydown",
    (e) => {
      const isModifierCombo = e.ctrlKey || e.metaKey || e.altKey;
      if (!SPECIAL_KEYS.has(e.key) && !isModifierCombo) return;
      const t = targetOf(e);
      const mods = [];
      if (e.ctrlKey) mods.push("ctrl");
      if (e.metaKey) mods.push("meta");
      if (e.altKey) mods.push("alt");
      if (e.shiftKey) mods.push("shift");
      send({
        kind: "key",
        key: e.key,
        code: e.code,
        modifiers: mods,
        selector: cssSelector(t),
        xpath: xpathOf(t),
        target: describeTarget(t),
        viewport: viewport(),
      });
    },
    { capture: true, passive: true },
  );

  // Scroll: debounce and emit the resting position once scrolling stops.
  let scrollTimer = null;
  window.addEventListener(
    "scroll",
    () => {
      if (scrollTimer) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        scrollTimer = null;
        send({
          kind: "scroll",
          scroll_x: window.scrollX,
          scroll_y: window.scrollY,
          viewport: viewport(),
        });
      }, 600);
    },
    { capture: true, passive: true },
  );
})();
