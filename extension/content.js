// Content script: READ-ONLY DOM observation only.
//
// Hard rule: this file must never call element.click(), .dispatchEvent(),
// set .value, or call .focus() on a page element, and must never construct
// synthetic MouseEvent/KeyboardEvent/InputEvent objects. All real interaction
// with the page happens via genuine OS-level input performed by the native
// tool; this script only reports what it observes (element existence,
// position, text) and passively listens to real, user-driven events.
//
// There is no selector matching here at all: the native tool clicks fixed
// viewport positions recorded during the Record step (re-anchored live
// against the browser window's current on-screen geometry - see
// geometry.py). This script's only remaining jobs are: report that
// geometry, report whether a searched username actually exists, read back
// text at a point for paste/send verification, and watch for captchas/open
// dialogs. TikTok's generated/state-dependent class names made selector-based
// re-location unreliable in practice, so replay doesn't depend on it at all.

let mode = "idle"; // idle | recording | replaying
let captchaObserver = null;

function send(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

function sendHello() {
  send({ type: "hello", extVersion: "content", tabId: null, url: location.href });
}

function rectOf(el) {
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}

// Snapshot of the current browser window's OS-level geometry, used by the
// native tool to convert a recorded viewport-relative rect into a real
// screen coordinate fresh on every single click. That also means a window
// that gets moved mid-run is handled automatically.
function windowGeometry() {
  return {
    screenX: window.screenX,
    screenY: window.screenY,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
    screenWidth: window.screen.width,
    screenHeight: window.screen.height,
  };
}

// ---- Captcha watch (unsolicited, always on) --------------------------------

function startCaptchaWatch() {
  if (captchaObserver) return;
  const check = () => {
    const hit = document.querySelector(
      'iframe[src*="captcha" i], div[class*="captcha" i], div[id*="captcha" i]'
    );
    if (hit) {
      send({ type: "captcha_detected", detail: describeElement(hit) });
    }
  };
  captchaObserver = new MutationObserver(check);
  captchaObserver.observe(document.documentElement, { childList: true, subtree: true });
  check();
}

function describeElement(el) {
  return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${el.className ? "." + String(el.className).split(/\s+/).join(".") : ""}`;
}

// ---- Recording ---------------------------------------------------------------
//
// Only the rect (position/size) and a short text snapshot are recorded -
// never a CSS selector. Replay reuses the rect directly as a fixed click
// target; the text snapshot is only used to flag the "Chat" button, which
// only becomes visible/interactable after a genuine hover.

function recordEvent(eventType, el) {
  send({
    type: "record_event",
    eventType,
    rectViewport: rectOf(el),
    text: (el.textContent || "").trim().slice(0, 60),
    timestamp: Date.now(),
  });
}

function recordingClickHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  recordEvent("click", el);
}

function isFormLikeElement(el) {
  const tag = el.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea") return true;
  if (el.isContentEditable) return true;
  const role = el.getAttribute("role");
  return role === "textbox" || role === "searchbox";
}

function recordingFocusHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  // Clicking anywhere inside a scrollable/tabindex-able container (e.g. a
  // chat list panel) moves DOM focus to that container, not to the thing
  // you actually clicked. Only real form controls are meaningful "focus"
  // steps for replay.
  if (!isFormLikeElement(el)) return;
  recordEvent("focus", el);
}

function recordingPasteHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  recordEvent("paste", el);
}

function startRecording() {
  mode = "recording";
  document.addEventListener("click", recordingClickHandler, { capture: true });
  document.addEventListener("focus", recordingFocusHandler, { capture: true });
  document.addEventListener("paste", recordingPasteHandler, { capture: true });
}

function stopRecording() {
  mode = "idle";
  document.removeEventListener("click", recordingClickHandler, { capture: true });
  document.removeEventListener("focus", recordingFocusHandler, { capture: true });
  document.removeEventListener("paste", recordingPasteHandler, { capture: true });
}

// ---- Replay support: geometry / username existence / text-at-point / reset --

function handleGetWindowGeometry(msg) {
  send({ type: "window_geometry_result", corrId: msg.corrId, geometry: windowGeometry() });
}

function findSearchResultRow(username) {
  const rows = Array.from(
    document.querySelectorAll('[role="listitem"], li, div[class*="result" i], div[class*="row" i]')
  );
  const needle = username.trim().toLowerCase();
  const exact = rows.filter((r) => (r.textContent || "").trim().toLowerCase() === needle);
  if (exact.length === 1) return { rows: exact, exact: true };
  const partial = rows.filter((r) => (r.textContent || "").toLowerCase().includes(needle));
  return { rows: partial, exact: false };
}

function handleCheckUsernameExists(msg) {
  const { rows, exact } = findSearchResultRow(msg.username);
  if (rows.length === 0) {
    send({ type: "username_exists_result", corrId: msg.corrId, status: "not_found" });
    return;
  }
  if (rows.length === 1) {
    send({ type: "username_exists_result", corrId: msg.corrId, status: "found" });
    return;
  }
  send({
    type: "username_exists_result",
    corrId: msg.corrId,
    status: exact ? "found" : "ambiguous",
    count: rows.length,
  });
}

// Reads text at a fixed viewport point (the same point native is about to
// click, or just clicked) - used to verify a paste landed, and to verify a
// message was sent (input goes empty), without matching anything by
// selector.
function handleGetTextAtPoint(msg) {
  const el = document.elementFromPoint(msg.viewportX, msg.viewportY);
  if (!el) {
    send({ type: "text_at_point_result", corrId: msg.corrId, found: false, text: "" });
    return;
  }
  const text = "value" in el ? el.value : el.innerText !== undefined ? el.innerText : el.textContent || "";
  send({ type: "text_at_point_result", corrId: msg.corrId, found: true, text: text || "" });
}

function handleResetState(msg) {
  const dialogOpen = !!document.querySelector('[role="dialog"], [class*="modal" i]:not([style*="display: none"])');
  send({ type: "verify_reset", corrId: msg.corrId, dialogOpen });
}

// ---- Message router -----------------------------------------------------------

chrome.runtime.onMessage.addListener((msg) => {
  switch (msg.type) {
    case "hello_ack":
      mode = msg.mode || mode;
      break;
    case "start_recording":
      startRecording();
      break;
    case "stop_recording":
      stopRecording();
      break;
    case "get_window_geometry":
      handleGetWindowGeometry(msg);
      break;
    case "check_username_exists":
      handleCheckUsernameExists(msg);
      break;
    case "get_text_at_point":
      handleGetTextAtPoint(msg);
      break;
    case "reset_state":
      handleResetState(msg);
      break;
    default:
      break;
  }
});

startCaptchaWatch();
sendHello();

// Re-announce on SPA navigations (history API) since background.js can't
// observe those directly.
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    sendHello();
  }
}).observe(document, { childList: true, subtree: true });
