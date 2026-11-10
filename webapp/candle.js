// A candle you light before a hard thing and blow out after. The microphone is read as a
// volume number a few times a second and nothing else; audio is never recorded or sent.
// A breath is told from a voice or a clap by its shape: most of its energy sits under
// 1 kHz, it is noise-like rather than musical, and it holds for nearly half a second.
"use strict";

(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var main = document.getElementById("main"), flame = document.getElementById("flame"),
      say = document.getElementById("say"), hint = document.getElementById("hint"),
      meter = document.getElementById("meter"), bar = document.getElementById("bar"),
      micBtn = document.getElementById("mic"), again = document.getElementById("again"),
      done = document.getElementById("done"), candle = document.getElementById("candle"),
      after = document.getElementById("after"), follow = document.getElementById("follow");
  var timers = [];

  var state = "unlit";     // unlit | lit | out
  var micOn = false, loud = 0, flick = 0, lean = 0, squash = 1;

  // Telegram-only setup; a plain browser skips every call.
  if (tg) {
    try { tg.ready(); } catch (e) { /* keep going */ }
    try { tg.expand(); } catch (e) { /* keep going */ }
    try { tg.setHeaderColor("#141416"); } catch (e) { /* older client */ }
    try { tg.setBackgroundColor("#141416"); } catch (e) { /* older client */ }
    try { tg.setBottomBarColor("#141416"); } catch (e) { /* older client */ }
  }

  function show(el, on) { el.hidden = !on; }

  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }

  // The follow-up is drawn by the smoke itself. One set of particles leaves the wick as
  // smoke, drifts, then is drawn into the shapes of the letters, glowing as it gathers;
  // the crisp words fade up underneath while the particles dissolve.
  var fx = document.getElementById("fx"), gfx = fx.getContext("2d"), anim = null;
  var SERIF = '500 30px "New York", ui-serif, "Iowan Old Style", Charter, Georgia, serif';
  var SANS = '600 13px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  function sprite(color) {
    var c = document.createElement("canvas"); c.width = c.height = 32;
    var g = c.getContext("2d"), r = g.createRadialGradient(16, 16, 0, 16, 16, 16);
    r.addColorStop(0, color.replace("A", "0.9")); r.addColorStop(0.5, color.replace("A", "0.28")); r.addColorStop(1, color.replace("A", "0"));
    g.fillStyle = r; g.fillRect(0, 0, 32, 32);
    return c;
  }
  var SMOKE = sprite("rgba(210,205,195,A)"), EMBER = sprite("rgba(255,190,90,A)");

  function textPoints(w, h, lines, dpr) {
    // where the words are: sample the filled glyphs on an offscreen canvas
    var c = document.createElement("canvas"); c.width = w; c.height = h;
    var g = c.getContext("2d"); g.scale(dpr, dpr); g.fillStyle = "#fff"; g.textAlign = "center"; g.textBaseline = "middle";
    lines.forEach(function (ln) { g.font = ln.font; try { g.letterSpacing = ln.spacing || "0px"; } catch (e) { /* older client */ } g.fillText(ln.text, w / dpr / 2, ln.y); });
    var d = g.getImageData(0, 0, w, h).data, pts = [], step = Math.max(2, Math.round(2.2 * dpr));
    for (var y = 0; y < h; y += step) for (var x = 0; x < w; x += step) if (d[(y * w + x) * 4 + 3] > 110) pts.push([x / dpr, y / dpr]);
    for (var i = pts.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = pts[i]; pts[i] = pts[j]; pts[j] = t; }
    return pts;
  }

  function ease(u) { return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2; }

  function morph(lines, onReady) {
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var mr = main.getBoundingClientRect(), cr = candle.getBoundingClientRect();
    var W = Math.round(mr.width), wickX = cr.left + cr.width / 2 - mr.left, wickY = cr.bottom - 118 - mr.top;
    var H = Math.max(160, Math.round(wickY));
    fx.width = W * dpr; fx.height = H * dpr; fx.style.height = H + "px";
    var ty = Math.max(70, H * 0.56);
    var L = [{ text: lines[0], font: SANS, y: ty - 34, spacing: "2px" }, { text: lines[1], font: SERIF, y: ty }];
    var pts = textPoints(fx.width, fx.height, L, dpr);
    var N = Math.min(1100, Math.max(700, pts.length));
    var P = [];
    for (var i = 0; i < N; i++) {
      var tgt = i < pts.length ? pts[i] : null;
      P.push({
        x: wickX + (Math.random() - 0.5) * 10, y: wickY + 2,
        vy: -(34 + Math.random() * 50), sway: (Math.random() - 0.5) * 3.4, ph: Math.random() * 6.28,
        born: Math.random() * 1500, size: 4 + Math.random() * 10, grow: 14 + Math.random() * 16,
        tx: tgt && tgt[0], ty: tgt && tgt[1], start: 1500 + Math.random() * 900, dur: 1400 + Math.random() * 600,
        fx: 0, fy: 0, jitter: Math.random() * 6.28
      });
    }
    var t0 = null;
    function frame(now) {
      if (t0 === null) t0 = now;
      var t = now - t0, dt = 16;
      gfx.setTransform(dpr, 0, 0, dpr, 0, 0);
      gfx.clearRect(0, 0, W, H);
      var textA = Math.max(0, Math.min(1, (t - 3500) / 1000));          // the words fade up
      var done = true;
      for (var i = 0; i < P.length; i++) {
        var p = P[i], age = t - p.born;
        if (age < 0) { done = false; continue; }
        // free smoke: rises, sways, widens, thins
        p.fy = p.y + p.vy * age / 1000;
        p.fx = p.x + Math.sin(age / 900 + p.ph) * 14 * p.sway + Math.sin(age / 2300 + p.ph) * 9 + p.sway * age / 90;
        var sz = p.size + p.grow * age / 1000;
        var smokeA = Math.max(0, Math.min(0.2, age / 500) - age / 5200);
        var x = p.fx, y = p.fy, a = smokeA, gold = 0;
        if (p.tx !== null) {
          var u = Math.max(0, Math.min(1, (t - p.start) / p.dur)), e = ease(u);
          if (u > 0) {
            var jx = Math.sin(t / 260 + p.jitter) * 0.8 * (1 - textA), jy = Math.cos(t / 310 + p.jitter) * 0.8 * (1 - textA);
            x = p.fx + (p.tx + jx - p.fx) * e; y = p.fy + (p.ty + jy - p.fy) * e;
            sz = sz + (3.2 - sz) * e;
            a = smokeA + (0.85 - smokeA) * e;
            gold = e;
            a *= 1 - textA;                                               // dissolve as the words arrive
          }
          if (u < 1 || textA < 1) done = false;
        } else if (smokeA > 0.01) { done = false; }
        if (a <= 0.005) continue;
        var half = sz / 2;
        if (gold < 1) { gfx.globalAlpha = a * (1 - gold); gfx.drawImage(SMOKE, x - half, y - half, sz, sz); }
        if (gold > 0) { gfx.globalAlpha = a * gold; gfx.drawImage(EMBER, x - half * 1.6, y - half * 1.6, sz * 1.6, sz * 1.6); }
      }
      if (textA > 0) {
        gfx.globalAlpha = textA; gfx.textAlign = "center"; gfx.textBaseline = "middle";
        gfx.shadowColor = "rgba(255,170,60," + (0.9 - 0.6 * textA) + ")"; gfx.shadowBlur = 22 - 14 * textA;
        L.forEach(function (ln, k) {
          gfx.font = ln.font; try { gfx.letterSpacing = ln.spacing || "0px"; } catch (e) { /* older client */ }
          gfx.fillStyle = k === 0 ? "#8c877b" : "#ece7da";
          gfx.fillText(ln.text, W / 2, ln.y);
        });
        gfx.shadowBlur = 0;
      }
      gfx.globalAlpha = 1;
      if (t > 3800 && onReady) { onReady(); onReady = null; }
      if (!done || textA < 1) anim = requestAnimationFrame(frame);
      else anim = null;
    }
    anim = requestAnimationFrame(frame);
  }

  function clearOut() {
    timers.forEach(clearTimeout); timers = [];
    if (anim) { cancelAnimationFrame(anim); anim = null; }
    gfx.setTransform(1, 0, 0, 1, 0, 0); gfx.clearRect(0, 0, fx.width, fx.height);
    after.textContent = "";
    follow.classList.remove("on"); show(follow, false);
  }

  function light() {
    state = "lit"; loud = 0;
    clearOut();
    main.classList.remove("out"); main.classList.add("lit");
    say.textContent = "It's lit. One breath before you go.";
    hint.textContent = micOn ? "Listening for a breath." : "";
    show(micBtn, !micOn); show(meter, micOn); show(again, false); show(done, false);
    try { tg && tg.HapticFeedback.impactOccurred("light"); } catch (e) { /* no haptics */ }
  }

  function blowOut(how) {
    if (state !== "lit") return;
    state = "out";
    main.classList.remove("lit"); main.classList.add("out");
    say.textContent = "";
    hint.textContent = how === "mic" ? "Blown out." : "Tapped out.";
    show(micBtn, false); show(meter, false); show(again, false); show(done, false);
    try { tg && tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* no haptics */ }
    after.textContent = "It's out. How did it go?";
    morph(["IT'S OUT.", "How did it go?"], function () { show(follow, true); follow.classList.add("on"); });
  }

  Array.prototype.forEach.call(follow.querySelectorAll("[data-pick]"), function (b) {
    b.addEventListener("click", function () {
      Array.prototype.forEach.call(follow.querySelectorAll("[data-pick]"), function (o) { o.disabled = o !== b; o.style.opacity = o === b ? 1 : .35; });
      say.textContent = "Noted. Tilia will pick this up with you.";
      show(again, true); show(done, true);
      try { tg && tg.HapticFeedback.impactOccurred("light"); } catch (e) { /* no haptics */ }
    });
  });

  candle.addEventListener("click", function () {
    if (state === "lit") blowOut("tap"); else light();
  });
  again.addEventListener("click", function () {
    Array.prototype.forEach.call(follow.querySelectorAll("[data-pick]"), function (o) { o.disabled = false; o.style.opacity = ""; });
    light();
  });
  done.addEventListener("click", function () {
    if (tg) { try { tg.close(); return; } catch (e) { /* fall through */ } }
    say.textContent = "You can close this page.";
  });

  // The flame: a slow idle flicker, bent and squashed by a breath.
  (function draw() {
    if (state === "lit") {
      flick += 0.15;
      var r = Math.sin(flick) * 2 + Math.sin(flick * 2.7) * 1.2 - lean;
      var s = (1 + Math.sin(flick * 1.3) * 0.04) * squash;
      flame.style.transform = "rotate(" + r + "deg) scaleY(" + s + ")";
    } else {
      flame.style.transform = "";
    }
    requestAnimationFrame(draw);
  })();

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
      hint.textContent = "Listening for a breath. Blow close to the bottom of the phone.";
      var last = performance.now(), floor = 0, peak = 0;
      (function tick(now) {
        var dt = now - last; last = now;
        if (state !== "lit") { lean = 0; squash = 1; loud = 0; requestAnimationFrame(tick); return; }
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
        lean = blowy ? Math.min(55, level * 120) : 0;
        squash = blowy ? Math.max(0.35, 1 - level) : 1;
        hint.textContent = "x" + over.toFixed(1) + " over quiet · low " + ratio.toFixed(2) + " · flat " + flat.toFixed(2)
          + (blowy ? " · breath" : "") + "\n" + proc + " · peak " + peak.toFixed(3) + " quiet " + floor.toFixed(3);
        loud = blowy ? loud + dt : Math.max(0, loud - dt * 2);
        if (loud > 400) blowOut("mic");
        requestAnimationFrame(tick);
      })(performance.now());
    }).catch(function (err) {
      hint.textContent = "No microphone (" + (err && err.name || "denied") + "). Tap the flame instead.";
    });
  });
})();
