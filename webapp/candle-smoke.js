// The smoke that becomes the follow-up. Owned by the smoke module: candle.js only calls
// CandleSmoke.init(canvas, main, candle), CandleSmoke.morph([small, big], onReady) and
// CandleSmoke.clear().
"use strict";

window.CandleSmoke = (function () {
  // One thin ribbon of smoke leaves the wick tip and is carried by a smooth, slowly
  // changing swirl, so neighbouring particles move together and the ribbon curls, folds
  // and thins as it cools. Then the ribbon is gathered, oldest end first, into the letters:
  // each particle is pulled in on a soft spring, circles once, settles, warms to ember,
  // flares as it lands, and cools to ink while the crisp words are revealed beneath it in
  // the same left-to-right wave. The words keep a faint warmth.
  var fx, gfx, main, candle, wick, anim = null;
  var SERIF = '500 30px "New York", ui-serif, "Iowan Old Style", Charter, Georgia, serif';
  var SANS = '600 13px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  var MAXP = 1100, MAXT = 960;             // particles in all; at most this many become letters
  var EMIT = 1550;                          // ms the wick keeps smoking
  var CAP0 = 1300, CAPW = 1300;             // the gathering wave: first start, spread across the words
  var REVEAL = 1300, REVEAL_FADE = 520;     // crisp text shows this long after a spot starts gathering
  var READY = 3800, END = 4900;

  // ---- sprites (pre-rendered; nothing is blurred in the hot loop) ----
  function sprite(rgb, stops) {
    var c = document.createElement("canvas"); c.width = c.height = 64;
    var g = c.getContext("2d"), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    stops.forEach(function (s) { r.addColorStop(s[0], "rgba(" + rgb + "," + s[1] + ")"); });
    g.fillStyle = r; g.fillRect(0, 0, 64, 64);
    return c;
  }
  var SOFT = [[0, 1], [0.25, 0.62], [0.55, 0.2], [0.8, 0.05], [1, 0]];
  var HOT = [[0, 1], [0.12, 0.9], [0.32, 0.38], [0.6, 0.08], [1, 0]];
  var SMOKE = sprite("206,201,191", SOFT);
  var EMBER = sprite("255,178,82", HOT);
  var INK = sprite("236,231,218", HOT);

  // ---- the air: a divergence-free swirl from a few drifting plane waves ----
  // psi = sum a*sin(k.x + w t + p); velocity = (dpsi/dy, -dpsi/dx). Smooth in space, so
  // neighbours travel together; slow in time, so the ribbon bends instead of jittering.
  var waves = [];
  function newAir() {
    var spec = [[110, 84, 46, 2.1], [64, 30, 24, -2.7], [170, 118, 30, 1.2], [52, -60, 6, 2.6]];
    waves = spec.map(function (s) {
      var lam = s[0] * (0.85 + Math.random() * 0.3), ang = (s[1] + (Math.random() - 0.5) * 24) * Math.PI / 180;
      var k = 2 * Math.PI / lam;
      return { kx: Math.cos(ang) * k, ky: Math.sin(ang) * k, a: s[2] / k, w: s[3], p: Math.random() * 6.283 };
    });
  }
  var air = [0, 0];
  function swirl(x, y, ts) {
    var vx = 0, vy = 0;
    for (var i = 0; i < waves.length; i++) {
      var q = waves[i], c = q.a * Math.cos(q.kx * x + q.ky * y + q.w * ts + q.p);
      vx += c * q.ky; vy -= c * q.kx;
    }
    air[0] = vx; air[1] = vy;
  }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function smooth(a, b, v) { var u = clamp01((v - a) / (b - a)); return u * u * (3 - 2 * u); }

  // ---- where the words are: sample the filled glyphs on an offscreen canvas ----
  function textPoints(W, H, lines, dpr) {
    var c = document.createElement("canvas"); c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    var g = c.getContext("2d"); g.scale(dpr, dpr); g.fillStyle = "#fff"; g.textAlign = "center"; g.textBaseline = "middle";
    var w = c.width, h = c.height, out = [];
    lines.forEach(function (ln, k) {
      g.clearRect(0, 0, W, H);
      g.font = ln.font; try { g.letterSpacing = ln.spacing || "0px"; } catch (e) { /* older client */ }
      g.fillText(ln.text, W / 2, ln.y);
      var d = g.getImageData(0, 0, w, h).data, step = Math.max(1, Math.round(1.5 * dpr));
      var y0 = Math.max(0, Math.floor((ln.y - 30) * dpr)), y1 = Math.min(h, Math.ceil((ln.y + 30) * dpr));
      for (var y = y0; y < y1; y += step) for (var x = 0; x < w; x += step) {
        if (d[(y * w + x) * 4 + 3] > 120) out.push({ x: x / dpr + (Math.random() - 0.5) * 0.5, y: y / dpr + (Math.random() - 0.5) * 0.5, line: k });
      }
    });
    // thin evenly if there are too many, then order the wave: small line first, then left to right
    for (var i = out.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = out[i]; out[i] = out[j]; out[j] = t; }
    out = out.slice(0, MAXT);
    out.sort(function (a, b) { return a.line - b.line || (a.x + a.y * 0.15) - (b.x + b.y * 0.15); });
    return out;
  }

  function drawText(g, L, W, glowA, blur) {
    g.textAlign = "center"; g.textBaseline = "middle";
    g.shadowColor = "rgba(255,168,64," + glowA.toFixed(3) + ")"; g.shadowBlur = blur;
    L.forEach(function (ln, k) {
      g.font = ln.font; try { g.letterSpacing = ln.spacing || "0px"; } catch (e) { /* older client */ }
      g.fillStyle = k === 0 ? "#8c877b" : "#ece7da";
      g.fillText(ln.text, W / 2, ln.y);
    });
    g.shadowBlur = 0; g.shadowColor = "transparent";
    try { g.letterSpacing = "0px"; } catch (e) { /* older client */ }
  }

  // the wick tip, live: the candle settles lower while the smoke is still rising
  function tipNow() {
    var m = main.getBoundingClientRect(), r = wick && wick.getBoundingClientRect();
    if (r && r.height > 0) return [r.left + r.width / 2 - m.left, r.top - m.top];
    var c = candle.getBoundingClientRect();
    return [c.left + c.width / 2 - m.left, c.bottom - 132 - m.top];
  }

  function morph(lines, onReady) {
    clear();
    newAir();
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var mr = main.getBoundingClientRect();
    var tip = tipNow();
    var W = Math.round(mr.width), H = Math.max(180, Math.round(tip[1] + 26));
    fx.width = Math.round(W * dpr); fx.height = Math.round(H * dpr); fx.style.height = H + "px";
    var ty = Math.max(80, tip[1] * 0.55);
    var L = [{ text: lines[0], font: SANS, y: ty - 36, spacing: "2px" }, { text: lines[1], font: SERIF, y: ty }];
    var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (calm) {                                                         // no drifting particles: the words just arrive
      var c0 = null;
      anim = requestAnimationFrame(function still(now) {
        if (c0 === null) c0 = now;
        var a = clamp01((now - c0) / 900);
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);
        gfx.globalAlpha = a; drawText(gfx, L, W, 0.2, 7); gfx.globalAlpha = 1;
        if (a > 0.6 && onReady) { onReady(); onReady = null; }
        anim = a < 1 ? requestAnimationFrame(still) : null;
      });
      return;
    }

    // the crisp words live on their own layer so they can be revealed in a soft wave
    var tc = document.createElement("canvas"); tc.width = fx.width; tc.height = fx.height;
    var tg = tc.getContext("2d");

    var pts = textPoints(W, H, L, dpr), nT = pts.length;
    var N = Math.min(MAXP, nT + Math.max(110, MAXP - MAXT));
    var nF = N - nT;
    // line extents and rank ranges, for the reveal mask
    var ext = L.map(function () { return { x0: 1e9, x1: -1e9, r0: 1, r1: 0 }; });
    pts.forEach(function (q, i) {
      var e = ext[q.line], r = nT > 1 ? i / (nT - 1) : 0;
      if (q.x < e.x0) e.x0 = q.x; if (q.x > e.x1) e.x1 = q.x;
      if (r < e.r0) e.r0 = r; if (r > e.r1) e.r1 = r;
    });

    var P = [], ti = 0;
    for (var i = 0; i < N; i++) {
      // free wisps are spread evenly through the ribbon; the rest take the letters in birth order
      var free = nF > 0 && Math.floor((i + 1) * nF / N) !== Math.floor(i * nF / N);
      var tgt = free || ti >= nT ? null : pts[ti++];
      var r = tgt ? (ti - 1) / Math.max(1, nT - 1) : 0;
      var born = EMIT * Math.pow(i / N, 1.2);
      P.push({
        born: born, live: false, x: 0, y: 0, vx: 0, vy: 0, gx: 0, gy: 0,
        tx: tgt ? tgt.x : 0, ty: tgt ? tgt.y : 0, tgt: !!tgt,
        cs: tgt ? Math.max(born + 380, CAP0 + r * CAPW) : 1e9,
        spin: Math.random() < 0.5 ? -1 : 1, ph: Math.random() * 6.283, ox: Math.cos(i * 2.399), oy: Math.sin(i * 2.399) * 0.6,
        warm: 0, d0: -1, arr: -1, life: 2200 + Math.random() * 900
      });
    }

    var t0 = null, last = 0;
    var K = 150, C = 8.5;                       // spring: a little under-damped, so it settles after one loop

    // one physics step; the frame loop runs several when the page stalls, so motion keeps real time
    function move(t, dt, tip) {
      var ts = t / 1000, draft = 4 * Math.sin(ts * 0.7 + waves[0].p), i, p, age, g;
      for (i = 0; i < P.length; i++) {
        p = P[i]; age = t - p.born;
        if (age < 0) continue;
        if (!p.live) {
          p.live = true; p.x = tip[0] + (Math.random() - 0.5) * 0.8; p.y = tip[1] - 1;
          p.vx = 0; p.vy = -150;
        }
        g = t - p.cs;
        var ramp = smooth(0, 650, g);
        var h = tip[1] - p.y;                                           // height above the wick
        swirl(p.x, p.y, ts);
        var turb = smooth(3, 46, h) * (0.6 + 0.4 * clamp01(age / 700)) * (1 + 0.7 * smooth(50, 150, h)); // laminar at the wick, curling higher up
        var up = 38 + 125 * Math.exp(-age / 600);                        // fastest near the wick, slows as it cools
        var fvx = air[0] * turb + draft * smooth(10, 90, h), fvy = air[1] * turb - up;
        var ax = (1 - ramp) * 11 * (fvx - p.vx), ay = (1 - ramp) * 11 * (fvy - p.vy);
        if (ramp > 0) {
          var jit = 1.1 * Math.exp(-Math.max(0, g - 700) / 450) * smooth(500, 900, g);   // settle-jitter that dies out
          p.gx = p.tx + Math.sin(t * 0.019 + p.ph) * jit; p.gy = p.ty + Math.cos(t * 0.023 + p.ph * 1.3) * jit;
          var dx = p.gx - p.x, dy = p.gy - p.y, orbit = 0.75 * Math.exp(-g / 520) * p.spin;
          ax += ramp * (K * dx - C * p.vx - K * orbit * dy);
          ay += ramp * (K * dy - C * p.vy + K * orbit * dx);
          var d = Math.sqrt(dx * dx + dy * dy);
          if (p.d0 < 0) p.d0 = Math.max(4, d);
          p.warm = Math.max(p.warm, ramp * clamp01(1.15 - d / p.d0));
          if (p.arr < 0 && d < 1.8 && g > 250) p.arr = t;
        }
        p.vx += ax * dt; p.vy += ay * dt;
        var sp = p.vx * p.vx + p.vy * p.vy;
        if (sp > 360000) { var s = 600 / Math.sqrt(sp); p.vx *= s; p.vy *= s; }
        p.x += p.vx * dt; p.y += p.vy * dt;
        if (ramp > 0) {                                                 // the last of the way is guaranteed
          var lock = smooth(1000, 1550, g);
          if (lock > 0) { p.x += (p.gx - p.x) * lock; p.y += (p.gy - p.y) * lock; p.vx *= 1 - lock; p.vy *= 1 - lock; }
        }
      }
    }

    function frame(now) {
      if (t0 === null) { t0 = now; last = now; }
      var t = now - t0, raw = Math.min(0.066, Math.max(0.001, (now - last) / 1000)); last = now;
      var tip = tipNow(), i, p, age, g;
      gfx.setTransform(dpr, 0, 0, dpr, 0, 0);
      gfx.clearRect(0, 0, W, H);

      // ---- move (real time, in steps of at most 1/60 s) ----
      var steps = Math.min(4, Math.ceil(raw / 0.0167));
      for (var st = 1; st <= steps; st++) move(t - (steps - st) * raw * 1000 / steps, raw / steps, tip);

      // ---- the thin ribbon core: one continuous line through the free smoke, in birth order ----
      // Drawn as connected runs (never overlapping dabs) so it stays thin and grey. It starts dim
      // at the wick, where the flame's ember is cooling, thickens a little and fades as it ages.
      var fade = 1 - smooth(1500, 3400, t);
      if (fade > 0) {
        gfx.lineCap = "butt"; gfx.lineJoin = "round"; gfx.strokeStyle = "rgb(190,186,177)";
        var run = -1, open = false, al = 0, lw = 1;
        var stroke2 = function () {                                       // a soft halo, then the fine core
          gfx.globalAlpha = al * 0.22; gfx.lineWidth = lw * 4; gfx.stroke();
          gfx.globalAlpha = al; gfx.lineWidth = lw; gfx.stroke();
        };
        for (i = 1; i <= P.length; i++) {
          var a0 = P[i - 1], a1 = P[i], ok = !!a1 && a0.live && a1.live && t - a0.cs < 120 && t - a1.cs < 120;
          if (ok) { var ddx = a1.x - a0.x, ddy = a1.y - a0.y; ok = ddx * ddx + ddy * ddy < 196; }
          var bk = ok ? Math.min(13, Math.floor((t - a1.born) / 180)) : -1;
          if (open && bk !== run) { stroke2(); open = false; }
          if (!ok) continue;
          if (!open) {
            al = 0.22 * smooth(0, 3, bk + 0.3) * Math.pow(1 - bk / 14, 1.3) * fade; lw = 0.8 + bk * 0.24;
            gfx.beginPath(); gfx.moveTo(a0.x, a0.y); open = true; run = bk;
          }
          gfx.lineTo(a1.x, a1.y);
        }
        if (open) stroke2();
      }

      // ---- smoke, and the ink the embers cool into (normal blending) ----
      var busy = false, late = 1 - smooth(2500, 3700, t);
      for (i = 0; i < P.length; i++) {
        p = P[i]; if (!p.live) { busy = true; continue; }
        age = t - p.born; g = t - p.cs;
        var ramp2 = smooth(0, 650, g), dis = smooth(REVEAL, REVEAL + 600, g);
        var smokeA = 0.013 * smooth(0, 450, age) * Math.pow(clamp01(1 - age / p.life), 1.5) * (1 - ramp2) * late;
        if (p.tgt) busy = busy || dis < 1; else busy = busy || smokeA > 0.004;
        if (smokeA > 0.004) {
          var sz = 4 + 24 * (1 - Math.exp(-age / 1000));
          sz = sz * (1 - ramp2) + 2.6 * ramp2;
          var sp2 = 10 * (age / 1000) * (1 - ramp2);                      // the haze diffuses off the core
          gfx.globalAlpha = smokeA; gfx.drawImage(SMOKE, p.x + p.ox * sp2 - sz / 2, p.y + p.oy * sp2 - sz / 2, sz, sz);
        }
        if (p.tgt) {
          var inkA = 0.55 * smooth(950, 1450, g) * (1 - dis);
          if (inkA > 0.004) { gfx.globalAlpha = inkA; gfx.drawImage(INK, p.x - 2.4, p.y - 2.4, 4.8, 4.8); }
        }
      }

      // ---- embers (additive: overlapping embers bloom, as fire does) ----
      gfx.globalCompositeOperation = "lighter";
      for (i = 0; i < P.length; i++) {
        p = P[i]; if (!p.live) continue;
        age = t - p.born; g = t - p.cs;
        var ea = 0, es = 4;
        if (p.tgt && p.warm > 0) {
          var flare = p.arr >= 0 ? Math.exp(-(t - p.arr) / 280) : 0;
          ea = Math.max(ea, (0.34 * p.warm + 0.45 * flare) * (1 - smooth(950, 1450, g)));
          es = 5 + 6 * flare;
        }
        if (ea > 0.004) { gfx.globalAlpha = Math.min(1, ea); gfx.drawImage(EMBER, p.x - es / 2, p.y - es / 2, es, es); }
      }
      gfx.globalCompositeOperation = "source-over";

      // ---- the crisp words, revealed in the same wave; the glow cools to a faint warmth ----
      var cooled = Math.exp(-Math.max(0, t - 3000) / 650);
      var glow = 0.2 + 0.55 * cooled, blur = 7 + 10 * cooled;
      if (t > CAP0 + REVEAL - 50) {
        tg.setTransform(dpr, 0, 0, dpr, 0, 0); tg.clearRect(0, 0, W, H);
        drawText(tg, L, W, glow, blur);
        if (t < CAP0 + CAPW + REVEAL + REVEAL_FADE + 40) {
          tg.globalCompositeOperation = "destination-out";
          var split = L[1].y - 25;                                       // between the small line and the big line's ascenders
          L.forEach(function (ln, k) {
            var e = ext[k]; if (e.x1 < e.x0) return;
            function hidden(rr) { return 1 - smooth(0, REVEAL_FADE, t - (CAP0 + rr * CAPW + REVEAL)); }
            var gr = tg.createLinearGradient(e.x0 - 2, 0, e.x1 + 2, 0);
            for (var s = 0; s <= 12; s++) gr.addColorStop(s / 12, "rgba(0,0,0," + hidden(e.r0 + (e.r1 - e.r0) * s / 12).toFixed(3) + ")");
            var top = k === 0 ? 0 : split, bot = k === 0 ? split : H;
            tg.fillStyle = gr; tg.fillRect(0, top, W, bot - top);
          });
          tg.globalCompositeOperation = "source-over";
        }
        gfx.setTransform(1, 0, 0, 1, 0, 0); gfx.globalAlpha = 1;
        gfx.drawImage(tc, 0, 0);
      }
      gfx.globalAlpha = 1;

      if (t > READY && onReady) { onReady(); onReady = null; }
      if (t < END || busy) anim = requestAnimationFrame(frame);
      else {                                                            // rest: just the words, faintly warm
        anim = null;
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);
        drawText(gfx, L, W, glow, blur);
      }
    }
    anim = requestAnimationFrame(frame);
  }

  function clear() {
    if (anim) { cancelAnimationFrame(anim); anim = null; }
    if (gfx) {
      gfx.setTransform(1, 0, 0, 1, 0, 0); gfx.globalAlpha = 1; gfx.globalCompositeOperation = "source-over";
      gfx.clearRect(0, 0, fx.width, fx.height);
    }
  }

  function init(canvas, mainEl, candleEl) {
    fx = canvas; gfx = fx.getContext("2d"); main = mainEl; candle = candleEl;
    wick = candle.querySelector(".wick");
  }

  return { init: init, morph: morph, clear: clear };
})();
