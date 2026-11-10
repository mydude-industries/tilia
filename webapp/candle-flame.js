// The flame. Owned by the flame module: candle.js only calls CandleFlame.init(candleEl),
// CandleFlame.setState("unlit" | "lit" | "out") and CandleFlame.setBreath(lean, squash),
// where lean is 0..55 (degrees away from the breath; positive bends the flame to the left)
// and squash is 1 (none) down to 0.35.
//
// Drawn on one small canvas laid over the wick. The flame is a bent spine with a teardrop
// width profile; soft pre-rendered discs are stamped along it in layers (blue cup, orange
// mantle, yellow body, white core) so it has no hard edges and bends like a real flame.
// Its life is 1-D value noise, never a loop. The canvas also carries the light the flame
// throws on the top of the wax, the spark when it is lit and the ember when it goes out.
"use strict";

window.CandleFlame = (function () {
  var W = 140, H = 220;          // canvas size in CSS px
  var OX = 70, OY = 150;         // wick tip inside the canvas
  var cv, ctx, dpr = 1, old;
  var wax = null;                // wax top in canvas coords: { l, r, top }
  var state = "unlit", running = false, last = 0, T = 0;
  var litAt = 0, outAt = 0;
  var leanT = 0, squashT = 1, lean = 0, squash = 1, outDir = 1;
  var sparks = [];
  var spr = {};

  // ---- noise -------------------------------------------------------------------------
  var P = new Float32Array(256);
  (function () { var s = 1234567; for (var i = 0; i < 256; i++) { s = (s * 16807) % 2147483647; P[i] = s / 2147483647; } })();
  function vn(x) {
    var i = Math.floor(x), f = x - i;
    var a = P[i & 255], b = P[(i + 1) & 255];
    f = f * f * (3 - 2 * f);
    return a + (b - a) * f;
  }
  function fbm(x) { return (vn(x) * 0.6 + vn(x * 2.13 + 17.1) * 0.28 + vn(x * 4.37 + 41.7) * 0.12) * 2 - 1; } // -1..1
  function sstep(a, b, x) { x = (x - a) / (b - a); x = x < 0 ? 0 : x > 1 ? 1 : x; return x * x * (3 - 2 * x); }
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function backOut(x) { var c1 = 1.5, c3 = c1 + 1; x -= 1; return 1 + c3 * x * x * x + c1 * x * x; }

  // ---- sprites -----------------------------------------------------------------------
  function disc(size, rgb, stops) {
    var c = document.createElement("canvas");
    c.width = c.height = size;
    var g = c.getContext("2d"), h = size / 2;
    var gr = g.createRadialGradient(h, h, 0, h, h, h);
    for (var i = 0; i < stops.length; i += 2) gr.addColorStop(stops[i], "rgba(" + rgb + "," + stops[i + 1] + ")");
    g.fillStyle = gr;
    g.fillRect(0, 0, size, size);
    return c;
  }
  function makeSprites() {
    var body = [0, 1, 0.35, 0.92, 0.62, 0.5, 0.82, 0.16, 1, 0];
    spr.blue = disc(64, "62,112,255", [0, 0.9, 0.4, 0.55, 0.75, 0.15, 1, 0]);
    spr.deep = disc(64, "232,92,26", body);
    spr.orange = disc(64, "255,150,48", body);
    spr.yellow = disc(64, "255,214,112", body);
    spr.core = disc(64, "255,250,232", [0, 1, 0.3, 0.85, 0.65, 0.3, 1, 0]);
    spr.halo = disc(256, "255,150,62", [0, 0.55, 0.12, 0.34, 0.3, 0.15, 0.55, 0.05, 1, 0]);
    spr.glow = disc(128, "255,190,110", [0, 0.8, 0.25, 0.45, 0.6, 0.12, 1, 0]);
    spr.spark = disc(32, "255,244,214", [0, 1, 0.25, 0.9, 0.5, 0.35, 1, 0]);
    spr.ember = disc(32, "255,120,40", [0, 1, 0.3, 0.8, 0.6, 0.25, 1, 0]);
    spr.heat = disc(32, "255,210,160", [0, 0.6, 0.5, 0.25, 1, 0]);
  }
  function stamp(s, x, y, r, a) {
    if (a <= 0.003 || r <= 0.05) return;
    ctx.globalAlpha = a > 1 ? 1 : a;
    ctx.drawImage(s, x - r, y - r, r * 2, r * 2);
  }

  // ---- geometry ----------------------------------------------------------------------
  function measure(candleEl) {
    var wick = candleEl.querySelector(".wick"), wx = candleEl.querySelector(".wax");
    var tipX = 30, tipY = 58;                       // candle.css defaults: 60x190 box
    if (wick && wick.offsetWidth) { tipX = wick.offsetLeft + wick.offsetWidth / 2; tipY = wick.offsetTop; }
    cv.style.left = (tipX - OX) + "px";
    cv.style.top = (tipY - OY) + "px";
    if (wx && wx.offsetWidth) {
      wax = { l: wx.offsetLeft - tipX + OX, r: wx.offsetLeft + wx.offsetWidth - tipX + OX, top: wx.offsetTop - tipY + OY };
    } else {
      wax = { l: OX - 20, r: OX + 20, top: OY + 12 };
    }
  }

  function init(candleEl) {
    old = candleEl.querySelector(".flame");
    if (old) old.style.display = "none";
    cv = document.createElement("canvas");
    cv.setAttribute("aria-hidden", "true");
    cv.style.cssText = "position:absolute;width:" + W + "px;height:" + H + "px;pointer-events:none;";
    dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    candleEl.appendChild(cv);
    ctx = cv.getContext("2d");
    makeSprites();
    measure(candleEl);
    window.addEventListener("resize", function () { measure(candleEl); });
  }

  // ---- the flame ---------------------------------------------------------------------
  var pts = [];
  // grow: 0..~1.1 (lighting), fade: 1..0 (going out); bend: extra radians at the tip
  function drawFlame(grow, fade, bend, jitter) {
    var wind = clamp(lean / 55, 0, 1), sq = squash;
    var gut = sstep(0.68, 0.9, vn(T * 0.33 + 100));                 // an occasional gutter
    var h = 47 * (1 + 0.075 * fbm(T * 1.05) + 0.025 * Math.sin(T * 12.7 + 3 * vn(T)) - 0.13 * gut
                  + 0.12 * wind * fbm(T * 13 + 7) + jitter);
    h *= (0.32 + 0.68 * sq) * grow;
    var R = 7.2 * (1 + 0.045 * fbm(T * 1.7 + 30)) * (1 + (1 - sq) * 0.6) * Math.pow(clamp(grow, 0, 1.2), 0.55);
    var bright = clamp(1 + 0.07 * fbm(T * 2.2 + 3) - 0.1 * gut + (1 - sq) * 0.35, 0.6, 1.45) * fade;
    var sway = 0.07 * fbm(T * 0.45 + 5) + 0.16 * gut * fbm(T * 4.2 + 9);
    var ripA = 0.045 + 0.22 * gut + 0.3 * wind, ripF = 10 + 8 * wind;
    var a = lean * Math.PI / 180 + bend;
    if (h < 0.3) return 0;

    // spine: integrate a direction that bends more toward the tip
    var n = Math.max(8, Math.ceil(h / 0.85)), ds = h / n, x = OX, y = OY - 1;
    pts.length = 0;
    for (var i = 0; i <= n; i++) {
      var t = i / n;
      var w = t < 0.24 ? 0.42 + 0.58 * Math.sin(t / 0.24 * Math.PI / 2) : Math.pow((1 - t) / 0.76, 1.12);
      pts.push(x, y, t, Math.max(0.7, R * w));
      var phi = a * Math.pow(t, 0.65) + sway * t + ripA * t * t * Math.sin(t * 6.5 - T * ripF);
      x -= Math.sin(phi) * ds;
      y -= Math.cos(phi) * ds;
    }
    var k, t2, r2, px, py;
    var colour = sstep(0.12, 0.6, grow);                          // a new flame is mostly blue

    // halo
    var mid = Math.floor(pts.length / 4 * 0.4) * 4;
    ctx.globalCompositeOperation = "lighter";
    stamp(spr.halo, pts[mid], pts[mid + 1], 62 * (0.9 + 0.1 * grow), 0.42 * bright * colour);
    stamp(spr.glow, pts[mid], pts[mid + 1], 20, 0.32 * bright * colour);

    // blue cup at the base
    for (k = 0; k < pts.length; k += 4) {
      t2 = pts[k + 2]; if (t2 > 0.34) break;
      stamp(spr.blue, pts[k], pts[k + 1], pts[k + 3] * 1.12, 0.2 * (1 - t2 / 0.34) * (fade + (1 - colour)) * (1 + wind * 0.5));
    }
    // mantle, body: painted over each other
    ctx.globalCompositeOperation = "source-over";
    for (k = 0; k < pts.length; k += 4) {
      t2 = pts[k + 2]; px = pts[k]; py = pts[k + 1]; r2 = pts[k + 3];
      stamp(spr.deep, px, py, r2 * 1.05, 0.5 * sstep(0.05, 0.22, t2) * (1 - 0.9 * sstep(0.74, 1, t2)) * fade * colour);
    }
    for (k = 0; k < pts.length; k += 4) {
      t2 = pts[k + 2]; px = pts[k]; py = pts[k + 1]; r2 = pts[k + 3];
      stamp(spr.orange, px, py, r2 * 0.92, 0.75 * sstep(0.06, 0.22, t2) * (1 - 0.92 * sstep(0.7, 1, t2)) * fade * colour);
    }
    for (k = 0; k < pts.length; k += 4) {
      t2 = pts[k + 2]; if (t2 > 0.93) break; px = pts[k]; py = pts[k + 1]; r2 = pts[k + 3];
      stamp(spr.yellow, px, py, r2 * 0.8, 0.85 * sstep(0.09, 0.24, t2) * (1 - sstep(0.6, 0.93, t2)) * fade * colour);
    }
    ctx.globalCompositeOperation = "lighter";
    for (k = 0; k < pts.length; k += 4) {
      t2 = pts[k + 2]; if (t2 > 0.7) break; px = pts[k]; py = pts[k + 1]; r2 = pts[k + 3];
      stamp(spr.core, px, py, r2 * 0.58, 0.24 * sstep(0.11, 0.26, t2) * (1 - sstep(0.38, 0.7, t2)) * bright * colour);
    }
    // a faint column of hot air above the tip
    var tx = pts[pts.length - 4], ty = pts[pts.length - 3];
    for (k = 1; k <= 7; k++) {
      var up = k * 4.5;
      stamp(spr.heat, tx + Math.sin(up * 0.35 - T * 5.5) * (0.4 + k * 0.25) - Math.sin(a) * up * 0.6, ty - up,
            2.5 + k * 0.6, 0.035 * (1 - k / 8) * fade * colour);
    }
    ctx.globalCompositeOperation = "source-over";
    return bright * colour;
  }

  // light on the top of the wax, and the little pool melted around the wick
  function drawWaxLight(level) {
    if (!wax || level <= 0.01) return;
    var cx = OX, top = wax.top, hw = (wax.r - wax.l) / 2;
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(cx, top + 0.5, hw, 5.5, 0, Math.PI, 0);
    ctx.lineTo(wax.r, H); ctx.lineTo(wax.l, H); ctx.closePath();
    ctx.clip();
    var g = ctx.createRadialGradient(cx, top - 4, 2, cx, top - 4, hw * 2.4);
    g.addColorStop(0, "rgba(255,176,84," + (0.42 * level) + ")");
    g.addColorStop(0.35, "rgba(255,150,60," + (0.2 * level) + ")");
    g.addColorStop(1, "rgba(255,130,40,0)");
    ctx.fillStyle = g;
    ctx.fillRect(wax.l, top - 6, wax.r - wax.l, H - top + 6);
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.5 * level;
    ctx.drawImage(spr.glow, cx - 11, top - 3.5, 22, 7.5);
    ctx.restore();
    ctx.globalAlpha = 1;
  }

  function drawEmber(a, cool) {
    if (a <= 0.01) return;
    ctx.globalCompositeOperation = "lighter";
    stamp(spr.ember, OX, OY + 0.5, 6 + 2 * (1 - cool), 0.35 * a);
    stamp(spr.ember, OX, OY + 0.5, 1.9, a * (1 - 0.4 * cool));
    stamp(spr.spark, OX, OY + 0.4, 0.9, a * (1 - cool) * 0.8);
    ctx.globalCompositeOperation = "source-over";
  }

  function drawSparks(dt) {
    if (!sparks.length) return;
    ctx.globalCompositeOperation = "lighter";
    for (var i = sparks.length - 1; i >= 0; i--) {
      var p = sparks[i];
      p.life -= dt;
      if (p.life <= 0) { sparks.splice(i, 1); continue; }
      p.vy += 140 * dt; p.vx *= 1 - 2 * dt;
      p.x += p.vx * dt; p.y += p.vy * dt;
      var a = p.life / p.max;
      stamp(spr.spark, p.x, p.y, 1.1, a);
      stamp(spr.spark, p.x - p.vx * 0.012, p.y - p.vy * 0.012, 0.8, a * 0.5);
    }
    ctx.globalCompositeOperation = "source-over";
  }

  function frame(ts) {
    var dt = last ? Math.min(0.05, (ts - last) / 1000) : 0.016;
    last = ts; T += dt;
    var e = 1 - Math.exp(-dt * 14);
    lean += (leanT - lean) * e;
    squash += (squashT - squash) * e;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    var keep = true;

    if (state === "lit") {
      var L = ts - litAt;
      var grow = L < 150 ? 0 : backOut(clamp((L - 150) / 700, 0, 1));
      if (L < 260) {                                       // the spark
        ctx.globalCompositeOperation = "lighter";
        var s = 1 - L / 260;
        stamp(spr.spark, OX, OY - 1, 2 + 5 * s * s, s);
        stamp(spr.glow, OX, OY - 1, 14 * s, 0.5 * s);
        ctx.globalCompositeOperation = "source-over";
      }
      drawSparks(dt);
      var bead = sstep(30, 200, L) * (1 - sstep(0.25, 0.75, grow));   // the bead of blue
      if (bead > 0) {
        ctx.globalCompositeOperation = "lighter";
        stamp(spr.blue, OX, OY - 2.5, 2 + 3 * sstep(30, 260, L), 0.95 * bead);
        stamp(spr.spark, OX, OY - 2.2, 1.2, 0.5 * bead);
        ctx.globalCompositeOperation = "source-over";
      }
      var lvl = grow > 0 ? drawFlame(grow, 1, 0, 0) : 0;
      drawWaxLight(lvl * Math.min(1, grow));
      drawEmber(0.4 * sstep(0.3, 1, grow), 0);          // the wick tip glowing inside the flame
    } else if (state === "out") {
      var O = ts - outAt;
      var c = clamp(O / 250, 0, 1);
      if (c < 1) {
        var j = 0.18 * Math.sin(O * 0.07) * (1 - c);       // it gutters as it dies
        var lv = drawFlame(Math.pow(1 - c, 1.4), Math.pow(1 - c, 0.8), outDir * 0.9 * c * c, j);
        drawWaxLight(lv);
      }
      drawSparks(dt);
      var ea = sstep(60, 220, O) * (1 - sstep(300, 2300, O)) * (0.82 + 0.18 * vn(O * 0.012));
      drawEmber(ea, sstep(200, 2300, O));
      if (O > 2400 && !sparks.length) keep = false;
    } else {
      keep = false;
    }
    ctx.globalAlpha = 1;
    if (keep) requestAnimationFrame(frame);
    else { ctx.clearRect(0, 0, W, H); running = false; last = 0; }
  }

  function start() {
    if (running || !ctx) return;
    running = true; last = 0;
    requestAnimationFrame(frame);
  }

  function setState(s) {
    var t = performance.now();
    if (s === "lit" && state !== "lit") {
      litAt = t; sparks.length = 0;
      for (var i = 0; i < 6; i++) {
        var ang = (Math.random() - 0.5) * 1.9, sp = 45 + Math.random() * 70;
        var life = 0.22 + Math.random() * 0.22;
        sparks.push({ x: OX, y: OY - 1, vx: Math.sin(ang) * sp, vy: -Math.cos(ang) * sp, life: life, max: life });
      }
    }
    if (s === "out" && state === "lit") {
      outAt = t;
      outDir = lean > 2 ? 1 : (Math.random() < 0.5 ? -1 : 1);
    }
    state = s;
    if (s !== "lit") { leanT = 0; squashT = 1; }
    if (s !== "unlit") start();
  }
  function setBreath(l, s) {
    if (state !== "lit") return;
    leanT = clamp(+l || 0, 0, 55);
    squashT = clamp(s === undefined ? 1 : +s, 0.35, 1);
  }

  return { init: init, setState: setState, setBreath: setBreath };
})();
