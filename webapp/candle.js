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
      done = document.getElementById("done"), candle = document.getElementById("candle");

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

  function light() {
    state = "lit"; loud = 0;
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
    say.textContent = "Out. How did it go?";
    hint.textContent = how === "mic" ? "Blown out." : "Tapped out.";
    show(micBtn, false); show(meter, false); show(again, true); show(done, true);
    try { tg && tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* no haptics */ }
  }

  candle.addEventListener("click", function () {
    if (state === "lit") blowOut("tap"); else light();
  });
  again.addEventListener("click", light);
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
        var blowy = over > 2 && ratio > 0.45 && flat > 0.15;
        var level = Math.min(1, over / 8);
        bar.style.width = Math.min(100, level * 120) + "%";
        bar.className = blowy ? "blow" : "";
        lean = blowy ? Math.min(55, level * 120) : 0;
        squash = blowy ? Math.max(0.35, 1 - level) : 1;
        hint.textContent = "x" + over.toFixed(1) + " over quiet · low " + ratio.toFixed(2) + " · flat " + flat.toFixed(2)
          + (blowy ? " · breath" : "") + "\n" + proc + " · peak " + peak.toFixed(3) + " quiet " + floor.toFixed(3);
        loud = blowy ? loud + dt : Math.max(0, loud - dt * 2);
        if (loud > 300) blowOut("mic");
        requestAnimationFrame(tick);
      })(performance.now());
    }).catch(function (err) {
      hint.textContent = "No microphone (" + (err && err.name || "denied") + "). Tap the flame instead.";
    });
  });
})();
