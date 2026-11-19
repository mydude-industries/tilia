// A candle you light before a hard thing and blow out after. The microphone is read as a
// volume number a few times a second and nothing else; audio is never recorded or sent.
// A breath is told from a voice or a clap by its shape: most of its energy sits under
// 1 kHz, it is noise-like rather than musical, and it holds for nearly half a second.
//
// Opened from Tilia's button it is a real entry (?t=candle&id=<token>): what it's for is
// asked first, the lit time and those words are kept in Telegram CloudStorage under
// candle_<token> (the button reopens the lit candle, burned down by the time away), and
// only blowing it out sends anything back: sendData({type:"candle", intention, lit_at,
// out_at, answer, now, later}), which closes the page. Before sending, the record becomes a pending
// one holding the answer, so an answer is never lost: reopened, the page offers "Send it
// again" (Tilia takes a second copy as already answered). Opened without an entry (the
// preview) it keeps nothing and sends nothing.
// Once lit, if the button names a relay (relay, mac, k), their words are sealed for the
// owner's Mac only and Tilia's line comes back in the smoke (candle-voice.js); without
// those params, or with no answer in 30 s, the candle is as it always was. The voice runs
// only inside Telegram, or with a relay on this machine (a dev run); never otherwise.
// At blow-out the smoke spells their words, then the line Tilia sealed for this moment
// (if it came), and one optional field asks "Anything to tell Tilia?" before Done.
"use strict";

