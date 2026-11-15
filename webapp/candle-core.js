/* The candle's pure parts, shared by the page and the Node tests: how far it has burned,
   how calm the breathing sounds, the lit state kept between visits and what goes back to
   Tilia. Nothing here touches the DOM, the microphone or storage. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.CandleCore = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var BURN_MS = 4 * 3600 * 1000;       // a candle lasts about four hours, then it's an ember
  var SKEW_MS = 10 * 60 * 1000;         // a phone clock this far ahead still reads as "just lit"
  var KEEP_MS = 8 * 24 * 3600 * 1000;   // an entry can be answered for 7 days; then it's swept
  var INTENTION_MAX = 60;               // one short line, so the smoke can spell it back
  var CALM_WINDOW_S = 30;               // how much breathing the flame listens to
  var NEUTRAL = 0.5;                    // the flame as it always was: nothing to go on
  var OUT_LINE = "It's out.";
  var ASK_LINE = "How did it go?";
  var ID = /^[A-Za-z0-9_-]{12}$/;

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function isTime(v) { return typeof v === "number" && isFinite(v) && v > 0; }

  /* 0 (just lit) to 1 (burned down to an ember), from real time since lighting. */
  function burnFraction(litAt, now) {
    if (!isTime(litAt) || !isTime(now) || now < litAt - SKEW_MS) return 0;
    return clamp01((now - litAt) / BURN_MS);
  }

  function cleanIntention(s) {
    if (typeof s !== "string") return "";
    return Array.from(s.replace(/\s+/g, " ").trim()).slice(0, INTENTION_MAX).join("").trim();
  }

  function storeKey(id) { return "candle_" + id; }

  function litValue(litAt, intention) {
    return JSON.stringify({ lit_at: litAt, intention: cleanIntention(intention) });
  }

  /* Blown out and answered: kept in place of the lit record before sendData, so an answer
     is never lost if the page is swiped away; reopened, it offers "Send it again" (Tilia
     takes a second copy as already answered). */
  function pendingValue(litAt, intention, outAt, answer) {
    return JSON.stringify({ lit_at: litAt, intention: cleanIntention(intention), out_at: outAt,
                            answer: answer, sent: true });
  }

  function readLit(raw) {
    if (typeof raw !== "string" || !raw) return null;
    try {
      var o = JSON.parse(raw);
      if (!o || typeof o !== "object" || Array.isArray(o) || !isTime(o.lit_at)) return null;
      var out = { litAt: o.lit_at, intention: cleanIntention(o.intention) };
      if (o.sent === true) {
        if (!isTime(o.out_at) || typeof o.answer !== "string" || !o.answer.trim()) return null;
        out.sent = true; out.outAt = o.out_at; out.answer = o.answer;
      }
      return out;
    } catch (e) {
      return null;
    }
  }

  /* What the page shows when it opens: unlit; lit (however long ago) and waiting; or sent
     (blown out and answered, with the answer to send again). */
  function restore(raw, now) {
    var lit = readLit(raw);
    if (!lit) return { state: "unlit" };
    if (lit.sent) {
      return { state: "sent", litAt: lit.litAt, intention: lit.intention, outAt: lit.outAt,
               answer: lit.answer };
    }
    var burn = burnFraction(lit.litAt, now);
    return { state: "lit", litAt: lit.litAt, intention: lit.intention, burn: burn, ember: burn >= 1 };
  }

  /* Other entries' candles: already sent, can no longer be answered, or can't be read. */
  function staleCandles(items, now, keep) {
    return Object.keys(items || {}).filter(function (k) {
      if (k === keep || k.slice(0, 7) !== "candle_") return false;
      var lit = readLit(items[k]);
      return !lit || lit.sent || now - lit.litAt > KEEP_MS;
    });
  }

  function parseLaunch(href) {
    var q = new URL(href).searchParams;
    var type = q.get("t"), id = q.get("id");
    return { type: type, id: id, diag: q.get("diag") === "1",
             entry: type === "candle" && ID.test(id || "") };
  }

  function payload(launch, intention, litAt, outAt, answer) {
    return JSON.stringify({ v: 1, type: "candle", id: launch.id, intention: cleanIntention(intention),
                            lit_at: litAt, out_at: outAt, answer: answer });
  }

  /* The same answer again, from a sent record restore() returned. */
  function resend(launch, s) {
    return payload(launch, s.intention, s.litAt, s.outAt, s.answer);
  }

  /* What the smoke spells at blow-out: their words, or a plain line when there are none. */
  function echo(intention) {
    var s = cleanIntention(intention);
    return [s || OUT_LINE, ASK_LINE];
  }

  /* ---- breath rhythm -----------------------------------------------------------------
     samples: the microphone's loudness (RMS) at `hz` per second, oldest first; voiced: an
     optional flag per sample, true where the page heard a voice (tonal, not breathy).
     Returns 0 (quick, uneven, or talking) .. 1 (slow, even breathing); NEUTRAL when there
     is nothing to go on (silence, a steady hum, too little sound yet).
     Slow even breaths swell and fall smoothly, every 5-20 s (3-12 a minute). Talking comes
     in syllables several times a second, so it is rough from one tenth of a second to the
     next even when its bursts come round slowly; it is never read as calm. */
  function pct(sorted, p) { return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]; }

  function calmFromRms(samples, hz, voiced) {
    hz = hz || 10;
    var all = Array.isArray(samples) ? samples : [];
    var take = Math.round(CALM_WINDOW_S * hz);
    var x = all.slice(-take).map(function (v) { return isFinite(v) && v > 0 ? +v : 0; });
    var vs = Array.isArray(voiced) ? voiced.slice(-take) : null;
    var n = x.length;
    if (n < 8 * hz) return NEUTRAL;
    var sorted = x.slice().sort(function (a, b) { return a - b; });
    var floor = Math.max(1e-6, pct(sorted, 0.1)), top = pct(sorted, 0.9);
    if (top < floor * 2.5) return NEUTRAL;                       // nothing above the room

    // a voice the page heard in the louder moments
    var active = 0, heard = 0;
    for (var i = 0; i < n; i++) {
      if (x[i] > floor * 2) { active++; if (vs && vs[i]) heard++; }
    }
    var voice = active ? clamp01((heard / active - 0.1) / 0.2) : 0;

    // roughness: what a half-second average leaves behind, against the slow swell itself
    var w = Math.max(1, Math.round(hz / 4)), s = new Array(n), mean = 0;
    for (i = 0; i < n; i++) {
      var a = 0, c = 0;
      for (var j = Math.max(0, i - w); j <= Math.min(n - 1, i + w); j++) { a += x[j]; c++; }
      s[i] = a / c; mean += s[i];
    }
    mean /= n;
    var rough = 0, slow = 0;
    for (i = 0; i < n; i++) { rough += (x[i] - s[i]) * (x[i] - s[i]); slow += (s[i] - mean) * (s[i] - mean); }
    var jitter = Math.sqrt(rough / Math.max(1e-18, slow));
    var talk = Math.max(voice, clamp01((jitter - 0.3) / 0.3));

    // the rhythm: the first strong repeat of the smoothed swell, 1.5 s to 20 s apart
    function corr(L) {
      var sa = 0, sb = 0, m = n - L;
      for (var k = 0; k < m; k++) { sa += s[k]; sb += s[k + L]; }
      sa /= m; sb /= m;
      var xy = 0, xx = 0, yy = 0;
      for (k = 0; k < m; k++) {
        var p = s[k] - sa, q = s[k + L] - sb;
        xy += p * q; xx += p * p; yy += q * q;
      }
      return xx > 0 && yy > 0 ? xy / Math.sqrt(xx * yy) : 0;
    }
    var lo = Math.round(1.5 * hz), hi = Math.min(Math.round(20 * hz), Math.floor(n * 0.6));
    var R = [], period = 0, even = 0;
    for (var L = lo - 1; L <= hi + 1; L++) R[L] = corr(L);
    for (L = lo; L <= hi; L++) {
      if (R[L] >= 0.4 && R[L] >= R[L - 1] && R[L] >= R[L + 1]) { period = L; even = R[L]; break; }
    }

    // evenness, breath by breath: how much the gaps between swells vary
    var ups = [], above = false, hiT = mean + 0.25 * Math.sqrt(slow / n), loT = mean - 0.25 * Math.sqrt(slow / n);
    for (i = 0; i < n; i++) {
      if (!above && s[i] > hiT) { above = true; ups.push(i); } else if (above && s[i] < loT) above = false;
    }
    if (ups.length >= 3) {
      var gaps = [], gm = 0, gv = 0;
      for (i = 1; i < ups.length; i++) { gaps.push(ups[i] - ups[i - 1]); gm += ups[i] - ups[i - 1]; }
      gm /= gaps.length;
      gaps.forEach(function (g) { gv += (g - gm) * (g - gm); });
      var spread = Math.sqrt(gv / gaps.length) / gm;
      even = Math.min(even, 0.8 - 0.4 * clamp01((spread - 0.08) / 0.14));
    }

    var calm;
    if (!period) {
      calm = 0.3;                                                 // sound, but no rhythm
    } else {
      var perMin = 60 * hz / period;
      if (perMin <= 12) {
        // even breaths are calm; slower ones a little calmer, but only as far as they're even
        calm = 0.45 + 0.55 * clamp01((even - 0.4) / 0.4) * (0.7 + 0.3 * clamp01((12 - perMin) / 8));
      } else {
        calm = Math.max(0.05, 0.35 - (perMin - 12) / 24);         // quick breathing
      }
    }
    calm = calm * (1 - talk) + Math.min(calm, 0.25) * talk;
    return clamp01(calm);
  }

  return {
    BURN_MS: BURN_MS,
    INTENTION_MAX: INTENTION_MAX,
    CALM_WINDOW_S: CALM_WINDOW_S,
    NEUTRAL: NEUTRAL,
    burnFraction: burnFraction,
    cleanIntention: cleanIntention,
    storeKey: storeKey,
    litValue: litValue,
    pendingValue: pendingValue,
    resend: resend,
    readLit: readLit,
    restore: restore,
    staleCandles: staleCandles,
    parseLaunch: parseLaunch,
    payload: payload,
    echo: echo,
    calmFromRms: calmFromRms,
  };
});
