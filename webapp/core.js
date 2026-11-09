/* Tilia worksheet core: pure functions shared by the page and the Node tests.
   Nothing here touches the DOM, the network or storage. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.TiliaCore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var PAYLOAD_MAX = 4096;
  var WORDING_MAX = 200;

  function intOrNull(s) {
    return s !== null && /^\d+$/.test(s) ? parseInt(s, 10) : null;
  }

  function decodeWording(raw) {
    if (!raw) return null;
    try {
      var b64 = raw.replace(/-/g, "+").replace(/_/g, "/");
      while (b64.length % 4) b64 += "=";
      var bin = atob(b64);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var obj = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
      return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : null;
    } catch (e) {
      return null;
    }
  }

  /* The launch URL: non-personal values in the query, personalized wording in the
     fragment key `tw`. Telegram adds its own tgWebApp… keys to the same fragment. */
  function parseLaunch(href) {
    var url = new URL(href);
    var q = url.searchParams;
    var frag = new URLSearchParams(url.hash.replace(/^#/, ""));
    var card = q.get("card");
    // A spread's cards ride dot-joined in the one `card` key; `card` stays the first.
    var cards = card ? card.split(".") : [];
    return {
      type: q.get("t"),
      v: intOrNull(q.get("v")),
      id: q.get("id"),
      card: card === null ? null : cards[0] || "",
      cards: cards,
      seed: intOrNull(q.get("seed")),
      lang: q.get("lang") || "en",
      diag: q.get("diag") === "1",
      wording: decodeWording(frag.get("tw")),
    };
  }

  function cut(s) {
    return Array.from(s.trim()).slice(0, WORDING_MAX).join("");
  }

  /* A copy of the sheet with Tilia's wording: the pitch, and the prompt of personal
     cards only. Ids, kinds, chips and limits never change. */
  function applyWording(sheet, wording) {
    var out = JSON.parse(JSON.stringify(sheet));
    if (!wording || typeof wording !== "object") return out;
    if (typeof wording.pitch === "string" && wording.pitch.trim()) {
      out.pitch = cut(wording.pitch);
      out.cards.forEach(function (c) {
        if (c.kind === "pitch") c.prompt = out.pitch;
      });
    }
    var prompts = wording.prompts;
    if (prompts && typeof prompts === "object") {
      out.cards.forEach(function (c) {
        var p = Object.prototype.hasOwnProperty.call(prompts, c.id) ? prompts[c.id] : null;
        if (c.personal === true && typeof p === "string" && p.trim()) c.prompt = cut(p);
      });
    }
    return out;
  }

  function buildPayload(launch, answers) {
    return JSON.stringify({ v: 1, type: launch.type, id: launch.id, answers: answers });
  }

  function byteLength(str) {
    return new TextEncoder().encode(str).length;
  }

  function remaining(launch, answers) {
    return PAYLOAD_MAX - byteLength(buildPayload(launch, answers));
  }

  function draftKey(id) {
    return "d_" + id;
  }

  /* Drafts carry the time they were saved: the newer of the cloud and local copies wins,
     and any draft older than an open entry's 7 days is deleted. */
  var DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

  function makeDraft(at, answers, now) {
    return JSON.stringify({ t: now, i: at, a: answers });
  }

  function readDraft(raw) {
    if (typeof raw !== "string" || !raw) return null;
    try {
      var d = JSON.parse(raw);
      return d && typeof d === "object" && typeof d.t === "number" && isFinite(d.t)
        && Array.isArray(d.a) ? d : null;
    } catch (e) {
      return null;
    }
  }

  function newestDraft(a, b) {
    var da = readDraft(a), db = readDraft(b);
    if (!da) return db;
    if (!db) return da;
    return db.t > da.t ? db : da;
  }

  function staleDrafts(items, now, keep) {
    return Object.keys(items).filter(function (k) {
      if (k.slice(0, 2) !== "d_" || k === keep) return false;
      var d = readDraft(items[k]);
      return !d || now - d.t > DRAFT_TTL_MS;
    });
  }

  return {
    PAYLOAD_MAX: PAYLOAD_MAX,
    WORDING_MAX: WORDING_MAX,
    parseLaunch: parseLaunch,
    applyWording: applyWording,
    buildPayload: buildPayload,
    byteLength: byteLength,
    remaining: remaining,
    draftKey: draftKey,
    DRAFT_TTL_MS: DRAFT_TTL_MS,
    makeDraft: makeDraft,
    readDraft: readDraft,
    newestDraft: newestDraft,
    staleDrafts: staleDrafts,
  };
});