(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var plain = !tg || tg.platform === "unknown";
  var K = window.CandleCore;
  var launch = K.parseLaunch(location.href);
  var main = document.getElementById("main"),
      say = document.getElementById("say"), hint = document.getElementById("hint"),
      meter = document.getElementById("meter"), bar = document.getElementById("bar"),
      micBtn = document.getElementById("mic"), again = document.getElementById("again"),
      done = document.getElementById("done"), candle = document.getElementById("candle"),
      after = document.getElementById("after"), follow = document.getElementById("follow"),
      ask = document.getElementById("ask"), field = document.getElementById("field"),
      lightBtn = document.getElementById("light"), title = document.getElementById("title"),
      lead = document.getElementById("lead"), resendBtn = document.getElementById("resend");
  var timers = [], burnTimer = null;
  CandleFlame.init(candle);
  CandleSmoke.init(document.getElementById("fx"), main, candle);
  CandleFollow.init(follow, picked);

  var state = "loading";   // loading | unlit | rising | lit | out | sent
  var sent = null;         // the answered candle, as restore() gives it, to send again
  var micOn = false, loud = 0;
  // lit: { litAt, intention, now, later, spelled }: now, Tilia's line once the smoke spelled
  // it; later, her line kept for blow-out; spelled, the later line blow-out really spelled.
  // Only spelled lines go back: the Mac counts a line as seen only when the page echoes it.
  var lit = null, outAt = 0;
  // Tilia's voice: only from a button that names a relay (else the candle is as it was)
  var voice = launch.entry && window.CandleVoice ? CandleVoice.params(location.search) : null;
  if (voice && !CandleVoice.mayAsk(voice, !plain)) voice = null;   // R8: Telegram, or a dev relay
  var askSeq = 0, stopWisp = null;

  // Telegram-only setup; a plain browser skips every call.
  if (tg) {
    try { tg.ready(); } catch (e) { /* keep going */ }
    try { tg.expand(); } catch (e) { /* keep going */ }
    try { tg.setHeaderColor("#141416"); } catch (e) { /* older client */ }
    try { tg.setBackgroundColor("#141416"); } catch (e) { /* older client */ }
    try { tg.setBottomBarColor("#141416"); } catch (e) { /* older client */ }
  }

  function show(el, on) { el.hidden = !on; }


  function haptic(kind) {
    try {
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (e) { /* no haptics */ }
  }

  /* ---------- the lit state: CloudStorage, and this phone's own copy ---------- */

  var key = launch.entry ? K.storeKey(launch.id) : null;
  var useCloud = false;
  try { useCloud = !plain && tg.isVersionAtLeast("6.9") && !!tg.CloudStorage; } catch (e) { /* older client */ }

  var local = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* in memory only */ } },
    remove: function (k) { try { window.localStorage.removeItem(k); } catch (e) { /* nothing to clear */ } },
    keys: function () {
      try {
        var out = [];
        for (var n = 0; n < window.localStorage.length; n++) out.push(window.localStorage.key(n));
        return out;
      } catch (e) { return []; }
    },
  };

  function cloud(method, args, cb) {
    try {
      tg.CloudStorage[method].apply(tg.CloudStorage, args.concat([function (err, val) { cb(err || null, val); }]));
    } catch (e) { cb(e); }
  }

  function saveLit() {
    if (!key) return;
    var v = K.litValue(lit.litAt, lit.intention, lit.later, lit.now);
    local.set(key, v);                       // both: a cloud save that fails still leaves this
    if (useCloud) cloud("setItem", [key, v], function () {});
  }

  // The lit candle, if there is one: the cloud copy, else this phone's. If CloudStorage
  // never answers, open with the local copy rather than hang.
  function loadLit(cb) {
    if (!key) return cb(null);
    if (!useCloud) return cb(local.get(key));
    var settled = false;
    function once(v) { if (!settled) { settled = true; cb(v); } }
    setTimeout(function () { once(local.get(key)); }, 1500);
    cloud("getItem", [key], function (err, val) { once(!err && val ? val : local.get(key)); });
  }

  // the answer replaces the lit record before it is sent (cb once saved, or after 1 s)
  function savePending(answer, cb) {
    if (!key) return cb();
    var v = K.pendingValue(lit.litAt, lit.intention, outAt, answer, { now: lit.now, later: lit.spelled });
    local.set(key, v);
    if (!useCloud) return cb();
    var settled = false;
    function once() { if (!settled) { settled = true; cb(); } }
    setTimeout(once, 1000);
    cloud("setItem", [key, v], once);
  }

  // A lit candle holds their words, so none outlives the entry it was for.
  function sweep() {
    var now = Date.now(), items = {};
    local.keys().forEach(function (k) { if (k && k.slice(0, 7) === "candle_") items[k] = local.get(k); });
    K.staleCandles(items, now, key).forEach(local.remove);
    if (!useCloud) return;
    cloud("getKeys", [], function (err, keys) {
      if (err || !Array.isArray(keys)) return;
      var mine = keys.filter(function (k) { return k.slice(0, 7) === "candle_" && k !== key; });
      if (!mine.length) return;
      cloud("getItems", [mine], function (err2, vals) {
        if (err2 || !vals) return;
        var stale = K.staleCandles(vals, now, key);
        if (stale.length) cloud("removeItems", [stale], function () {});
      });
    });
  }

  /* ---------- the stages ---------- */

  function clearOut() {
    timers.forEach(clearTimeout); timers = [];
    askSeq++; stopWisp = null;
    main.classList.remove("speaking");
    CandleSmoke.clear();
    after.textContent = "";
    CandleFollow.reset();
  }

  // the wax shortens with the time it has been burning; past four hours it is an ember
  function burnNow() {
    if (state !== "lit" || !lit) return 0;
    var f = K.burnFraction(lit.litAt, Date.now());
    candle.style.setProperty("--burnt", Math.round(f * 100) + "px");
    CandleFlame.setBurn(f);
    return f;
  }

  function unlit() {
    state = "unlit";
    lit = null;
    clearOut();
    candle.style.setProperty("--burnt", "0px");
    CandleFlame.setBurn(0);
    main.classList.remove("lit", "out", "rising");
    title.textContent = "Set your intention";
    lead.textContent = "";
    say.textContent = "";
    hint.textContent = "";
    field.value = "";
    lightBtn.disabled = true;
    show(ask, true); show(micBtn, false); show(meter, false); show(again, false); show(done, false);
    show(resendBtn, false);
  }

  // their words fall into the wick and become the flame: one continuous morph, the flame
  // growing by what has arrived; it is lit once the last of them has merged
  function begin() {
    if (state !== "unlit") return;
    var words = K.cleanIntention(field.value);
    if (!K.canLight(words)) { field.focus(); return; }   // something to light it for, first
    field.blur();
    show(ask, false);
    state = "rising";
    main.classList.add("rising");
    say.textContent = "";
    main.style.setProperty("--kindle", "0");
    CandleSmoke.rise(words, function (frac, fade) {
      var grow = CandleFlame.gather(frac, fade);
      main.style.setProperty("--kindle", (Math.min(1, grow) * (fade === undefined ? 1 : fade)).toFixed(3));
      return grow;
    }, function () {
      main.classList.remove("rising");
      light(words, Date.now(), false);
    });
  }

  // already: a candle lit on an earlier visit, reopened (later: Tilia's line, if it came)
  function light(intention, litAt, already, later, now) {
    state = "lit"; loud = 0;
    lit = { litAt: litAt, intention: intention, later: later || undefined, now: now || undefined };
    clearOut();
    if (!already) saveLit();
    var top0 = candle.getBoundingClientRect().top;
    var f = burnNow();
    CandleFlame.setState("lit", already);
    main.classList.remove("out", "rising"); main.classList.add("lit");
    title.textContent = intention || "Before the hard thing";
    lead.textContent = "Come back and blow it out when it's done.";
    say.textContent = !already ? "It's lit. One breath before you go."
      : f >= 1 ? "It burned down while you were away. It's still here when you're ready."
        : "Still burning. Blow it out when it's done.";
    hint.textContent = micOn ? "Listening." : "";
    show(ask, false); show(micBtn, !micOn); show(meter, micOn); show(again, false); show(done, false);
    if (!already) glide(top0);
    clearInterval(burnTimer);
    burnTimer = setInterval(burnNow, 30000);
    if (!already) haptic("light");
    listen();
  }

  // Lit (saved first), so Tilia is asked: a thin wisp rises while the page waits, and her
  // line forms in the smoke when it comes. Her later line is kept with the lit candle for
  // blow-out. No answer in 30 s: the wisp fades and nothing is spelled, ever.
  function listen() {
    if (!voice || !lit || !CandleVoice.needsAsk({ state: state, later: lit.later })) return;
    var seq = ++askSeq, mine = lit;
    // a late reopen (R9) asks only for the blow-out line: no wisp, nothing spelled now
    var late = !CandleVoice.speaksNow(mine.litAt, Date.now());
    stopWisp = late ? null : CandleSmoke.gather();
    CandleVoice.ask({ relay: voice.relay, mac: voice.mac, k: voice.k, id: launch.id,
                      intention: mine.intention }).then(function (lines) {
      if (seq !== askSeq) return;
      var still = state === "lit" && lit === mine;
      if (!lines) { if (still && stopWisp) stopWisp(); stopWisp = null; return; }
      mine.later = lines.later;                  // kept for blow-out, if it's still to come
      if (still && !CandleVoice.speaksNow(mine.litAt, Date.now())) {
        if (stopWisp) stopWisp();                // past 10 min: never spelled (R9)
        stopWisp = null;
        saveLit();
        return;
      }
      if (!still) return;                        // too late for the smoke: never spelled
      mine.now = lines.now;                      // spelled just below
      saveLit();
      stopWisp = null;
      main.classList.add("speaking");
      after.textContent = lines.now;
      CandleSmoke.speak(lines.now, function () {
        if (seq === askSeq) main.classList.remove("speaking");
      }, CandleVoice.holdMs(lines.now));
    });
  }

  // the lit words above are a different length, so the candle may sit higher or lower now:
  // it glides there (with its flame) instead of jumping
  function glide(top0) {
    var dy = top0 - candle.getBoundingClientRect().top;
    if (Math.abs(dy) < 1) return;
    candle.style.transition = "none";
    candle.style.transform = "translateY(" + dy.toFixed(1) + "px)";
    void candle.offsetWidth;
    candle.style.transition = "";
    candle.style.transform = "";
  }

  function blowOut(how) {
    if (state !== "lit") return;
    state = "out";
    outAt = Date.now();
    main.classList.remove("speaking");
    clearInterval(burnTimer);
    CandleFlame.setState("out");
    CandleFlame.setCalm(K.NEUTRAL);
    main.classList.remove("lit"); main.classList.add("out");
    say.textContent = "";
    hint.textContent = how === "mic" ? "Blown out." : "Tapped out.";
    show(micBtn, false); show(meter, false); show(again, false); show(done, false);
    haptic("success");
    // their words, then Tilia's sealed line (saved with the lit candle, or only in memory
    // if it came late); without one, just their words
    var words = K.echo(lit && lit.intention, lit && lit.later);
    if (lit) lit.spelled = words.length > 1 ? words[1] : undefined;
    after.textContent = words.join(" ");
    CandleSmoke.morph(words, CandleFollow.show);
  }

  function send(data) {
    main.setAttribute("data-sent", data);   // for the dev frames outside Telegram
    if (plain) {
      say.textContent = "Noted. Outside Telegram nothing is sent.";   // the field's Done was the Done
      return;
    }
    try { tg.sendData(data); } catch (e) {
      say.textContent = "That didn't go through. Close this and tap Open again.";
    }
  }

  // Done (with their words, or nothing): kept as pending first, then sent back to Tilia at
  // once (Telegram closing the page is the confirmation)
  function picked(answer) {
    haptic("light");
    if (!launch.entry) {
      say.textContent = "Noted. Tilia will pick this up with you.";
      show(again, true);
      return;
    }
    state = "sent";
    var shown = lit ? { now: lit.now, later: lit.spelled } : null;
    var data = K.payload(launch, lit ? lit.intention : "", lit ? lit.litAt : outAt, outAt, answer, shown);
    savePending(answer, function () {
      say.textContent = "Noted. Tilia will pick this up with you.";
      send(data);
    });
  }

  // reopened after answering: their words and the answer, and one tap to send it again
  function answered(s) {
    state = "sent";
    sent = s;
    lit = { litAt: s.litAt, intention: s.intention, now: s.now, later: s.later, spelled: s.later };
    outAt = s.outAt;
    candle.style.setProperty("--burnt", Math.round(K.burnFraction(s.litAt, s.outAt) * 100) + "px");
    CandleFlame.setBurn(K.burnFraction(s.litAt, s.outAt));
    main.classList.remove("lit", "rising"); main.classList.add("out");
    say.textContent = s.answer ? "You said: " + s.answer : "";
    hint.textContent = "";
    show(ask, false); show(micBtn, false); show(meter, false); show(again, false); show(done, false);
    var words = K.echo(s.intention, s.later);
    after.textContent = words.join(" ");
    CandleSmoke.morph(words, function () { show(resendBtn, true); });
  }

  candle.addEventListener("click", function () {
    if (state === "lit") blowOut("tap");
    else if (state === "unlit") begin();
  });
  ask.addEventListener("submit", function (e) { e.preventDefault(); begin(); });
  field.addEventListener("input", function () { lightBtn.disabled = !K.canLight(field.value); });
  again.addEventListener("click", unlit);
  resendBtn.addEventListener("click", function () {
    if (!sent) return;
    show(resendBtn, false);
    say.textContent = "Noted. Tilia will pick this up with you.";
    send(K.resend(launch, sent));
  });
  done.addEventListener("click", function () {
    if (tg && !plain) { try { tg.close(); return; } catch (e) { /* fall through */ } }
    say.textContent = "You can close this page.";
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) burnNow();
  });

  /* ---------- the microphone: blow-out, and the breathing the flame follows ---------- */

  micBtn.addEventListener("click", function () {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      hint.textContent = "No microphone here. Tap the flame instead.";
      return;
    }
    hint.textContent = "Asking for the microphone…";
    // Ask for a raw microphone: the phone's voice processing (noise suppression, echo
    // cancellation, auto gain) is built to strip the broadband rush a breath makes.
    var raw = { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } };
    navigator.mediaDevices.getUserMedia(raw).catch(function () {
      return navigator.mediaDevices.getUserMedia({ audio: true });
    }).then(function (stream) {
      var got = {};
      try { got = stream.getAudioTracks()[0].getSettings(); } catch (e) { /* older client */ }
      var proc = ["echoCancellation", "noiseSuppression", "autoGainControl"].map(function (k) {
        return k.slice(0, 4) + ":" + (got[k] === undefined ? "?" : got[k] ? "on" : "off");
      }).join(" ");
      var Ctx = window.AudioContext || window.webkitAudioContext;
      var ctx = new Ctx();
      try { ctx.resume(); } catch (e) { /* already running */ }
      var an = ctx.createAnalyser();
      an.fftSize = 2048; an.smoothingTimeConstant = 0.5;
      ctx.createMediaStreamSource(stream).connect(an);
      var N = an.frequencyBinCount, db = new Float32Array(N), wave = new Float32Array(an.fftSize);
      var binHz = ctx.sampleRate / an.fftSize;
      var lowTop = Math.round(1000 / binHz), flatTop = Math.round(2000 / binHz), allTop = Math.round(4000 / binHz);
      micOn = true;
      show(micBtn, false); show(meter, true);
      hint.textContent = "It keeps burning while you're away. Blow it out when it's done.";
      var last = performance.now(), floor = 0, peak = 0;
      // the breathing the flame follows: loudness ten times a second, the last 30 s of it
      var env = [], heard = [], binSum = 0, binN = 0, binVoice = 0, binMs = 0, sinceCalm = 0;
      var KEEP = K.CALM_WINDOW_S * 10;
      (function tick(now) {
        var dt = now - last; last = now;
        if (state !== "lit") {
          loud = 0; env.length = 0; heard.length = 0; binSum = binN = binVoice = binMs = 0;
          requestAnimationFrame(tick); return;
        }
        // loudness from the waveform itself (the phone's gain varies; the shape doesn't)
        an.getFloatTimeDomainData(wave);
        var sq = 0;
        for (var j = 0; j < wave.length; j++) sq += wave[j] * wave[j];
        var rms = Math.sqrt(sq / wave.length);
        // the room's quiet level: creeps up slowly, drops fast, never zero
        floor = rms < floor ? floor * 0.9 + rms * 0.1 : floor + (rms - floor) * 0.01;
        if (floor < 0.0015) floor = 0.0015;
        var over = rms / floor;                                        // how far above quiet
        if (rms > peak) peak = rms;
        an.getFloatFrequencyData(db);
        var lowE = 0, allE = 0, logSum = 0, linSum = 0, n = 0;
        for (var i = 1; i < allTop; i++) {
          var m = Math.pow(10, db[i] / 20), e = m * m;
          allE += e;
          if (i < lowTop) lowE += e;
          if (i >= 2 && i < flatTop) { logSum += Math.log(m + 1e-12); linSum += m; n++; }
        }
        var ratio = allE ? lowE / allE : 0;                           // share under 1 kHz
        var flat = n ? Math.exp(logSum / n) / (linSum / n) : 0;        // 1 = pure noise, ~0 = a voice
        var blowy = over > 3 && ratio > 0.5 && flat > 0.15;
        var level = Math.min(1, over / 12);
        bar.style.width = Math.min(100, level * 120) + "%";
        bar.className = blowy ? "blow" : "";
        CandleFlame.setBreath(blowy ? Math.min(55, level * 120) : 0, blowy ? Math.max(0.35, 1 - level) : 1);
        if (launch.diag) {
          hint.textContent = "x" + over.toFixed(1) + " over quiet · low " + ratio.toFixed(2) + " · flat " + flat.toFixed(2)
            + (blowy ? " · breath" : "") + "\n" + proc + " · peak " + peak.toFixed(3) + " quiet " + floor.toFixed(3);
        }
        loud = blowy ? loud + dt : Math.max(0, loud - dt * 2);
        if (loud > 400) blowOut("mic");

        // breath rhythm (never shown as a number): a voice is tonal, a breath is noise-like
        binSum += rms; binN++; binMs += dt;
        if (over > 2 && flat < 0.1) binVoice++;
        if (binMs >= 100) {
          env.push(binSum / binN); heard.push(binVoice * 2 > binN);
          if (env.length > KEEP) { env.shift(); heard.shift(); }
          binSum = binN = binVoice = 0; binMs -= 100;
          if (++sinceCalm >= 10) {                                     // once a second
            sinceCalm = 0;
            CandleFlame.setCalm(K.calmFromRms(env, 10, heard));
          }
        }
        requestAnimationFrame(tick);
      })(performance.now());
    }).catch(function (err) {
      hint.textContent = "No microphone (" + (err && err.name || "denied") + "). Tap the flame instead.";
    });
  });

  /* ---------- start ---------- */

  loadLit(function (raw) {
    var s = K.restore(raw, Date.now());
    if (s.state === "lit") light(s.intention, s.litAt, true, s.later, s.now);
    else if (s.state === "sent") answered(s);
    else unlit();
    if (key) setTimeout(function () { try { sweep(); } catch (e) { /* housekeeping only */ } }, 0);
  });
})();
