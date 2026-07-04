// Content script: READ-ONLY DOM observation only.
//
// Hard rule: this file must never call element.click(), .dispatchEvent(),
// set .value, or call .focus() on a page element, and must never construct
// synthetic MouseEvent/KeyboardEvent/InputEvent objects. All real interaction
// with the page happens via genuine OS-level input performed by the native
// tool; this script only reports what it observes (element existence,
// position, text) and passively listens to real, user-driven events.
//
// There is no selector matching here at all, and no trusting of
// self-reported window geometry either (window.screenX/outerWidth/
// innerWidth/devicePixelRatio were observed to be internally inconsistent
// on Windows Server / Remote Desktop - innerWidth larger than outerWidth,
// which is physically impossible). The native tool instead determines real
// screen coordinates by taking an actual screenshot and finding two
// distinctly-colored marker elements this script places on request - pure
// pixel ground truth, no metric-trusting involved. This script's only
// remaining jobs: place/remove those markers, report whether a searched
// username actually exists, read back text at a point for paste/send
// verification, and watch for captchas/open dialogs.

let mode = "idle"; // idle | recording | replaying
let captchaObserver = null;
let calibrationMarkers = [];

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

// ---- Calibration markers (pixel ground truth) -------------------------------

function placeMarker(x, y, size, rgb) {
  const marker = document.createElement("div");
  marker.setAttribute("data-tts-aff-mes-marker", "true");
  marker.style.cssText = [
    "position:fixed",
    `left:${x}px`,
    `top:${y}px`,
    `width:${size}px`,
    `height:${size}px`,
    `background:rgb(${rgb[0]},${rgb[1]},${rgb[2]})`,
    "z-index:2147483647",
    "pointer-events:none",
    "border:none",
    "border-radius:0",
    "box-shadow:none",
    "filter:none",
    "opacity:1",
  ].join(";");
  document.documentElement.appendChild(marker);
  calibrationMarkers.push(marker);
}

function handlePlaceMarkers(msg) {
  removeMarkers();
  for (const m of msg.markers) {
    placeMarker(m.x, m.y, m.size, m.rgb);
  }
  send({ type: "markers_placed", corrId: msg.corrId });
}

function removeMarkers() {
  calibrationMarkers.forEach((m) => m.remove());
  calibrationMarkers = [];
}

function handleRemoveMarkers() {
  removeMarkers();
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
// Guided capture: native tells the user (via its own GUI) which of the 5
// fixed points to click next. This script just reports every real click's
// rect while recording is active - native assigns each one to the current
// step in order. No focus/paste listening, no text-based heuristics, no
// selector - just "here's where that click landed."

function recordingClickHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  send({
    type: "record_event",
    eventType: "click",
    rectViewport: rectOf(el),
    timestamp: Date.now(),
  });
}

function startRecording() {
  mode = "recording";
  document.addEventListener("click", recordingClickHandler, { capture: true });
}

function stopRecording() {
  mode = "idle";
  document.removeEventListener("click", recordingClickHandler, { capture: true });
}

// ---- Replay support: username existence / text-at-point / reset -----------

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
    case "place_calibration_markers":
      handlePlaceMarkers(msg);
      break;
    case "remove_calibration_markers":
      handleRemoveMarkers();
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
