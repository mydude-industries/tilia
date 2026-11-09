/*
 * Tilia blot generator.
 *
 * drawBlot(canvas, seed) draws a mirrored, folded-ink blot from an integer seed.
 * The same seed gives the same 512x512 pixels on every engine and device:
 *   - integer PRNG (mulberry32) and integer hashing (Math.imul), never Math.random
 *   - pixel math uses only + - * / Math.sqrt Math.floor Math.round (IEEE-exact);
 *     no sin/cos/exp/pow/log/atan, which differ between JS engines
 *   - rendering happens at a fixed internal 512x512, then is scaled for display
 *
 * Core API (works in Node and the browser, no DOM needed):
 *   TiliaBlot.generate(seed, {shade:true}) -> {seed, attempt, passed, attempts,
 *       palette, mask (Uint8Array, 1 = ink), rgba (Uint8ClampedArray), stats}
 */
(function (root) {
  'use strict';

  var SIZE = 512;
  var HALF = SIZE / 2;
  var T = 0.3;            // ink threshold on the field
  var HALO = 0.07;        // width of the bleed band below the threshold
  var KS = 1.7361;        // kernel support radius / visual radius ((1-s)^3 = T at s = 0.3307)
  var MAX_ATTEMPTS = 16;
  var MARGIN = Math.round(SIZE * 0.06); // 31 px
  var PAPER = [246, 241, 230];

  // ---------- integer randomness ----------
  function mix32(x) {
    x = x >>> 0;
    x ^= x >>> 16; x = Math.imul(x, 0x7feb352d);
    x ^= x >>> 15; x = Math.imul(x, 0x846ca68b);
    x ^= x >>> 16;
    return x >>> 0;
  }
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function subSeed(seed, k) {
    return mix32((seed >>> 0) ^ mix32(Math.imul(k + 1, 0x9E3779B9) ^ 0x5bd1e995));
  }

  // ---------- exact helpers ----------
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function smooth(e0, e1, x) {
    var t = clamp01((x - e0) / (e1 - e0));
    return t * t * (3 - 2 * t);
  }
  function lat(ix, iy, s) {
    return mix32(Math.imul(ix, 0x27d4eb2d) ^ Math.imul(iy, 0x165667b1) ^ s) / 4294967296;
  }
  // value noise in [0,1), quintic fade
  function vnoise(x, y, s) {
    var ix = Math.floor(x), iy = Math.floor(y);
    var fx = x - ix, fy = y - iy;
    var ux = fx * fx * fx * (fx * (fx * 6 - 15) + 10);
    var uy = fy * fy * fy * (fy * (fy * 6 - 15) + 10);
    var a = lat(ix, iy, s), b = lat(ix + 1, iy, s);
    var c = lat(ix, iy + 1, s), d = lat(ix + 1, iy + 1, s);
    var ab = a + (b - a) * ux, cd = c + (d - c) * ux;
    return ab + (cd - ab) * uy;
  }
  function fbm2(x, y, s) {
    return vnoise(x, y, s) * 0.62 + vnoise(x * 2.03 + 17.1, y * 2.03 + 3.7, s ^ 0x5f3759df) * 0.38;
  }
  function norm(x, y) {
    var l = Math.sqrt(x * x + y * y);
    if (l === 0) return [1, 0];
    return [x / l, y / l];
  }
  // small rotation without trig: rotate by roughly `k` radians and renormalise
  function turn(d, k) { return norm(d[0] - k * d[1], d[1] + k * d[0]); }

  // ---------- composition ----------
  // Coordinates: a = distance from the fold (>= 0), y = 0 top .. 1 bottom, unit = canvas width.
  function Comp(r) {
    this.r = r;
    this.k = [];     // kernels
  }
  Comp.prototype.ellipse = function (cx, cy, rx, ry, dir, w, c) {
    var R = (rx > ry ? rx : ry) * KS;
    this.k.push({ t: 1, cx: cx, cy: cy, ux: dir[0], uy: dir[1], irx: 1 / (rx * KS), iry: 1 / (ry * KS),
      w: w, c: c, a0: cx - R, a1: cx + R, y0: cy - R, y1: cy + R });
  };
  Comp.prototype.capsule = function (ax, ay, bx, by, ra, rb, w, c) {
    var R = (ra > rb ? ra : rb) * KS;
    var dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    this.k.push({ t: 0, ax: ax, ay: ay, dx: dx, dy: dy, il2: l2 > 0 ? 1 / l2 : 0, ra: ra * KS, rd: (rb - ra) * KS,
      w: w, c: c,
      a0: (ax < bx ? ax : bx) - R, a1: (ax > bx ? ax : bx) + R,
      y0: (ay < by ? ay : by) - R, y1: (ay > by ? ay : by) + R });
  };
  // a curving, tapering limb; returns its end point and direction
  Comp.prototype.limb = function (x, y, d, len, r0, r1, segs, curl, c, w) {
    var r = this.r, px = x, py = y, pr = r0;
    for (var i = 0; i < segs; i++) {
      var f = (i + 1) / segs;
      var nr = r0 + (r1 - r0) * f;
      var sl = len / segs * (0.75 + r() * 0.5);
      d = turn(d, curl + (r() - 0.5) * 0.5);
      var nx = px + d[0] * sl, ny = py + d[1] * sl;
      if (nx < 0) nx = 0.004;
      this.capsule(px, py, nx, ny, pr, nr, w || 1, c);
      px = nx; py = ny; pr = nr;
    }
    return { x: px, y: py, d: d, r: pr };
  };

  function rdir(r, vbias) {
    // outward (a >= 0) direction with a vertical bias
    var dx = 0.15 + r() * 0.85;
    var dy = (r() - 0.5) * 2 * (1 + vbias);
    return norm(dx, dy);
  }

  function compose(seed) {
    var r = mulberry32(seed);
    var C = new Comp(r);
    // weighted: two-figure frames (2) read strongly, so they come up less often
    var kr = r(), kind = kr < 0.24 ? 0 : kr < 0.48 ? 1 : kr < 0.62 ? 2 : kr < 0.82 ? 3 : 4;
    var gc = function (p) { return r() < p ? 0.72 + r() * 0.28 : r() * 0.45; }; // colour tag (gold-ness)
    var i, n, e, d;
    var anchors = []; // points on the body to grow from

    if (kind === 0) {
      // tall figure: column on the fold, arms and horns
      var top = 0.12 + r() * 0.12, bot = 0.78 + r() * 0.12;
      var rw = 0.045 + r() * 0.04;
      C.capsule(0, top, 0, (top + bot) / 2, rw * (0.6 + r() * 0.5), rw, 1, gc(0.1));
      C.capsule(0, (top + bot) / 2, 0, bot, rw, rw * (0.5 + r() * 0.7), 1, gc(0.1));
      n = 2 + Math.floor(r() * 3);
      for (i = 0; i < n; i++) {
        var yy = top + (bot - top) * (i + 0.5) / n + (r() - 0.5) * 0.08;
        C.ellipse(0.03 + r() * 0.08, yy, 0.04 + r() * 0.05, 0.05 + r() * 0.07, rdir(r, 1), 1, gc(0.15));
        anchors.push([0.05 + r() * 0.06, yy]);
      }
    } else if (kind === 1) {
      // wings: bat / butterfly
      var wy = 0.38 + r() * 0.2;
      C.capsule(0, wy - 0.18 - r() * 0.1, 0, wy + 0.16 + r() * 0.12, 0.03 + r() * 0.02, 0.02 + r() * 0.03, 1, gc(0.1));
      var up = (r() - 0.65) * 0.9;
      var wd = norm(1, up);
      var span = 0.26 + r() * 0.1;
      for (i = 0; i < 3; i++) {
        var t = (i + 0.6) / 3.4;
        C.ellipse(span * t, wy + up * span * t + (r() - 0.5) * 0.05, 0.06 + r() * 0.05 - i * 0.008,
          0.07 + r() * 0.07, turn(wd, (r() - 0.5) * 0.8), 1, gc(0.2));
      }
      anchors.push([span * 0.8, wy + up * span * 0.8], [span * 0.5, wy + 0.1], [span * 0.4, wy - 0.08]);
    } else if (kind === 2) {
      // two figures facing each other, joined low (and maybe high)
      var fx = 0.12 + r() * 0.08;
      var ft = 0.18 + r() * 0.12, fb = 0.62 + r() * 0.12;
      C.capsule(fx + (r() - 0.5) * 0.06, ft, fx, fb, 0.035 + r() * 0.025, 0.05 + r() * 0.03, 1, gc(0.3));
      C.ellipse(fx * 0.6, fb + 0.04, 0.1 + r() * 0.06, 0.05 + r() * 0.04, norm(1, (r() - 0.5) * 0.6), 1, gc(0.15));
      C.capsule(0, fb + 0.02, fx, fb, 0.03 + r() * 0.02, 0.04, 1, gc(0.1));
      if (r() < 0.6) C.capsule(0, ft + 0.08 + r() * 0.1, fx, ft + 0.12, 0.012 + r() * 0.015, 0.025, 1, gc(0.4));
      C.ellipse(fx + 0.02, ft - 0.02, 0.035 + r() * 0.03, 0.035 + r() * 0.03, rdir(r, 0), 1, gc(0.3));
      anchors.push([fx, ft + 0.05], [fx + 0.03, (ft + fb) / 2], [fx, fb + 0.05], [fx * 0.5, fb + 0.06]);
    } else if (kind === 3) {
      // mask / face: broad mass with eyes and a central slit
      var my = 0.42 + r() * 0.16;
      C.ellipse(0.04, my, 0.14 + r() * 0.06, 0.16 + r() * 0.08, norm(1, (r() - 0.5) * 0.4), 1, gc(0.15));
      C.ellipse(0.12 + r() * 0.06, my - 0.08 - r() * 0.06, 0.07 + r() * 0.04, 0.06 + r() * 0.05, rdir(r, 0.5), 1, gc(0.3));
      C.ellipse(0.1 + r() * 0.06, my + 0.1 + r() * 0.06, 0.06 + r() * 0.04, 0.07 + r() * 0.05, rdir(r, 0.5), 1, gc(0.3));
      anchors.push([0.2, my - 0.12], [0.22, my + 0.04], [0.16, my + 0.16], [0.03, my - 0.2], [0.03, my + 0.22]);
    } else {
      // stacked masses: totem
      n = 3 + Math.floor(r() * 2);
      var y0 = 0.16 + r() * 0.08, step = (0.68 - (y0 - 0.16)) / n;
      for (i = 0; i < n; i++) {
        var cy = y0 + step * (i + 0.5);
        var wdt = 0.05 + r() * 0.1;
        C.ellipse(wdt * 0.5, cy, wdt + 0.03, step * (0.45 + r() * 0.3), norm(1, (r() - 0.5) * 0.8), 1, gc(0.25));
        anchors.push([wdt + 0.02, cy]);
      }
      C.capsule(0, y0, 0, y0 + step * n, 0.02 + r() * 0.02, 0.02 + r() * 0.02, 1, gc(0.1));
    }

    // limbs from anchors
    n = 2 + Math.floor(r() * 4);
    var ends = [];
    for (i = 0; i < n; i++) {
      var an = anchors[Math.floor(r() * anchors.length)];
      d = rdir(r, 0.9);
      var r0 = 0.018 + r() * 0.03;
      e = C.limb(an[0], an[1], d, 0.1 + r() * 0.14, r0, r0 * (0.25 + r() * 0.35), 2 + Math.floor(r() * 3),
        (r() - 0.5) * 0.7, gc(0.35));
      if (r() < 0.45) C.ellipse(e.x, e.y, 0.015 + r() * 0.03, 0.015 + r() * 0.035, e.d, 1, gc(0.5));
      ends.push(e);
    }
    // horns / ears at the top of the fold, the classic plate move
    if (r() < 0.55) {
      var hy = 0.5;
      for (i = 0; i < C.k.length; i++) if (C.k[i].y0 + 0.1 < hy) hy = C.k[i].y0 + 0.1;
      d = norm(0.25 + r() * 0.5, -1);
      var hr = 0.015 + r() * 0.015;
      e = C.limb(0.02 + r() * 0.04, hy + 0.02, d, 0.08 + r() * 0.1, hr, hr * 0.35, 2, (r() - 0.3) * 0.6, gc(0.4));
      ends.push(e);
    }
    // tendrils and drips
    n = Math.floor(r() * 4);
    for (i = 0; i < n && ends.length; i++) {
      e = ends[Math.floor(r() * ends.length)];
      C.limb(e.x, e.y, turn(e.d, (r() - 0.5) * 1.2), 0.05 + r() * 0.08, 0.006 + r() * 0.006, 0.0025, 2, (r() - 0.5) * 0.8, gc(0.4));
    }
    // holes: symmetric white spaces (negative ink)
    var bodyK = C.k.slice();
    n = 1 + Math.floor(r() * 3);
    for (i = 0; i < n; i++) {
      var bk = bodyK[Math.floor(r() * bodyK.length)];
      var hx, hy2;
      if (bk.t === 1) { hx = bk.cx + (r() - 0.5) * 0.04; hy2 = bk.cy + (r() - 0.5) * 0.06; }
      else { var tt = r(); hx = bk.ax + bk.dx * tt; hy2 = bk.ay + bk.dy * tt; }
      if (hx < 0.025) hx = 0.025 + r() * 0.03;
      var hs = 0.012 + r() * 0.025;
      C.ellipse(hx, hy2, hs * (0.7 + r() * 0.8), hs * (0.7 + r() * 0.9), rdir(r, 1), -(1.2 + r() * 0.8), 0);
    }
    // central slit along the fold
    if (r() < 0.45) {
      var sy = 0.3 + r() * 0.3;
      C.capsule(0, sy, 0, sy + 0.08 + r() * 0.18, 0.006 + r() * 0.008, 0.003 + r() * 0.006, -(1 + r()), 0);
    }

    // splatter: clusters thrown outward from the body
    var spl = [];
    n = 6 + Math.floor(r() * 22);
    var posK = [];
    for (i = 0; i < C.k.length; i++) if (C.k[i].w > 0) posK.push(C.k[i]);
    for (i = 0; i < n; i++) {
      var src = ends.length && r() < 0.6 ? ends[Math.floor(r() * ends.length)] : null;
      var sx, sy2, sd;
      if (src) { sx = src.x; sy2 = src.y; sd = turn(src.d, (r() - 0.5) * 1.4); }
      else {
        var pk = posK[Math.floor(r() * posK.length)];
        sx = pk.t === 1 ? pk.cx : pk.ax + pk.dx * 0.5; sy2 = pk.t === 1 ? pk.cy : pk.ay + pk.dy * 0.5;
        sd = rdir(r, 1);
      }
      var dist = 0.04 + r() * 0.12;
      var sz = r();
      var rad = 0.0028 + sz * sz * sz * 0.012;
      spl.push({ x: sx + sd[0] * dist, y: sy2 + sd[1] * dist, rad: rad, c: gc(0.45), d: sd });
      if (rad > 0.007 && r() < 0.6) {
        spl.push({ x: sx + sd[0] * (dist + rad * 2.5), y: sy2 + sd[1] * (dist + rad * 2.5), rad: rad * 0.45, c: gc(0.45), d: sd });
      }
    }
    return { comp: C, kind: kind, splat: spl, warp: 0.012 + r() * 0.014, wf: 4 + r() * 4,
      edge: 0.05 + r() * 0.05, ef: 20 + r() * 18, ns: [mix32(seed ^ 0xa1), mix32(seed ^ 0xb2), mix32(seed ^ 0xc3),
        mix32(seed ^ 0xd4), mix32(seed ^ 0xe5), mix32(seed ^ 0xf6)], fill: 0.8 + r() * 0.08,
      lean: r() };
  }

  // field at (a, y); returns ink field, writes colour mix into out[0]
  function field(K, a, y, out) {
    var f = 0, cw = 0, cs = 0;
    for (var i = 0; i < K.length; i++) {
      var k = K[i];
      if (a < k.a0 || a > k.a1 || y < k.y0 || y > k.y1) continue;
      var s;
      if (k.t === 1) {
        var dx = a - k.cx, dy = y - k.cy;
        var p = (dx * k.ux + dy * k.uy) * k.irx, q = (dy * k.ux - dx * k.uy) * k.iry;
        s = p * p + q * q;
      } else {
        var px = a - k.ax, py = y - k.ay;
        var t = (px * k.dx + py * k.dy) * k.il2;
        if (t < 0) t = 0; else if (t > 1) t = 1;
        var ex = px - k.dx * t, ey = py - k.dy * t;
        var R = k.ra + k.rd * t;
        s = (ex * ex + ey * ey) / (R * R);
      }
      if (s >= 1) continue;
      var g = 1 - s; g = g * g * g * k.w;
      f += g;
      if (g > 0) { cw += g; cs += g * k.c; }
    }
    out[0] = cw > 0 ? cs / cw : 0;
    return f;
  }

  function warpAt(P, a, y, o) {
    var ns = P.ns, wf = P.wf;
    o[0] = a + P.warp * (fbm2(a * wf, y * wf, ns[0]) - 0.5) * 2;
    o[1] = y + P.warp * (fbm2(a * wf + 31.7, y * wf + 9.3, ns[1]) - 0.5) * 2;
  }

  // ---------- one candidate ----------
  function buildCandidate(cseed) {
    var P = compose(cseed);
    var K = P.comp.k;
    var tmp = [0], w = [0, 0];
    // coarse pass to find the extent of the body (without splatter)
    var amax = 0, ymin = 9, ymax = -9, i, j, a, y, f;
    for (j = 0; j < 200; j++) {
      y = -0.25 + j * 0.0075;
      for (i = 0; i < 90; i++) {
        a = i * 0.0075;
        warpAt(P, a, y, w);
        f = field(K, w[0] < 0 ? -w[0] : w[0], w[1], tmp);
        if (f > T - HALO) {
          if (a > amax) amax = a;
          if (y < ymin) ymin = y;
          if (y > ymax) ymax = y;
        }
      }
    }
    if (ymax < ymin) { amax = 0.2; ymin = 0.3; ymax = 0.7; }
    amax += 0.008; ymin -= 0.008; ymax += 0.008;
    // fit: largest dimension fills `fill` of the frame, inside the margin
    var box = P.fill - 0.01; // ~0.79..0.87 of the frame (margin is 0.06 each side)
    var sc = box / (2 * amax);
    var sv = box / (ymax - ymin);
    if (sv < sc) sc = sv;
    P.scale = sc;
    P.ycen = (ymin + ymax) / 2;
    // splatter in comp coords, dropped if it would land in the margin
    var lim = 0.5 - (MARGIN + 6) / SIZE;
    for (i = 0; i < P.splat.length; i++) {
      var s = P.splat[i];
      var ax = s.x * sc, ay = (s.y - P.ycen) * sc;
      var reach = (s.rad * KS + P.warp) * sc;
      if (s.x < 0 || ax + reach > lim || ay - reach < -lim || ay + reach > lim) continue;
      P.comp.ellipse(s.x, s.y, s.rad * (1 + P.lean * 0.4), s.rad, s.d, 1, s.c);
    }
    return P;
  }

  // full-resolution field (half width computed, mirrored)
  function rasterField(P) {
    var K = P.comp.k;
    var F = new Float64Array(SIZE * SIZE);
    var Cm = new Float64Array(SIZE * SIZE);
    var tmp = [0], w = [0, 0];
    var isc = 1 / P.scale;
    var ns = P.ns, ef = P.ef, edge = P.edge;
    for (var j = 0; j < SIZE; j++) {
      var y = P.ycen + ((j + 0.5) / SIZE - 0.5) * isc;
      for (var i = 0; i < HALF; i++) {
        var a = ((HALF - 0.5 - i) / SIZE) * isc;
        warpAt(P, a, y, w);
        var wa = w[0] < 0 ? -w[0] : w[0];
        var f = field(K, wa, w[1], tmp);
        if (f > 0.05) {
          f += edge * (vnoise(a * ef, y * ef, ns[2]) * 0.55 + vnoise(a * ef * 2.7, y * ef * 2.7, ns[3]) * 0.3 +
            vnoise(a * ef * 7.3, y * ef * 7.3, ns[4] ^ 0x1234) * 0.15 - 0.5);
        }
        var p = j * SIZE + i, q = j * SIZE + (SIZE - 1 - i);
        F[p] = f; F[q] = f; Cm[p] = tmp[0]; Cm[q] = tmp[0];
      }
    }
    return { F: F, C: Cm };
  }

  // ---------- quality gate ----------
  function floodOutside(block, out) {
    // marks in `out` every non-block pixel reachable from the border (4-connected)
    var N = SIZE, stack = new Int32Array(N * N), sp = 0, i, p;
    function push(p) { if (!block[p] && !out[p]) { out[p] = 1; stack[sp++] = p; } }
    for (i = 0; i < N; i++) { push(i); push((N - 1) * N + i); push(i * N); push(i * N + N - 1); }
    while (sp > 0) {
      p = stack[--sp];
      var x = p % N;
      if (x > 0) push(p - 1);
      if (x < N - 1) push(p + 1);
      if (p >= N) push(p - N);
      if (p < N * N - N) push(p + N);
    }
  }
  function dilate(mask, rad) {
    var N = SIZE, tmp = new Uint8Array(N * N), out = new Uint8Array(N * N), x, y, k;
    for (y = 0; y < N; y++) {
      var run = -1e9;
      for (x = 0; x < N; x++) { if (mask[y * N + x]) run = x; tmp[y * N + x] = x - run <= rad ? 1 : 0; }
      run = 1e9;
      for (x = N - 1; x >= 0; x--) { if (mask[y * N + x]) run = x; if (run - x <= rad) tmp[y * N + x] = 1; }
    }
    for (x = 0; x < N; x++) {
      var r2 = -1e9;
      for (y = 0; y < N; y++) { if (tmp[y * N + x]) r2 = y; out[y * N + x] = y - r2 <= rad ? 1 : 0; }
      r2 = 1e9;
      for (y = N - 1; y >= 0; y--) { if (tmp[y * N + x]) r2 = y; if (r2 - y <= rad) out[y * N + x] = 1; }
    }
    return out;
  }

  function evaluate(F, T) {
    var N = SIZE, total = N * N, i, x, y, p;
    var mask = new Uint8Array(total);
    var ink = 0, marginHit = 0;
    var bx0 = N, bx1 = -1, by0 = N, by1 = -1;
    for (p = 0; p < total; p++) {
      var f = F[p];
      if (f > T) { mask[p] = 1; ink++; }
      if (f > T - HALO * 0.6) {
        x = p % N; y = (p - x) / N;
        if (x < MARGIN || y < MARGIN || x >= N - MARGIN || y >= N - MARGIN) marginHit++;
        if (f > T) { if (x < bx0) bx0 = x; if (x > bx1) bx1 = x; if (y < by0) by0 = y; if (y > by1) by1 = y; }
      }
    }
    var coverage = ink / total;
    // axis contact: rows with ink on the fold
    var axisRows = 0;
    for (y = 0; y < N; y++) if (mask[y * N + HALF - 1]) axisRows++;
    var height = by1 >= by0 ? by1 - by0 + 1 : 1;
    // components (8-connected) to find the main body
    var lab = new Int32Array(total), stack = new Int32Array(total), nlab = 0, best = 0, bestArea = 0, comps = 0;
    for (p = 0; p < total; p++) {
      if (!mask[p] || lab[p]) continue;
      nlab++; var area = 0, sp = 0; lab[p] = nlab; stack[sp++] = p;
      while (sp > 0) {
        var q = stack[--sp]; area++;
        var qx = q % N, qy = (q - qx) / N;
        for (var dy = -1; dy <= 1; dy++) for (var dx = -1; dx <= 1; dx++) {
          var nx = qx + dx, ny = qy + dy;
          if (nx < 0 || ny < 0 || nx >= N || ny >= N) continue;
          var r = ny * N + nx;
          if (mask[r] && !lab[r]) { lab[r] = nlab; stack[sp++] = r; }
        }
      }
      if (area >= 20) comps++;
      if (area > bestArea) { bestArea = area; best = nlab; }
    }
    var mainOnAxis = false;
    for (y = 0; y < N && !mainOnAxis; y++) if (lab[y * N + HALF - 1] === best) mainOnAxis = true;
    // shape complexity of the main body on a 4x coarser grid (ignores pixel-level raggedness)
    var M = N / 4, cm = new Uint8Array(M * M), carea = 0, cper = 0;
    for (y = 0; y < M; y++) for (x = 0; x < M; x++) {
      var cnt = 0;
      for (var yy = 0; yy < 4; yy++) for (var xx = 0; xx < 4; xx++) if (lab[(y * 4 + yy) * N + x * 4 + xx] === best) cnt++;
      if (cnt >= 8) { cm[y * M + x] = 1; carea++; }
    }
    for (y = 0; y < M; y++) for (x = 0; x < M; x++) {
      if (!cm[y * M + x]) continue;
      if (x === 0 || !cm[y * M + x - 1]) cper++;
      if (x === M - 1 || !cm[y * M + x + 1]) cper++;
      if (y === 0 || !cm[(y - 1) * M + x]) cper++;
      if (y === M - 1 || !cm[(y + 1) * M + x]) cper++;
    }
    // 4-connected pixel perimeter overstates a circle by 4/pi; normalise so a disc ~ 1
    var complexity = carea > 0 ? (cper * cper) / (16 * carea) : 0;
    var solidity = bestArea / ((bx1 - bx0 + 1) * height || 1);
    // enclosed white (strict) and semi-enclosed white (after closing narrow mouths)
    var out1 = new Uint8Array(total);
    floodOutside(mask, out1);
    var enclosed = 0;
    for (p = 0; p < total; p++) if (!mask[p] && !out1[p]) enclosed++;
    var dil = dilate(mask, 6);
    var out2 = new Uint8Array(total);
    floodOutside(dil, out2);
    var semi = 0;
    for (p = 0; p < total; p++) if (!mask[p] && !out2[p]) semi++;

    var fails = [];
    if (coverage < 0.22) fails.push('coverage-low');
    if (coverage > 0.5) fails.push('coverage-high');
    if (!mainOnAxis || axisRows < height * 0.12) fails.push('axis');
    if (semi < total * 0.003) fails.push('no-white-space');
    if (marginHit > 0) fails.push('margin');
    if (complexity < 2.2 || solidity > 0.68) fails.push('blob');
    return {
      mask: mask, passed: fails.length === 0, fails: fails,
      stats: { coverage: coverage, axisRows: axisRows, enclosed: enclosed, semiEnclosed: semi,
        complexity: complexity, solidity: solidity, components: comps, marginHits: marginHit }
    };
  }

  // Thin compositions: lower the ink threshold (spreads the ink, like pressing the fold harder)
  // just enough to reach TARGET_COV, never below T_MIN. Pure histogram counting, so deterministic.
  var TARGET_COV = 0.24, T_MIN = 0.15;
  function fitThreshold(F) {
    var total = SIZE * SIZE, need = TARGET_COV * total, bins = 100, hist = new Int32Array(bins + 1), p, c = 0;
    for (p = 0; p < total; p++) { var f = F[p]; if (f > T_MIN && f <= T) hist[Math.floor((f - T_MIN) / (T - T_MIN) * bins)]++; else if (f > T) c++; }
    if (c >= need) return T;
    for (var b = bins - 1; b >= 0; b--) { c += hist[b]; if (c >= need) return T_MIN + (T - T_MIN) * b / bins; }
    return T_MIN;
  }

  // ---------- shading ----------
  function shade(P, R, mask, palette) {
    var N = SIZE, F = R.F, Cm = R.C, T = P.T, ts = P.T / 0.3; // edge widths follow the threshold
    var rgba = new Uint8ClampedArray(N * N * 4);
    var ns = P.ns, isc = 1 / P.scale;
    var green = palette === 'green';
    var ink = green ? [44, 82, 66] : [24, 22, 20];
    var gold = [181, 143, 66];
    // gold is an accent: raise its threshold until it covers at most ~22% of the ink
    var gt = 0.5;
    if (green) {
      var hist = new Int32Array(64), tot = 0, q;
      for (q = 0; q < N * N; q++) if (mask[q]) { var hb = Math.floor(Cm[q] * 63); hist[hb < 0 ? 0 : hb]++; tot++; }
      var acc = 0, b;
      for (b = 63; b >= 0; b--) { acc += hist[b]; if (acc > tot * 0.22) break; }
      var lim = (b + 1) / 63 - 0.08;
      if (lim > gt) gt = lim;
    }
    for (var j = 0; j < N; j++) {
      var y = P.ycen + ((j + 0.5) / N - 0.5) * isc;
      for (var i = 0; i < HALF; i++) {
        var a = ((HALF - 0.5 - i) / N) * isc;
        var p = j * N + i, f = F[p];
        // paper grain (pixel hash, mirrored with the blot so the fold reads as one sheet)
        var gr = (lat(i, j, ns[5]) - 0.5) * 3;
        var r0 = PAPER[0] + gr, g0 = PAPER[1] + gr, b0 = PAPER[2] + gr;
        var cr = r0, cg = g0, cb = b0;
        if (f > T - HALO * ts) {
          var core = smooth(T - 0.007 * ts, T + 0.01 * ts, f);
          var h = smooth(T - HALO * ts, T, f);
          var fiber = vnoise(a * 140, y * 140, ns[4]);
          var halo = h * h * 0.13 * (0.35 + 0.65 * fiber);
          var thick = clamp01((f - T) / 0.7);
          var rim = 1 - smooth(T, T + 0.1, f);
          var mott = vnoise(a * 9, y * 9, ns[2] ^ 0x77) * 0.65 + vnoise(a * 23, y * 23, ns[3] ^ 0x55) * 0.35 - 0.5;
          var streak = vnoise(a * 26, y * 11, ns[3] ^ 0x99) - 0.5;
          var dens = 0.8 + 0.2 * thick + 0.12 * rim + 0.26 * mott + 0.05 * streak;
          if (dens > 1) dens = 1; if (dens < 0.5) dens = 0.5;
          var al = core * dens;
          if (halo > al) al = halo;
          var ir = ink[0], ig = ink[1], ib = ink[2];
          if (green) {
            var gw = smooth(gt, gt + 0.07, Cm[p] + 0.25 * (vnoise(a * 7, y * 7, ns[1] ^ 0x33) - 0.5));
            ir = ir + (gold[0] - ir) * gw; ig = ig + (gold[1] - ig) * gw; ib = ib + (gold[2] - ib) * gw;
            al = al * (1 - 0.08 * gw);
          } else {
            // thin black ink shows a faint warm brown
            var wt = 1 - dens;
            ir += 30 * wt; ig += 22 * wt; ib += 12 * wt;
          }
          // a hairline crease where the paper was folded
          if (i === HALF - 1) al *= 0.94;
          cr = r0 + (ir - r0) * al; cg = g0 + (ig - g0) * al; cb = b0 + (ib - b0) * al;
        }
        var o1 = p * 4, o2 = (j * N + (N - 1 - i)) * 4;
        cr = Math.round(cr); cg = Math.round(cg); cb = Math.round(cb);
        rgba[o1] = cr; rgba[o1 + 1] = cg; rgba[o1 + 2] = cb; rgba[o1 + 3] = 255;
        rgba[o2] = cr; rgba[o2 + 1] = cg; rgba[o2 + 2] = cb; rgba[o2 + 3] = 255;
      }
    }
    return rgba;
  }

  // ---------- public ----------
  function normSeed(seed) {
    var s = Number(seed);
    if (!isFinite(s)) s = 0;
    s = Math.floor(s < 0 ? -s : s);
    return (s % 4294967296) >>> 0;
  }

  function generate(seed, opts) {
    seed = normSeed(seed);
    var wantShade = !opts || opts.shade !== false;
    var palette = mulberry32(mix32(seed ^ 0x7a11a))() < 1 / 3 ? 'green' : 'black';
    var best = null, tried = [];
    for (var k = 0; k < MAX_ATTEMPTS; k++) {
      var cs = subSeed(seed, k);
      var P = buildCandidate(cs);
      var R = rasterField(P);
      P.T = fitThreshold(R.F);
      var ev = evaluate(R.F, P.T);
      tried.push(ev.fails);
      if (!best || ev.fails.length < best.ev.fails.length) best = { P: P, R: R, ev: ev, k: k };
      if (ev.passed) break;
    }
    var res = {
      seed: seed, attempt: best.k, attempts: tried.length, passed: best.ev.passed, palette: palette,
      kind: best.P.kind, mask: best.ev.mask, stats: best.ev.stats, fails: tried, size: SIZE
    };
    if (wantShade) res.rgba = shade(best.P, best.R, best.ev.mask, palette);
    return res;
  }

  var cache = {};
  function drawBlot(canvas, seed) {
    seed = normSeed(seed);
    var res = cache[seed];
    if (!res) {
      res = generate(seed);
      var off = document.createElement('canvas');
      off.width = SIZE; off.height = SIZE;
      off.getContext('2d').putImageData(new ImageData(res.rgba, SIZE, SIZE), 0, 0);
      res.image = off;
      cache[seed] = res;
    }
    var ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(res.image, 0, 0, canvas.width, canvas.height);
    return res;
  }

  function hash(bytes) { // FNV-1a over a byte array, hex
    var h = 0x811c9dc5;
    for (var i = 0; i < bytes.length; i++) { h ^= bytes[i]; h = Math.imul(h, 0x01000193); }
    return ('0000000' + (h >>> 0).toString(16)).slice(-8);
  }

  // GEN names this generator. A seed's pixels are frozen forever (test/blot-golden.json):
  // any change that moves a pixel is a new generator, never an edit to this one.
  var api = { generate: generate, drawBlot: drawBlot, hash: hash, SIZE: SIZE, MAX_ATTEMPTS: MAX_ATTEMPTS, GEN: 1 };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else { root.TiliaBlot = api; root.drawBlot = drawBlot; }
})(typeof self !== 'undefined' ? self : this);
