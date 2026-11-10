// A candle you light before a hard thing and blow out after. The microphone is read as a
// volume number a few times a second and nothing else; audio is never recorded or sent.
// A breath is told from a voice or a clap by its shape: most of its energy sits under
// 400 Hz, it is noise-like rather than musical, and it holds for nearly half a second.
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
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      var ctx = new Ctx();
      try { ctx.resume(); } catch (e) { /* already running */ }
      var an = ctx.createAnalyser();
      an.fftSize = 2048; an.smoothingTimeConstant = 0.5;
      ctx.createMediaStreamSource(stream).connect(an);
      var N = an.frequencyBinCount, db = new Float32Array(N), binHz = ctx.sampleRate / an.fftSize;
      var lowTop = Math.round(400 / binHz), flatTop = Math.round(2000 / binHz), allTop = Math.round(4000 / binHz);
      micOn = true;
      show(micBtn, false); show(meter, true);
      hint.textContent = "Listening for a breath.";
      var last = performance.now();
      (function tick(now) {
        var dt = now - last; last = now;
        if (state !== "lit") { lean = 0; squash = 1; loud = 0; requestAnimationFrame(tick); return; }
        an.getFloatFrequencyData(db);
        var lowE = 0, allE = 0, logSum = 0, linSum = 0, n = 0;
        for (var i = 1; i < allTop; i++) {
          var m = Math.pow(10, db[i] / 20), e = m * m;
          allE += e;
          if (i < lowTop) lowE += e;
          if (i >= 2 && i < flatTop) { logSum += Math.log(m + 1e-12); linSum += m; n++; }
        }
        var level = Math.min(1, Math.sqrt(allE / allTop) * 40);     // loudness, 0..1
        var ratio = allE ? lowE / allE : 0;                           // share under 400 Hz
        var flat = n ? Math.exp(logSum / n) / (linSum / n) : 0;        // 1 = pure noise, ~0 = a voice
        var blowy = level > 0.12 && ratio > 0.6 && flat > 0.25;
        bar.style.width = Math.min(100, level * 120) + "%";
        bar.className = blowy ? "blow" : "";
        lean = blowy ? Math.min(55, level * 120) : 0;
        squash = blowy ? Math.max(0.35, 1 - level) : 1;
        hint.textContent = "loud " + level.toFixed(2) + " · low " + ratio.toFixed(2) + " · flat " + flat.toFixed(2) + (blowy ? " · breath" : "");
        loud = blowy ? loud + dt : Math.max(0, loud - dt * 2);
        if (loud > 450) blowOut("mic");
        requestAnimationFrame(tick);
      })(performance.now());
    }).catch(function (err) {
      hint.textContent = "No microphone (" + (err && err.name || "denied") + "). Tap the flame instead.";
    });
  });
})();
