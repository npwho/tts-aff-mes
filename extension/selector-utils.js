// Shared helpers for building and resolving resilient selector descriptors.
// Read-only: every function here only inspects the DOM, never mutates or
// dispatches events on page elements.

const HASH_LIKE_ID = /^[a-z0-9_-]{8,}$/i;
const PREFERRED_ATTRS = ["data-testid", "data-e2e", "aria-label", "data-qa"];

function isHashLikeId(id) {
  if (!id) return true;
  // Reject ids that look auto-generated (long hex/alnum blobs) rather than
  // human-authored stable ids.
  return HASH_LIKE_ID.test(id) && /\d/.test(id) && /[a-z]/i.test(id) && id.length >= 10;
}

function shortAncestorPath(el, maxDepth = 4) {
  const parts = [];
  let node = el;
  for (let i = 0; i < maxDepth && node && node.nodeType === 1; i++) {
    let part = node.tagName.toLowerCase();
    if (node.className && typeof node.className === "string") {
      const cls = node.className.trim().split(/\s+/).slice(0, 2).join(".");
      if (cls) part += `.${cls}`;
    }
    parts.unshift(part);
    node = node.parentElement;
  }
  return parts.join(" > ");
}

// Build a SelectorDescriptor for an element that was just observed via a
// real, user-driven event.
function buildSelectorDescriptor(el) {
  for (const attr of PREFERRED_ATTRS) {
    const val = el.getAttribute(attr);
    if (val) {
      return {
        strategy: attr,
        value: `[${attr}="${CSS.escape(val)}"]`,
        attributes: collectAttributes(el),
        textContent: (el.textContent || "").trim().slice(0, 120),
        ancestorPath: shortAncestorPath(el),
      };
    }
  }

  if (el.id && !isHashLikeId(el.id)) {
    return {
      strategy: "id",
      value: `#${CSS.escape(el.id)}`,
      attributes: collectAttributes(el),
      textContent: (el.textContent || "").trim().slice(0, 120),
      ancestorPath: shortAncestorPath(el),
    };
  }

  const text = (el.textContent || "").trim();
  if (text && text.length <= 60) {
    return {
      strategy: "text",
      value: text,
      attributes: collectAttributes(el),
      textContent: text,
      ancestorPath: shortAncestorPath(el),
    };
  }

  return {
    strategy: "structural",
    value: shortAncestorPath(el),
    attributes: collectAttributes(el),
    textContent: text.slice(0, 120),
    ancestorPath: shortAncestorPath(el),
  };
}

function collectAttributes(el) {
  const out = {};
  for (const attr of el.attributes || []) {
    out[attr.name] = attr.value;
  }
  return out;
}

// Resolve a previously-recorded SelectorDescriptor against the *current* live
// DOM. Tries strategies in the same preference order used at record time.
// Returns { el, matchedBy } or { el: null, matchedBy: null }.
function resolveSelectorDescriptor(desc) {
  if (PREFERRED_ATTRS.includes(desc.strategy) || desc.strategy === "id") {
    try {
      const el = document.querySelector(desc.value);
      if (el) return { el, matchedBy: desc.strategy };
    } catch (e) {
      /* fall through to fallback strategies */
    }
  }

  if (desc.textContent) {
    const candidates = Array.from(document.querySelectorAll("button, a, div[role], span"));
    const exact = candidates.find((c) => (c.textContent || "").trim() === desc.textContent.trim());
    if (exact) return { el: exact, matchedBy: "text" };
  }

  if (desc.ancestorPath) {
    const tagChain = desc.ancestorPath.split(" > ").map((p) => p.split(".")[0]);
    let scope = document;
    let found = null;
    for (const tag of tagChain) {
      const next = scope.querySelector(tag);
      if (!next) break;
      found = next;
      scope = next;
    }
    if (found) return { el: found, matchedBy: "structural" };
  }

  return { el: null, matchedBy: null };
}

function rectOf(el) {
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}
