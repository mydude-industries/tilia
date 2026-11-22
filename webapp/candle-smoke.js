// The smoke that becomes the follow-up. Owned by the smoke module: candle.js only calls
// CandleSmoke.init(canvas, main, candle), CandleSmoke.morph([small, big] | [big], onReady),
// CandleSmoke.rise(text, onGather, onDone), CandleSmoke.gather() -> stop,
// CandleSmoke.speak(line, onDone, holdMs) and CandleSmoke.clear().
// rise is the morph backwards: their words come apart into motes that fall into the flame's
// own shape and become it. onGather(frac, fade) is called every frame with how much of the
// words has arrived (the flame grows by it, and returns its size); onDone (lighting it)
// runs once the last has merged.
// gather is a thin wisp off the lit flame while Tilia is asked; speak spells Tilia's line
// from the flame's tip, holds it holdMs, and lets it drift away (onDone when it's gone).
"use strict";

window.CandleSmoke = (function () {
  // One thin ribbon of smoke leaves the wick tip and is carried by a smooth, slowly
  // changing swirl, so neighbouring particles move together and the ribbon curls, folds
  // and thins as it cools. Then the ribbon is gathered, oldest end first, into the letters:
  // each particle is pulled in on a soft spring, circles once, settles, warms to ember,
  // flares as it lands, and cools to ink while the crisp words are revealed beneath it in
  // the same left-to-right wave. The words keep a faint warmth.
  var fx, gfx, main, candle, wick, anim = null;
  var SANS_FACE = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  var SERIF_FACE = '"New York", ui-serif, "Iowan Old Style", Charter, Georgia, serif';
  var SANS = "600 13px " + SANS_FACE;
  var MAXP = 1100, MAXT = 960;             // particles in all; at most this many become letters
  var EMIT = 1550;                          // ms the wick keeps smoking
  var RISE_PACE = 1.8;                      // the words→flame rise runs this many times slower than real time
  var CAP0 = 1300, CAPW = 1300;             // the gathering wave: first start, spread across the words
  var REVEAL = 1300, REVEAL_FADE = 520;     // crisp text shows this long after a spot starts gathering
  var READY = 3800, END = 4900;
  var LEAVE_W = 1100, LEAVE_FADE = 520;     // a held line comes apart left to right across this long
  var DRIFT = 2600;                         // and its motes drift off as smoke for this long

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
  var FLAME = sprite("255,214,112", HOT);                 // the flame's yellow body (rise)

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
      g.fillStyle = ln.color || (k === 0 ? "#8c877b" : "#ece7da");
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

  // ---- fitting words to the width: a smaller size first, then a cut with an ellipsis ----
  var meas = null;
  function widthOf(text, font, spacing) {
    meas = meas || document.createElement("canvas").getContext("2d");
    meas.font = font;
    return meas.measureText(text).width + (parseFloat(spacing) || 0) * Array.from(text).length;
  }
  function fitSans(text, maxW) {
    var px = 13, spacing = "2px";
    while (widthOf(text, "600 " + px + "px " + SANS_FACE, spacing) > maxW) {
      if (spacing !== "0px") spacing = parseFloat(spacing) > 1 ? "1px" : "0px";
      else if (px > 10) px -= 0.5;
      else break;
    }
    var font = "600 " + px + "px " + SANS_FACE;
    if (widthOf(text, font, spacing) <= maxW) return { text: text, font: font, spacing: spacing };
    var chars = Array.from(text);                                   // only when even the smallest won't fit
    while (chars.length > 1 && widthOf(chars.join("") + "\u2026", font, spacing) > maxW) chars.pop();
    return { text: chars.join("").trim() + "\u2026", font: font, spacing: spacing };
  }
  // their words, in the serif: one line if it fits at a readable size, else two
  function fitSerif(text, maxW, maxPx) {
    for (var px = maxPx || 30; px >= 19; px -= 1) {
      var f = "500 " + px + "px " + SERIF_FACE;
      if (widthOf(text, f) <= maxW) return { font: f, lines: [text], px: px };
    }
    var words = text.split(" "), best = null;
    for (var k = 1; k < words.length; k++) {
      var a = words.slice(0, k).join(" "), b = words.slice(k).join(" ");
      var m = Math.max(a.length, b.length);
      if (!best || m < best.m) best = { m: m, lines: [a, b] };
    }
    var lines = best ? best.lines : [text];
    for (px = Math.min(26, maxPx || 26); px >= 14; px -= 1) {
      f = "500 " + px + "px " + SERIF_FACE;
      if (lines.every(function (ln) { return widthOf(ln, f) <= maxW; })) return { font: f, lines: lines, px: px };
    }
    return { font: "500 14px " + SERIF_FACE, lines: lines, px: 14 };
  }

  // the canvas over the page, from the top of main to just below the wick; resized only
  // when it has to be (resizing wipes it, and the wisp may be drawing)
  var geo = { dpr: 1, W: 0, H: 0 };
  function fit() {
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var mr = main.getBoundingClientRect();
    var tip = tipNow();
    var W = Math.round(mr.width), H = Math.max(180, Math.round(tip[1] + 26));
    if (fx.width !== Math.round(W * dpr) || fx.height !== Math.round(H * dpr)) {
      fx.width = Math.round(W * dpr); fx.height = Math.round(H * dpr);
    }
    fx.style.height = H + "px";
    geo = { dpr: dpr, W: W, H: H };
    return { dpr: dpr, W: W, H: H, tip: tip };
  }
  function reduced() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // blow-out: [their words, Tilia's sealed line] (their words small above, her line in the
  // serif, on two rows if it needs them), or [their words] alone, in the serif
  function morph(lines, onReady) {
    clear();
    newAir();
    var c = fit();
    var ty = Math.max(80, c.tip[1] * 0.55);
    var big = String(lines.length > 1 ? lines[1] : lines[0]);
    var fs = fitSerif(big, c.W - 40, 30), gap = fs.px * 1.3;
    var y0 = ty - (fs.lines.length - 1) * gap / 2;
    var L = fs.lines.map(function (ln, k) {
      return { text: ln, font: fs.font, color: "#ece7da", y: y0 + k * gap };
    });
    L.forEach(function (ln, k) { ln.top = k === 0 ? y0 - fs.px * 0.83 : (L[k - 1].y + ln.y) / 2; });
    if (lines.length > 1) {
      var small = fitSans(String(lines[0]), c.W - 24);                     // their own words can be long
      L.unshift({ text: small.text, font: small.font, y: y0 - Math.round(fs.px * 0.9 + 9),
                  spacing: small.spacing, color: "#8c877b" });
    }
    L[0].top = 0;
    form(c, L, { onReady: onReady });
  }

  // ---- speak: Tilia's line, in the smoke above the lit flame. The wisp that waited fades
  // as a ribbon leaves the flame's tip and gathers into the line (the same morph as at
  // blow-out); it holds long enough to read, then comes apart left to right and drifts
  // away as smoke. onDone once it has gone.
  function speak(line, onDone, hold) {
    if (anim) { cancelAnimationFrame(anim); anim = null; }
    if (wisp && wisp.stop < 0) wisp.stop = wisp.now;                       // the wait is over
    if (!waves.length) newAir();                                           // the same air the wisp is in
    var c = fit();
    var ty = Math.max(80, c.tip[1] * 0.5);
    var fs = fitSerif(String(line), c.W - 40, 25), gap = fs.px * 1.3;
    // a line that wraps must still clear the flame: lift the block until its last row sits
    // above the flame's tip, never above the top of the room
    var low = ty + (fs.lines.length - 1) * gap / 2 + fs.px * 0.6, clear = c.tip[1] - C0().FLAME_H - 14;
    if (low > clear) ty = Math.max(fs.px + 8 + (fs.lines.length - 1) * gap / 2, ty - (low - clear));
    var L = fs.lines.map(function (ln, k) {
      return { text: ln, font: fs.font, color: "#ece7da", y: ty - (fs.lines.length - 1) * gap / 2 + k * gap };
    });
    L.forEach(function (ln, k) { ln.top = k === 0 ? 0 : (L[k - 1].y + ln.y) / 2; });
    form(c, L, { lift: C0().FLAME_H + 4, hold: hold > 0 ? hold : 3500, onDone: onDone });
  }

  // Smoke into words: a ribbon leaves the wick (or `lift` px above it, the flame's tip),
  // and is gathered into L. o.onReady once the words can be read. With o.hold, the words
  // stay that long once formed, then drift off as smoke and o.onDone runs.
  function form(c, L, o) {
    var dpr = c.dpr, W = c.W, H = c.H, lift = o.lift || 0, onReady = o.onReady, onDone = o.onDone;
    var hold = o.hold || 0;
    var calm = reduced();
    function done() { if (onDone) { var d = onDone; onDone = null; d(); } }

    if (calm) {                                                         // no drifting particles: the words just arrive
      var c0 = null;
      anim = requestAnimationFrame(function still(now) {
        if (c0 === null) c0 = now;
        var t = now - c0, a = clamp01(t / 900);
        if (hold) a *= 1 - clamp01((t - 900 - hold) / 900);             // and, when it's a line, leave
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);
        gfx.globalAlpha = a; drawText(gfx, L, W, 0.2, 7); gfx.globalAlpha = 1;
        if (a > 0.6 && onReady) { onReady(); onReady = null; }
        var more = hold ? t < 1800 + hold : a < 1;
        anim = more ? requestAnimationFrame(still) : null;
        if (!more && hold) { gfx.clearRect(0, 0, W, H); done(); }
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
    // a held line: once formed it holds, then leaves in the same left-to-right wave it came in
    var SHOWN = CAP0 + CAPW + REVEAL + REVEAL_FADE;
    var REL = hold ? SHOWN + hold : 1e12, GONE = REL + LEAVE_W + DRIFT;

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
        rl: tgt ? REL + r * LEAVE_W + Math.random() * 90 : 1e12, gone: -1,
        spin: Math.random() < 0.5 ? -1 : 1, ph: Math.random() * 6.283, ox: Math.cos(i * 2.399), oy: Math.sin(i * 2.399) * 0.6,
        warm: 0, d0: -1, arr: -1, life: 2200 + Math.random() * 900
      });
    }

    var t0 = null, last = 0;
    var K = 150, C = 8.5;                       // spring: a little under-damped, so it settles after one loop

    // one physics step; the frame loop runs several when the page stalls, so motion keeps real time
    function move(t, dt, tip) {
      var ts = t / 1000, draft = 4 * Math.sin(ts * 0.7 + waves[0].p), i, p, age, g;
      var src = tip[1] - lift;
      for (i = 0; i < P.length; i++) {
        p = P[i]; age = t - p.born;
        if (age < 0) continue;
        if (!p.live) {
          p.live = true; p.x = tip[0] + (Math.random() - 0.5) * 0.8; p.y = src - 1;
          p.vx = 0; p.vy = -150;
        }
        if (t >= p.rl) {                                                // leaving: a letter's mote, loose in the air again
          if (p.gone < 0) { p.gone = t; p.vx = 0; p.vy = 0; }
          var la = t - p.gone, loose = smooth(0, 900, la);
          swirl(p.x, p.y, ts);
          var lvx = air[0] * 0.55 * loose + draft * 0.5, lvy = air[1] * 0.55 * loose - (10 + 16 * loose);
          p.vx += 3 * (lvx - p.vx) * dt; p.vy += 3 * (lvy - p.vy) * dt;
          p.x += p.vx * dt; p.y += p.vy * dt;
          continue;
        }
        g = t - p.cs;
        var ramp = smooth(0, 650, g);
        var h = src - p.y;                                              // height above where it left
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
      if (wisp) wispTick(now);                                          // the wait's wisp, fading behind

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
        if (p.gone >= 0) {                                              // a letter coming apart: ink, then smoke
          var la = t - p.gone, out = 1 - smooth(DRIFT * 0.35, DRIFT, la);
          var inkL = 0.5 * Math.exp(-la / 300);
          if (inkL > 0.004) { gfx.globalAlpha = inkL; gfx.drawImage(INK, p.x - 2.4, p.y - 2.4, 4.8, 4.8); }
          var smL = 0.04 * smooth(0, 400, la) * out, szL = 4 + 20 * smooth(0, DRIFT * 0.7, la);
          if (smL > 0.004) { gfx.globalAlpha = smL; gfx.drawImage(SMOKE, p.x - szL / 2, p.y - szL / 2, szL, szL); }
          continue;
        }
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
        p = P[i]; if (!p.live || p.gone >= 0) continue;
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
      var leaving = t > REL - 50;
      if (t > CAP0 + REVEAL - 50 && t < REL + LEAVE_W + LEAVE_FADE + 200) {
        tg.setTransform(dpr, 0, 0, dpr, 0, 0); tg.clearRect(0, 0, W, H);
        drawText(tg, L, W, glow, blur);
        if (t < CAP0 + CAPW + REVEAL + REVEAL_FADE + 40 || leaving) {
          tg.globalCompositeOperation = "destination-out";
          L.forEach(function (ln, k) {
            var e = ext[k]; if (e.x1 < e.x0) return;
            function hidden(rr) {
              var h0 = 1 - smooth(0, REVEAL_FADE, t - (CAP0 + rr * CAPW + REVEAL));
              return leaving ? Math.max(h0, smooth(0, LEAVE_FADE, t - (REL + rr * LEAVE_W))) : h0;
            }
            var gr = tg.createLinearGradient(e.x0 - 2, 0, e.x1 + 2, 0);
            for (var s = 0; s <= 12; s++) gr.addColorStop(s / 12, "rgba(0,0,0," + hidden(e.r0 + (e.r1 - e.r0) * s / 12).toFixed(3) + ")");
            var top = ln.top || 0, bot = k + 1 < L.length ? L[k + 1].top : H;
            tg.fillStyle = gr; tg.fillRect(0, top, W, bot - top);
          });
          tg.globalCompositeOperation = "source-over";
        }
        gfx.setTransform(1, 0, 0, 1, 0, 0); gfx.globalAlpha = 1;
        gfx.drawImage(tc, 0, 0);
      }
      gfx.globalAlpha = 1;

      if (t > READY && onReady) { onReady(); onReady = null; }
      if (hold) {                                                       // a line: it holds, leaves, and is gone
        if (t < GONE) { anim = requestAnimationFrame(frame); return; }
        anim = null;
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);
        if (wisp && !wispAnim) wispAnim = requestAnimationFrame(wispLoop);
        done();
        return;
      }
      if (t < END || busy) anim = requestAnimationFrame(frame);
      else {                                                            // rest: just the words, faintly warm
        anim = null;
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);
        drawText(gfx, L, W, glow, blur);
      }
    }
    anim = requestAnimationFrame(frame);
  }

  // ---- gather: while Tilia is asked, a thin, calm wisp rises off the flame's tip, the
  // way a candle smokes a little in still air. It never flickers: one slow ribbon, bent
  // by the same smooth air as the smoke, fading as it climbs. gather() returns stop(),
  // which lets it thin and fade out (speak stops it too). Nothing with reduced motion.
  var wisp = null, wispAnim = null;
  var WISP_LIFE = 3500, WISP_EVERY = 50;
  function C0() { return window.CandleCore || { FLAME_H: 47 }; }

  function gather() {
    if (!fx || reduced()) return function () {};
    if (!waves.length) newAir();
    if (!anim) fit();
    if (wisp) wisp.stop = -1;                                          // asked again while it fades: keep it
    else wisp = { P: [], t0: null, last: 0, next: 0, now: 0, stop: -1 };
    if (!anim && !wispAnim) wispAnim = requestAnimationFrame(wispLoop);
    var mine = wisp;
    return function stop() { if (wisp === mine && wisp.stop < 0) wisp.stop = wisp.now; };
  }

  function wispLoop(now) {
    wispAnim = null;
    if (!wisp || anim) return;                                          // a morph running draws it itself
    gfx.setTransform(geo.dpr, 0, 0, geo.dpr, 0, 0);
    gfx.clearRect(0, 0, geo.W, geo.H);
    wispTick(now);
    if (wisp) wispAnim = requestAnimationFrame(wispLoop);
    else gfx.clearRect(0, 0, geo.W, geo.H);
  }

  // one step and one drawing of the wisp, in CSS px (the caller has set the transform)
  function wispTick(now) {
    var w = wisp;
    if (w.t0 === null) { w.t0 = now; w.last = now; }
    var t = now - w.t0, dt = Math.min(0.05, Math.max(0.001, (now - w.last) / 1000));
    w.last = now; w.now = t;
    var tip = tipNow(), sx = tip[0], sy = tip[1] - C0().FLAME_H * 0.92, ts = t / 1000;
    if (w.stop < 0) {
      if (t - w.next > 400) w.next = t - 400;                           // a stalled page doesn't burst
      while (w.next <= t) {
        w.P.push({ x: sx, y: sy, vx: 0, vy: -26, born: w.next });
        w.next += WISP_EVERY;
      }
    }
    var i, p, keep = [];
    for (i = 0; i < w.P.length; i++) {
      p = w.P[i];
      var age = t - p.born;
      if (age > WISP_LIFE) continue;
      var h = sy - p.y;
      swirl(p.x, p.y, ts * 0.6);
      var turb = 0.32 * smooth(6, 90, h);                               // straight off the tip, a slow curl higher up
      var fvx = air[0] * turb, fvy = air[1] * turb - (26 - 8 * smooth(0, WISP_LIFE, age));
      p.vx += 2.5 * (fvx - p.vx) * dt; p.vy += 2.5 * (fvy - p.vy) * dt;
      p.x += p.vx * dt; p.y += p.vy * dt;
      keep.push(p);
    }
    w.P = keep;
    var master = smooth(0, 2200, t) * (w.stop < 0 ? 1 : 1 - smooth(w.stop, w.stop + 1400, t));
    if (master <= 0.001) {
      if (w.stop >= 0) wisp = null;
      return;
    }
    gfx.lineCap = "round"; gfx.lineJoin = "round"; gfx.strokeStyle = "rgb(196,191,181)";
    for (i = 1; i < w.P.length; i++) {
      var a = w.P[i - 1], b = w.P[i], u = (t - b.born) / WISP_LIFE;
      var al = 0.17 * smooth(60, 700, t - b.born) * Math.pow(clamp01(1 - u), 1.5) * master;
      if (al < 0.003) continue;
      var lw = 0.7 + 1.5 * u;
      gfx.beginPath(); gfx.moveTo(a.x, a.y); gfx.lineTo(b.x, b.y);
      gfx.globalAlpha = al * 0.22; gfx.lineWidth = lw * 4; gfx.stroke();
      gfx.globalAlpha = al; gfx.lineWidth = lw; gfx.stroke();
    }
    gfx.globalAlpha = 1;
  }

  // ---- rise: their words become the flame, on one timeline. The words come apart, left
  // to right, into motes; each mote is given a place inside the flame's own shape (sampled
  // once, base first) and pulled to it on a damped spring that stiffens as it goes, so it
  // lifts off like smoke, falls with real momentum, overshoots a little and settles. As a
  // mote reaches its place it warms from smoke-grey through ember to the flame's yellow and
  // fades into the flame, and the flame grows by what has arrived (onGather(frac) returns
  // the flame's size now, so later motes aim at the flame as it has grown). The first to
  // land spark the wick; onDone (the candle is lit) runs once the last has merged. No mote
  // vanishes and no flame pops in: the light only moves from the motes into the flame.
  function rise(text, onGather, onDone) {
    clear();
    newAir();
    var C = window.CandleCore;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var mr = main.getBoundingClientRect();
    var tip = tipNow();
    var W = Math.round(mr.width), H = Math.max(180, Math.round(tip[1] + 26));
    fx.width = Math.round(W * dpr); fx.height = Math.round(H * dpr); fx.style.height = H + "px";
    var ty = Math.max(80, tip[1] * 0.55);
    var fit = fitSerif(text, W - 32), gap = fit.px * 1.3;
    var L = fit.lines.map(function (ln, k) {
      return { text: ln, font: fit.font, y: ty - (fit.lines.length - 1) * gap / 2 + k * gap };
    });
    var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function gathered(frac, fade) { var g = onGather ? onGather(frac, fade) : frac; return isFinite(g) ? g : frac; }
    function finish() { if (onDone) { var d = onDone; onDone = null; d(); } }
    function drawWords(g, a) {
      g.textAlign = "center"; g.textBaseline = "middle"; g.globalAlpha = a;
      g.shadowColor = "rgba(255,168,64,0.2)"; g.shadowBlur = 7; g.fillStyle = "#ece7da";
      L.forEach(function (ln) { g.font = ln.font; g.fillText(ln.text, W / 2, ln.y); });
      g.shadowBlur = 0; g.shadowColor = "transparent"; g.globalAlpha = 1;
    }
    if (calm) {                                                         // no drifting: the words crossfade into the flame
      var c0 = null;
      anim = requestAnimationFrame(function still(now) {
        if (c0 === null) c0 = now;
        var a = 1 - clamp01((now - c0 - 500) / 900);
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);
        drawWords(gfx, a);
        gathered(1, 1 - a);
        if (a <= 0) { anim = null; finish(); return; }
        anim = requestAnimationFrame(still);
      });
      return;
    }

    var pts = textPoints(W, H, L, dpr), n = pts.length;
    var HOLD = 650, SPREAD = 900;                                      // words show, then come apart
    var LOCK = 2100, MERGE = 260;                                      // by LOCK every mote is home; it fades in MERGE ms
    var places = C.teardropPoints(n, 7);
    var P = pts.map(function (q, i) {
      var r = n > 1 ? i / (n - 1) : 0;
      return {
        x: q.x, y: q.y, vx: (Math.random() - 0.5) * 24, vy: -18 - Math.random() * 30,  // lifts off like smoke
        go: HOLD + r * SPREAD + Math.random() * 120, at: places[i],
        d0: -1, heat: 0, m: 0
      };
    });
    // the crisp words are hidden in the same left-to-right wave the motes leave in
    var tc = document.createElement("canvas"); tc.width = fx.width; tc.height = fx.height;
    var tgc = tc.getContext("2d");
    var x0 = 1e9, x1 = -1e9;
    pts.forEach(function (q) { if (q.x < x0) x0 = q.x; if (q.x > x1) x1 = q.x; });
    var t0 = null, last = 0, grow = 0, merged = 0;

    // one physics step (the frame loop runs several when the page stalls, so motion keeps real time)
    function move(t, dt, tip) {
      var ts = t / 1000, i, p, g, q, k, c, dx, dy, d, loose, lock;
      for (i = 0; i < n; i++) {
        p = P[i]; g = t - p.go;
        if (g < 0 || p.m >= 1) continue;
        q = C.teardropAt(p.at, grow);
        var tx = tip[0] + q.x, ty2 = tip[1] + q.y;
        k = 4 + 66 * smooth(80, 1150, g); c = 2 * 0.52 * Math.sqrt(k);    // a soft start, then a firm, slightly springy pull
        dx = tx - p.x; dy = ty2 - p.y;
        swirl(p.x, p.y, ts);
        loose = 7 * (1 - p.heat) * (1 - smooth(500, 1300, g));             // the air carries them early on, not near the flame
        p.vx += (k * dx - c * p.vx + loose * air[0]) * dt;
        p.vy += (k * dy - c * p.vy + loose * air[1]) * dt;
        var sp = p.vx * p.vx + p.vy * p.vy;
        if (sp > 360000) { var s2 = 600 / Math.sqrt(sp); p.vx *= s2; p.vy *= s2; }
        p.x += p.vx * dt; p.y += p.vy * dt;
        lock = smooth(LOCK - 500, LOCK, g);                                // the last of the way is guaranteed
        if (lock > 0) { p.x += (tx - p.x) * lock * 0.25; p.y += (ty2 - p.y) * lock * 0.25; p.vx *= 1 - lock * 0.25; p.vy *= 1 - lock * 0.25; }
        dx = tx - p.x; dy = ty2 - p.y; d = Math.sqrt(dx * dx + dy * dy);
        if (p.d0 < 0) p.d0 = Math.max(6, d);
        p.heat = Math.max(p.heat, clamp01(1.12 - d / p.d0));
        if (p.m > 0 || (d < 3.5 && g > 300) || g > LOCK) {                 // home: fade into the flame
          var dm = Math.min(1 - p.m, dt * 1000 / MERGE);
          p.m += dm; merged += dm;
        }
      }
    }

    function frame(now) {
      if (t0 === null) { t0 = now; last = now; }
      // RISE_PACE slows the whole rise evenly (the owner: "too fast"); physics and timeline
      // scale together, so the motion keeps its shape, only slower.
      var t = (now - t0) / RISE_PACE, raw = Math.min(0.066, Math.max(0.001, (now - last) / 1000)) / RISE_PACE; last = now;
      var tip = tipNow(), i, p;
      gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H);

      var steps = Math.min(4, Math.ceil(raw / 0.0167));
      for (var st = 1; st <= steps; st++) move(t - (steps - st) * raw * 1000 / steps, raw / steps, tip);
      var frac = n ? merged / n : 1;
      if (frac > 0.9999) frac = 1;
      grow = gathered(frac, 1);

      // the words, wiped away left to right as their motes leave
      if (t < HOLD + SPREAD + 500 && n) {
        tgc.setTransform(dpr, 0, 0, dpr, 0, 0); tgc.clearRect(0, 0, W, H);
        drawWords(tgc, smooth(0, 260, t));
        tgc.globalCompositeOperation = "destination-out";
        var gr = tgc.createLinearGradient(x0 - 2, 0, x1 + 2, 0);
        for (var k = 0; k <= 12; k++) {
          var gone = smooth(0, 380, t - (HOLD + (k / 12) * SPREAD) + 60);
          gr.addColorStop(k / 12, "rgba(0,0,0," + gone.toFixed(3) + ")");
        }
        tgc.fillStyle = gr; tgc.fillRect(0, 0, W, H);
        tgc.globalCompositeOperation = "source-over";
        gfx.setTransform(1, 0, 0, 1, 0, 0); gfx.drawImage(tc, 0, 0);
        gfx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      // the motes: smoke-grey, warming to ember, then the flame's yellow as they arrive
      gfx.globalCompositeOperation = "lighter";
      for (i = 0; i < n; i++) {
        p = P[i];
        if (t < p.go || p.m >= 1) continue;
        var h = p.heat, a = (1 - p.m) * (0.5 - 0.22 * h);
        var size = 4 - 1.6 * h;
        var lo = h < 0.55 ? INK : EMBER, hi = h < 0.55 ? EMBER : FLAME;
        var f = h < 0.55 ? smooth(0.1, 0.55, h) : smooth(0.6, 0.95, h);
        if (a * (1 - f) > 0.004) { gfx.globalAlpha = a * (1 - f); gfx.drawImage(lo, p.x - size / 2, p.y - size / 2, size, size); }
        if (a * f > 0.004) { gfx.globalAlpha = a * f; gfx.drawImage(hi, p.x - size / 2, p.y - size / 2, size, size); }
      }
      gfx.globalCompositeOperation = "source-over"; gfx.globalAlpha = 1;

      if (frac >= 1) { anim = null; gfx.setTransform(dpr, 0, 0, dpr, 0, 0); gfx.clearRect(0, 0, W, H); finish(); return; }
      anim = requestAnimationFrame(frame);
    }
    anim = requestAnimationFrame(frame);
  }

  function clear() {
    if (anim) { cancelAnimationFrame(anim); anim = null; }
    if (wispAnim) { cancelAnimationFrame(wispAnim); wispAnim = null; }
    wisp = null;
    if (gfx) {
      gfx.setTransform(1, 0, 0, 1, 0, 0); gfx.globalAlpha = 1; gfx.globalCompositeOperation = "source-over";
      gfx.clearRect(0, 0, fx.width, fx.height);
    }
  }

  function init(canvas, mainEl, candleEl) {
    fx = canvas; gfx = fx.getContext("2d"); main = mainEl; candle = candleEl;
    wick = candle.querySelector(".wick");
  }

  return { init: init, morph: morph, rise: rise, gather: gather, speak: speak, clear: clear };
})();
