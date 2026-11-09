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
  var OPTION_MAX = 80;
  var STARTER_WORDS = 4;
  var ELLIPSIS = "\u2026";

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
    var options = wording.options;
    if (options && typeof options === "object") {
      out.cards.forEach(function (c, n) {
        // R25: only answer cards are tailored; a card's sentence starters stay generic.
        // D15: personal cards, and the answer card right after each drawn card (its taps
        // are written for that card; the Mac's entries.card_answer_ids).
        var drawn = n > 0 && out.cards[n - 1].kind === "reveal";
        if ((c.personal !== true && !drawn) || c.kind !== "text" || c.option_mode !== "answer") return;
        var o = Object.prototype.hasOwnProperty.call(options, c.id)
          ? cleanOptions(options[c.id], c.option_mode) : null;
        if (o) c.options = o;
      });
    }
    return out;
  }

  /* R24: tailored taps follow the Mac's rules (entries.clean_wording): 3-5 distinct
     one-line taps of up to 80 characters; on a starter card each ends with an ellipsis.
     Anything else keeps the sheet's own taps. */
  function cleanOptions(raw, mode) {
    if (!Array.isArray(raw) || raw.length < 3 || raw.length > 5) return null;
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var o = raw[i];
      if (typeof o !== "string" || /[\n\r\v\f\u0085\u2028\u2029]/.test(o)) return null;
      o = o.trim();
      if (mode === "starter" && o.slice(-3) === "...") o = o.slice(0, -3).trim() + ELLIPSIS;
      if (!o || Array.from(o).length > OPTION_MAX || out.indexOf(o) >= 0) return null;
      if (mode === "starter" && (o.slice(-1) !== ELLIPSIS || o.length < 2
          || o.slice(0, -1).trim().split(/\s+/).length > STARTER_WORDS)) return null;
      out.push(o);
    }
    return out;
  }

  /* What a tap puts in the box: an answer as it is; a starter without its ellipsis and
     with a space, so the cursor lands ready for their words. */
  function fillOf(card, option) {
    return card.option_mode === "starter"
      ? option.replace(new RegExp(ELLIPSIS + "$"), "").replace(/\s+$/, "") + " "
      : option;
  }

  // The tap the box still holds untouched, or null once they've written their own.
  function pickedOption(card, value) {
    var v = (value || "").trim();
    if (!v) return null;
    var opts = card.options || [];
    for (var i = 0; i < opts.length; i++) if (fillOf(card, opts[i]).trim() === v) return opts[i];
    return null;
  }

  /* The box after a tap: an empty box or an untouched tap is replaced (the same tap
     again clears it); words they typed themselves stay, and the tap is added after them. */
  function tapOption(card, current, option) {
    var cur = current || "";
    var picked = pickedOption(card, cur);
    if (picked === option) return "";
    if (!cur.trim() || picked !== null) return fillOf(card, option);
    if (/\n$/.test(cur)) return cur + fillOf(card, option);
    var base = cur.replace(/\s+$/, "");
    return base + (/[.!?\u2026]$/.test(base) ? " " : ". ") + fillOf(card, option);
  }

  /* R26: a saved draft back into the sheet's answers. Each answer goes to the card it was
     written for (drafts name their cards), or by position when the draft has the sheet's
     exact shape. A draft from before the sheet changed keeps its words anyway: whatever
     can't be placed goes into the empty text cards in order, the rest joined into the
     last one, cut to fit. Feelings and slider numbers are only kept where they pair. */
  function restoreAnswers(asked, d) {
    function empty(c) { return c.kind === "text" ? "" : c.kind === "chips" ? [] : null; }
    function fit(c, a) {
      if (c.kind === "text" && typeof a === "string") return a.slice(0, c.max_chars);
      if (c.kind === "chips" && Array.isArray(a)) {
        return a.filter(function (x) { return c.chips.indexOf(x) >= 0; });
      }
      if ((c.kind === "mood" || c.kind === "slider") && typeof a === "number"
          && a >= c.min && a <= c.max) return Math.round(a);
      return undefined;
    }
    var out = asked.map(empty);
    var left = [];
    var ids = Array.isArray(d.k) && d.k.length === d.a.length ? d.k : null;
    var same = !ids && d.a.length === asked.length;
    d.a.forEach(function (a, n) {
      var at = ids ? asked.findIndex(function (c) { return c.id === ids[n]; }) : same ? n : -1;
      var v = at >= 0 ? fit(asked[at], a) : undefined;
      if (v !== undefined) out[at] = v;
      else if (typeof a === "string" && a.trim()) left.push(a.trim());
      else if (Array.isArray(a) && a.length) {
        var words = a.filter(function (x) { return typeof x === "string" && x.trim(); });
        if (words.length) left.push(words.join(", "));
      }
    });
    var free = [];
    asked.forEach(function (c, n) { if (c.kind === "text" && !out[n]) free.push(n); });
    free.forEach(function (n, j) {
      if (!left.length) return;
      var text = j === free.length - 1 ? left.splice(0).join("\n") : left.shift();
      out[n] = text.slice(0, asked[n].max_chars).trim();
    });
    return out;
  }

  // The closing feeling card, when the opening one was answered.
  function moodPrompt(first) {
    return typeof first === "number" ? "You started at " + first + ". Where are you now?" : null;
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

  // `k` names each answer's card, so a draft still lands right if the sheet changes.
  function makeDraft(at, answers, now, ids) {
    var d = { t: now, i: at, a: answers };
    if (ids) d.k = ids;
    return JSON.stringify(d);
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
    OPTION_MAX: OPTION_MAX,
    cleanOptions: cleanOptions,
    fillOf: fillOf,
    pickedOption: pickedOption,
    tapOption: tapOption,
    moodPrompt: moodPrompt,
    restoreAnswers: restoreAnswers,
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
