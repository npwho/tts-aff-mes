// Service worker: owns the WebSocket connection to the native automation tool.
// This file never touches page DOM and never dispatches input events - it is
// purely a message relay between content.js (in the target tab) and the
// native tool's WebSocket server.

const WS_URL = "ws://127.0.0.1:8765";
const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 2000;

let socket = null;
let reconnectDelayMs = RECONNECT_MIN_MS;
let activeTabId = null;

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
    reconnectDelayMs = RECONNECT_MIN_MS;
    sendHelloForActiveTab();
  });

  socket.addEventListener("message", (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    // The heartbeat only tests whether this background worker's socket is
    // alive, not whether any particular tab's content script is - answer it
    // directly instead of forwarding to a tab (which may not exist, may be
    // the wrong tab, or may just never reply, silently breaking every
    // heartbeat and making native think the connection died constantly).
    if (msg.type === "ping") {
      sendToNative({ type: "pong" });
      return;
    }
    routeToContentScript(msg);
  });

  socket.addEventListener("close", scheduleReconnect);
  socket.addEventListener("error", () => {
    try {
      socket.close();
    } catch (e) {
      /* noop */
    }
  });
}

function scheduleReconnect() {
  setTimeout(connect, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 1.5, RECONNECT_MAX_MS);
}

function sendToNative(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  }
}

function routeToContentScript(msg) {
  const tabId = activeTabId;
  if (tabId == null) return;
  chrome.tabs.sendMessage(tabId, msg).catch(() => {
    // Content script not ready (e.g. mid-navigation) - drop silently, it will
    // re-send `hello` once it (re)injects, which pulls it back into sync.
  });
}

async function sendHelloForActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tabs.length === 0) return;
  activeTabId = tabs[0].id;
  sendToNative({
    type: "hello",
    extVersion: chrome.runtime.getManifest().version,
    tabId: activeTabId,
    url: tabs[0].url,
  });
}

// Relay messages coming up from content.js (real observed events, locate
// results, etc.) straight to the native tool.
chrome.runtime.onMessage.addListener((message, sender) => {
  if (sender.tab) {
    activeTabId = sender.tab.id;
  }
  sendToNative(message);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (tabId === activeTabId && changeInfo.status === "loading") {
    // SPA nav / reload - content script will re-send hello once re-injected.
  }
});

connect();
// Manifest V3 service workers can be suspended for inactivity; re-establish
// the socket whenever the worker wakes back up.
chrome.runtime.onStartup?.addListener(connect);
self.addEventListener("activate", connect);
