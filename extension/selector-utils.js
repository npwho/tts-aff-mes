// Shared helpers for building and resolving resilient selector descriptors.
// Read-only: every function here only inspects the DOM, never mutates or
// dispatches events on page elements.

const HASH_LIKE_ID = /^[a-z0-9_-]{8,}$/i;
const PREFERRED_ATTRS = ["data-testid", "data-e2e", "aria-label", "data-qa", "title"];

function isHashLikeId(id) {
  if (!id) return true;
  // Reject ids that look auto-generated (long hex/alnum blobs) rather than
  // human-authored stable ids.
  return HASH_LIKE_ID.test(id) && /\d/.test(id) && /[a-z]/i.test(id) && id.length >= 10;
}

// Human-readable summary only (shown in the native GUI's recording log) -
// truncated to 2 classes per level for brevity. NOT used for matching.
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

// A real, queryable compound CSS descendant selector using the FULL class
// list at each ancestor level (e.g. "div.flex.items-center button.core-btn
// button.core-btn-text svg path"). Far more specific than matching one
// generic tag name at a time, which is what made the earlier structural
// fallback match essentially the first <div>/<button>/<svg> on the whole
// page instead of the recorded one.
function structuralSelector(el, maxDepth = 4) {
  const parts = [];
  let node = el;
  for (let i = 0; i < maxDepth && node && node.nodeType === 1; i++) {
    let part = node.tagName.toLowerCase();
    if (node.className && typeof node.className === "string") {
      const classes = node.className.trim().split(/\s+/).filter(Boolean);
      for (const c of classes) {
        try {
          part += `.${CSS.escape(c)}`;
        } catch (e) {
          /* skip unescapable class token */
        }
      }
    }
    parts.unshift(part);
    node = node.parentElement;
  }
  return parts.join(" ");
}

// Snapshot of the current browser window's OS-level geometry, used by the
// native tool to convert a viewport-relative rect into a real screen
// coordinate fresh on every single locate response - no separate
// calibration step, and no staleness if the window moves/resizes mid-run.
function windowGeometry() {
  return {
    screenX: window.screenX,
    screenY: window.screenY,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
  };
}

function rectOf(el) {
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}

function rectCenter(rect) {
  return { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 };
}

function rectDistance(a, b) {
  if (!a || !b) return Infinity;
  const ca = rectCenter(a);
  const cb = rectCenter(b);
  return Math.hypot(ca.x - cb.x, ca.y - cb.y);
}

function isVisible(el) {
  return !!(el.offsetParent || el.getClientRects().length);
}

// Pick the best of several candidate elements against an optional recorded
// hint rect (the element's position at record time). Prefers visible
// elements, then the one positioned closest to where it was originally
// recorded - a much better disambiguator than "whichever the browser
// happened to return first" when a compound selector still matches more
// than one element (e.g. several structurally-identical rows).
function pickBestCandidate(candidates, hintRect) {
  const visible = candidates.filter(isVisible);
  const pool = visible.length ? visible : candidates;
  if (pool.length <= 1) return pool[0] || null;
  if (!hintRect) return pool[0];
  let best = pool[0];
  let bestDist = rectDistance(rectOf(best), hintRect);
  for (const c of pool.slice(1)) {
    const d = rectDistance(rectOf(c), hintRect);
    if (d < bestDist) {
      best = c;
      bestDist = d;
    }
  }
  return best;
}

// Build a SelectorDescriptor for an element that was just observed via a
// real, user-driven event.
function buildSelectorDescriptor(el) {
  const hintRect = rectOf(el);
  const ancestorPath = shortAncestorPath(el);

  for (const attr of PREFERRED_ATTRS) {
    const val = el.getAttribute(attr);
    if (val) {
      return {
        strategy: attr,
        value: `[${attr}="${CSS.escape(val)}"]`,
        attributes: collectAttributes(el),
        textContent: (el.textContent || "").trim().slice(0, 120),
        ancestorPath,
        hintRect,
      };
    }
  }

  if (el.id && !isHashLikeId(el.id)) {
    return {
      strategy: "id",
      value: `#${CSS.escape(el.id)}`,
      attributes: collectAttributes(el),
      textContent: (el.textContent || "").trim().slice(0, 120),
      ancestorPath,
      hintRect,
    };
  }

  const text = (el.textContent || "").trim();
  if (text && text.length <= 60) {
    return {
      strategy: "text",
      value: text,
      attributes: collectAttributes(el),
      textContent: text,
      ancestorPath,
      hintRect,
    };
  }

  return {
    strategy: "structural",
    value: structuralSelector(el),
    attributes: collectAttributes(el),
    textContent: text.slice(0, 120),
    ancestorPath,
    hintRect,
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
      const candidates = Array.from(document.querySelectorAll(desc.value));
      const best = pickBestCandidate(candidates, desc.hintRect);
      if (best) return { el: best, matchedBy: desc.strategy };
    } catch (e) {
      /* fall through to fallback strategies */
    }
  }

  if (desc.textContent) {
    const candidates = Array.from(document.querySelectorAll("button, a, div[role], span")).filter(
      (c) => (c.textContent || "").trim() === desc.textContent.trim()
    );
    const best = pickBestCandidate(candidates, desc.hintRect);
    if (best) return { el: best, matchedBy: "text" };
  }

  if (desc.strategy === "structural" && desc.value) {
    try {
      const candidates = Array.from(document.querySelectorAll(desc.value));
      const best = pickBestCandidate(candidates, desc.hintRect);
      if (best) return { el: best, matchedBy: "structural" };
    } catch (e) {
      /* compound selector didn't parse (e.g. dynamic classes changed
         entirely) - nothing sensible left to fall back to. */
    }
  }

  return { el: null, matchedBy: null };
}
