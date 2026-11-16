/* The candle's voice. Once it is lit, the page seals their words for the owner's Mac
   (CandleSeal), hands them to the relay named in the button's URL, and waits for Tilia's
   two lines: `now`, spelled in the smoke at once, and `later`, kept for blow-out.
     POST <relay>/v1/ask/<mac>            {"entry", "sealed"} -> {"ask_id"}
     GET  <relay>/v1/answer/<ask_id>?wait  200 {"sealed"} | {} (no answer), 204 (not yet), 404 (gone)
   It waits 30 s at most, then settles with nothing: there is never a stand-in line.
   No relay params (an old button, a plain browser, no relay set up): no ask at all.
   fetch and the clock can be passed in, so the Node tests wait without waiting. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./candle-seal.js"), root);
  } else {
    root.CandleVoice = factory(root.CandleSeal, root);
  }
})(typeof self !== "undefined" ? self : this, function (Seal, root) {
  "use strict";

  var WAIT_MS = 30000;                    // the page gives up after this long
  var POLL_S = 25;                        // the relay holds an answer poll at most this long
  var MAC = /^[A-Za-z0-9_-]{6,32}$/;
  var KEY = /^[A-Za-z0-9_-]{8,2048}$/;    // the Mac's public key, SPKI, base64url
  var ENTRY = /^[A-Za-z0-9_-]{12}$/;
  var ASK_ID = /^[A-Za-z0-9_-]{1,128}$/;
  var LOCAL = { "127.0.0.1": true, "localhost": true };  // http only here (R7); the CSP allows no more

  /* The relay's base URL without a trailing slash: https, or http on this machine only;
     no credentials, query or fragment. null for anything else. */
  function relayBase(text) {
    if (typeof text !== "string" || !text) return null;
    var u;
    try { u = new URL(text); } catch (e) { return null; }
    if (u.username || u.password || u.search || u.hash || /[?#]/.test(text)) return null;
    if (!(u.protocol === "https:" || (u.protocol === "http:" && LOCAL[u.hostname]))) return null;
    return (u.origin + u.pathname).replace(/\/+$/, "");
  }

  /* {relay, mac, k} from the page's query (a string or URLSearchParams): all three valid,
     or null and the candle keeps quiet. */
  function params(search) {
    var q;
    try {
      q = typeof URLSearchParams !== "undefined" && search instanceof URLSearchParams ? search
        : new URLSearchParams(typeof search === "string" ? search : "");
    } catch (e) { return null; }
    var relay = relayBase(q.get("relay")), mac = q.get("mac") || "", k = q.get("k") || "";
    if (!relay || !MAC.test(mac) || !KEY.test(k)) return null;
    return { relay: relay, mac: mac, k: k };
  }

  /* Whether to ask at all (R8): inside Telegram, or with a relay on this machine (a dev
     run in a desktop browser). A plain browser with a real relay never asks. */
  function mayAsk(p, inTelegram) {
    if (!p || !p.relay) return false;
    if (inTelegram) return true;
    try { return !!LOCAL[new URL(p.relay).hostname]; } catch (e) { return false; }
  }

  /* A lit candle reopened without Tilia's later line asks again (closed while the smoke
     waited); the Mac re-sends lines it already has without a second model call. */
  function needsAsk(s) {
    return !!s && s.state === "lit" && !s.later;
  }

  /* Whether Tilia's `now` line may still be spelled (R9): it belongs near lighting, so a
     reopen more than 10 minutes after litAt still asks (for `later`, kept for blow-out) but
     never speaks `now` or marks it spelled. */
  var NOW_MS = 10 * 60 * 1000;
  function speaksNow(litAt, at) {
    return typeof litAt === "number" && isFinite(litAt) && typeof at === "number" &&
      at - litAt <= NOW_MS;
  }

  /* How long the smoke holds a line before it drifts away: long enough to read. */
  function holdMs(line) {
    var n = typeof line === "string" ? Array.from(line).length : 0;
    return Math.min(6000, 2500 + 60 * n);
  }

  function isLine(v) { return typeof v === "string" && /\S/.test(v); }

  /* Tilia's {now, later} for this candle, or null: no params, the relay down or slow, the
     Mac asleep, a refused or expired ask, or anything that won't open. Never rejects. */
  function ask(o) {
    o = o || {};
    var fetchFn = o.fetch || (root && typeof root.fetch === "function" ? root.fetch.bind(root) : null);
    var clock = typeof o.now === "function" ? o.now : Date.now;
    var budget = o.budgetMs > 0 ? o.budgetMs : WAIT_MS;
    var base = relayBase(o.relay);
    if (!Seal || !fetchFn || !base || !MAC.test(o.mac || "") || !KEY.test(o.k || "") || !ENTRY.test(o.id || "")) {
      return Promise.resolve(null);
    }
    var start = clock(), key = null, over = false, timer = null;
    var ctl = typeof AbortController === "function" ? new AbortController() : null;

    function left() { return budget - (clock() - start); }
    function call(url, init) {
      init.credentials = "omit"; init.cache = "no-store"; init.referrerPolicy = "no-referrer";
      if (ctl) init.signal = ctl.signal;
      return fetchFn(url, init);
    }
    function pause(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
    function opened(lines) {
      return lines && isLine(lines.now) && isLine(lines.later) ? { now: lines.now, later: lines.later } : null;
    }
    function poll(id) {
      var ms = left();
      if (over || ms < 1000) return null;
      var wait = Math.min(POLL_S, Math.floor(ms / 1000)), t = clock();
      return call(base + "/v1/answer/" + encodeURIComponent(id) + "?wait=" + wait, { method: "GET" }).then(function (r) {
        if (r.status === 200) {
          return r.json().then(function (b) {
            return b && typeof b.sealed === "string" ? Seal.openAnswer(key, b.sealed).then(opened) : null;
          });
        }
        var again = r.status === 204 || r.status === 429 || r.status >= 500;
        if (!again) return null;                                    // 404: expired or the relay restarted
        return r.status === 204 && clock() - t >= 1000 ? poll(id) : pause(1000).then(function () { return poll(id); });
      });
    }

    var run = Promise.resolve().then(function () {
      return Seal.sealAsk(o.k, typeof o.intention === "string" ? o.intention : "");
    }).then(function (s) {
      key = s.key;
      return call(base + "/v1/ask/" + encodeURIComponent(o.mac), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry: o.id, sealed: s.sealed }),
      });
    }).then(function (r) {
      if (!r || r.status !== 200) return null;
      return r.json().then(function (b) {
        return b && typeof b.ask_id === "string" && ASK_ID.test(b.ask_id) ? poll(b.ask_id) : null;
      });
    }).catch(function () { return null; });

    // a relay that never answers at all still can't hold the page past the budget
    var expired = new Promise(function (r) { timer = setTimeout(function () { r(null); }, budget + 500); });
    return Promise.race([run, expired]).then(function (v) {
      over = true;
      clearTimeout(timer);
      if (ctl) { try { ctl.abort(); } catch (e) { /* already done */ } }
      return v || null;
    });
  }

  return {
    WAIT_MS: WAIT_MS,
    params: params,
    mayAsk: mayAsk,
    NOW_MS: NOW_MS,
    needsAsk: needsAsk,
    speaksNow: speaksNow,
    holdMs: holdMs,
    ask: ask,
  };
});
