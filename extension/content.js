// Content script: READ-ONLY DOM observation only.
//
// Hard rule: this file must never call element.click(), .dispatchEvent(),
// set .value, or call .focus() on a page element, and must never construct
// synthetic MouseEvent/KeyboardEvent/InputEvent objects. All real interaction
// with the page happens via genuine OS-level input performed by the native
// tool; this script only reports what it observes (element existence,
// position, text) and passively listens to real, user-driven events.

let mode = "idle"; // idle | calibrating | recording | replaying
let calibrationMarkers = [];
let captchaObserver = null;

function send(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

function sendHello() {
  send({ type: "hello", extVersion: "content", tabId: null, url: location.href });
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

// ---- Calibration ------------------------------------------------------------

function handleCalibrationRequestPoint(msg) {
  const marker = document.createElement("div");
  marker.setAttribute("data-tts-aff-mes-calibration-marker", "true");
  marker.style.cssText = [
    "position:fixed",
    `left:${msg.viewportX}px`,
    `top:${msg.viewportY}px`,
    "width:16px",
    "height:16px",
    "border-radius:50%",
    "background:red",
    "border:2px solid white",
    "z-index:2147483647",
    "pointer-events:none",
  ].join(";");
  document.documentElement.appendChild(marker);
  calibrationMarkers.push(marker);

  send({
    type: "calibration_marker_ready",
    corrId: msg.corrId,
    pointId: msg.pointId,
    viewportX: msg.viewportX,
    viewportY: msg.viewportY,
    windowInnerWidth: window.innerWidth,
    windowInnerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio,
    screenX: window.screenX,
    screenY: window.screenY,
  });
}

function handleCalibrationComplete() {
  calibrationMarkers.forEach((m) => m.remove());
  calibrationMarkers = [];
}

// ---- Recording ---------------------------------------------------------------

function recordingClickHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  send({
    type: "record_event",
    eventType: "click",
    selector: buildSelectorDescriptor(el),
    rectViewport: rectOf(el),
    timestamp: Date.now(),
  });
}

function recordingFocusHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  send({
    type: "record_event",
    eventType: "focus",
    selector: buildSelectorDescriptor(el),
    rectViewport: rectOf(el),
    timestamp: Date.now(),
  });
}

function recordingPasteHandler(event) {
  const el = event.target;
  if (!(el instanceof Element)) return;
  send({
    type: "record_event",
    eventType: "paste",
    selector: buildSelectorDescriptor(el),
    rectViewport: rectOf(el),
    timestamp: Date.now(),
  });
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

// ---- Replay: locate / search-result / text readback / send verify / reset ---

function handleReplayLocate(msg) {
  const { el, matchedBy } = resolveSelectorDescriptor(msg.selectorDescriptor);
  if (!el) {
    send({ type: "replay_locate_result", corrId: msg.corrId, stepId: msg.stepId, found: false, reason: "no_dom_match" });
    return;
  }
  send({
    type: "replay_locate_result",
    corrId: msg.corrId,
    stepId: msg.stepId,
    found: true,
    rectViewport: rectOf(el),
    confidence: matchedBy === msg.selectorDescriptor.strategy ? "exact" : "fallback",
    matchedBy,
  });
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

function handleReplayLocateSearchResult(msg) {
  const emptyState = document.querySelector('[class*="empty" i], [class*="no-result" i]');
  const { rows, exact } = findSearchResultRow(msg.username);

  if (rows.length === 0) {
    send({ type: "search_result_status", corrId: msg.corrId, status: emptyState ? "not_found" : "not_found" });
    return;
  }
  if (rows.length === 1) {
    send({ type: "search_result_status", corrId: msg.corrId, status: "found", rectViewport: rectOf(rows[0]) });
    return;
  }
  const exactRow = exact ? rows[0] : null;
  send({
    type: "search_result_status",
    corrId: msg.corrId,
    status: "ambiguous",
    count: rows.length,
    exactMatchRect: exactRow ? rectOf(exactRow) : null,
  });
}

function handleGetTextContent(msg) {
  const { el } = resolveSelectorDescriptor(msg.selectorDescriptor);
  if (!el) {
    send({ type: "text_content_result", corrId: msg.corrId, text: "", length: 0 });
    return;
  }
  const text = el.innerText !== undefined ? el.innerText : el.textContent || "";
  send({ type: "text_content_result", corrId: msg.corrId, text, length: text.length });
}

function handleSendVerify(msg) {
  const prefix = (msg.expectedTextPrefix || "").trim();
  const bubbles = Array.from(document.querySelectorAll('[class*="message" i][class*="outgoing" i], [class*="message" i][class*="sent" i]'));
  const sent = bubbles.some((b) => (b.textContent || "").trim().startsWith(prefix));
  send({ type: "send_verify_result", corrId: msg.corrId, sent });
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
    case "calibration_request_point":
      handleCalibrationRequestPoint(msg);
      break;
    case "calibration_complete":
      handleCalibrationComplete();
      break;
    case "start_recording":
      startRecording();
      break;
    case "stop_recording":
      stopRecording();
      break;
    case "replay_locate":
      handleReplayLocate(msg);
      break;
    case "replay_locate_search_result":
      handleReplayLocateSearchResult(msg);
      break;
    case "get_text_content":
      handleGetTextContent(msg);
      break;
    case "send_verify":
      handleSendVerify(msg);
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
