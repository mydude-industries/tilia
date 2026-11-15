"use strict";
// The personal candle (rituals phase 1, D4): burn-down from the time it was lit, the
// breath rhythm that steadies the flame, the lit state kept between visits, and what
// goes back to Tilia at blow-out.
const test = require("node:test");
const assert = require("node:assert/strict");
const K = require("../candle-core.js");

const H = 3600 * 1000;
const LIT = Date.UTC(2026, 10, 15, 14, 0);
const HZ = 10;  // the page samples the loudness ten times a second

// a seeded generator, so the synthetic sounds are the same on every run
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const QUIET = 0.002;
// slow, even breaths close to the phone: a soft swell on each out-breath
// `uneven` changes each breath's length (by up to that factor either way)
function breathing(perMin, seconds, seed = 1, uneven = 1) {
  const r = rng(seed), out = [];
  let phase = 0, rate = 1;
  for (let i = 0; i < seconds * HZ; i++) {
    const before = Math.floor(phase / Math.PI);
    phase += (2 * Math.PI * perMin / 60 / HZ) * rate;
    if (Math.floor(phase / Math.PI) !== before) rate = Math.pow(uneven, 2 * r() - 1);
    const swell = Math.pow(Math.max(0, Math.sin(phase)), 2);
    out.push(QUIET * (1 + 0.15 * r()) + 0.02 * swell);
  }
  return out;
}

// talking: syllables come and go several times a second, loud and uneven, with pauses
function talking(seconds, seed = 2, every = 0, burst = 0) {
  const r = rng(seed), out = [];
  for (let i = 0; i < seconds * HZ; i++) {
    const t = i / HZ;
    const on = every ? (t % every) < burst : r() > 0.25;
    out.push(QUIET * (1 + 0.15 * r()) + (on ? 0.03 * r() * r() + 0.004 * r() : 0));
  }
  return out;
}

test("burn-down from lit_at: four hours from a full candle to an ember", () => {
  assert.equal(K.BURN_MS, 4 * H);
  assert.equal(K.burnFraction(LIT, LIT), 0);
  assert.equal(K.burnFraction(LIT, LIT + 1 * H), 0.25);
  assert.equal(K.burnFraction(LIT, LIT + 3 * H), 0.75);
  assert.equal(K.burnFraction(LIT, LIT + 4 * H), 1);
  assert.equal(K.burnFraction(LIT, LIT + 30 * H), 1);  // never past the ember
  // a clock that disagrees a little, or a missing time, is a fresh candle
  assert.equal(K.burnFraction(LIT, LIT - 5 * 60 * 1000), 0);
  assert.equal(K.burnFraction(null, LIT), 0);
  assert.equal(K.burnFraction("soon", LIT), 0);
});

test("burn-down from lit_at: reopened hours later it is still lit and still waits", () => {
  const raw = K.litValue(LIT, "the call with Riley");
  for (const [hours, ember] of [[0, false], [1, false], [3, false], [5, true], [40, true]]) {
    const s = K.restore(raw, LIT + hours * H);
    assert.equal(s.state, "lit", `${hours} h`);              // it waits to be blown out
    assert.equal(s.intention, "the call with Riley");
    assert.equal(s.litAt, LIT);
    assert.equal(s.burn, Math.min(1, hours / 4));
    assert.equal(s.ember, ember, `${hours} h`);
  }
  // nothing saved, or something unreadable: an unlit candle
  for (const bad of [null, "", "{", "[]", '{"lit_at":"x"}', '{"intention":"x"}']) {
    assert.deepEqual(K.restore(bad, LIT), { state: "unlit" }, String(bad));
  }
});

test("empty intention allowed: lit, saved, restored and sent without one", () => {
  const raw = K.litValue(LIT, "   ");
  assert.deepEqual(JSON.parse(raw), { lit_at: LIT, intention: "" });
  const s = K.restore(raw, LIT + 2 * H);
  assert.equal(s.state, "lit");
  assert.equal(s.intention, "");
  const p = JSON.parse(K.payload({ id: "abcdefghijkl" }, s.intention, LIT, LIT + 2 * H,
    "About what I expected."));
  assert.deepEqual(p, { v: 1, type: "candle", id: "abcdefghijkl", intention: "",
    lit_at: LIT, out_at: LIT + 2 * H, answer: "About what I expected." });
  // the smoke says "It's out." when there's nothing of theirs to echo
  assert.deepEqual(K.echo(""), ["It's out.", "How did it go?"]);
  assert.deepEqual(K.echo("the call with Riley"), ["the call with Riley", "How did it go?"]);
});

test("intention is one short line", () => {
  assert.equal(K.cleanIntention("  the   call\nwith Riley "), "the call with Riley");
  assert.equal(Array.from(K.cleanIntention("x".repeat(200))).length, K.INTENTION_MAX);
  assert.equal(K.cleanIntention(undefined), "");
});

test("the lit state lives under the entry's own key", () => {
  assert.equal(K.storeKey("abcdefghijkl"), "candle_abcdefghijkl");
  // Telegram CloudStorage keys take only letters, digits, _ and -
  assert.match(K.storeKey("AB_cd-12efgh"), /^[A-Za-z0-9_-]{1,128}$/);
  const launch = K.parseLaunch("file:///tilia/v0.2.3/candle.html?t=candle&v=1&id=abcdefghijkl&lang=en");
  assert.deepEqual(launch, { type: "candle", id: "abcdefghijkl", diag: false, entry: true });
  // the preview page (no entry) still works, it just keeps nothing and sends nothing
  assert.equal(K.parseLaunch("file:///tilia/preview/candle.html").entry, false);
  assert.equal(K.parseLaunch("file:///x/candle.html?t=blot&id=abcdefghijkl").entry, false);
  assert.equal(K.parseLaunch("file:///x/candle.html?t=candle&id=short").entry, false);
});

test("stale lit candles are swept after the entry could no longer be answered", () => {
  const now = LIT + 9 * 24 * H;
  const items = {
    candle_aaaaaaaaaaaa: K.litValue(LIT, "old"),                  // 9 days: gone
    candle_bbbbbbbbbbbb: K.litValue(now - 2 * H, "this week"),    // keeps
    candle_cccccccccccc: "{",                                     // unreadable: gone
    candle_dddddddddddd: K.litValue(LIT, "mine"),                 // the open one: keeps
    d_abcdefghijkl: "{}",                                         // a worksheet draft: not ours
  };
  assert.deepEqual(K.staleCandles(items, now, "candle_dddddddddddd").sort(),
    ["candle_aaaaaaaaaaaa", "candle_cccccccccccc"]);
});

test("breath rhythm: slow even breathing reads as calm", () => {
  for (const perMin of [4, 6, 8, 10]) {
    const c = K.calmFromRms(breathing(perMin, 30, perMin), HZ);
    assert.ok(c >= 0.75, `${perMin}/min gave ${c}`);
  }
  // slower is calmer than quicker, within the calm band
  assert.ok(K.calmFromRms(breathing(5, 30), HZ) > K.calmFromRms(breathing(11, 30), HZ));
});

test("breath rhythm: quick or uneven breathing reads as unsettled", () => {
  for (const perMin of [20, 30]) {
    const c = K.calmFromRms(breathing(perMin, 30, perMin), HZ);
    assert.ok(c <= 0.35, `${perMin}/min gave ${c}`);
  }
  // breaths whose gaps vary by a third or more (14 s, then 9 s apart for seed 1)
  for (const seed of [1, 3, 4]) {
    const uneven = K.calmFromRms(breathing(7, 30, seed, 3), HZ);
    assert.ok(uneven < 0.6, `uneven (${seed}) gave ${uneven}`);
  }
});

test("breath rhythm ignores speech bursts", () => {
  // ordinary talking
  for (const seed of [2, 5, 9]) {
    const c = K.calmFromRms(talking(30, seed), HZ);
    assert.ok(c <= 0.35, `talking (${seed}) gave ${c}`);
  }
  // talking in bursts that come round as slowly as calm breaths do
  for (const [every, burst] of [[6, 2.5], [8, 3], [10, 4]]) {
    const c = K.calmFromRms(talking(30, 4, every, burst), HZ);
    assert.ok(c <= 0.35, `bursts every ${every} s gave ${c}`);
  }
  // even calm-looking loudness is not calm when the page heard a voice in it
  const calm = breathing(6, 30);
  const voiced = calm.map((v) => v > QUIET * 3);
  assert.ok(K.calmFromRms(calm, HZ, voiced) <= 0.35);
  // a few voiced moments (a cough, a word) don't spoil a calm stretch
  const few = calm.map((_, i) => i % 97 === 0);
  assert.ok(K.calmFromRms(calm, HZ, few) >= 0.75);
});

test("breath rhythm: talking for a while and then breathing slowly settles back", () => {
  const mixed = talking(20, 7).concat(breathing(6, 40, 8));
  const window = mixed.slice(-K.CALM_WINDOW_S * HZ);
  assert.ok(K.calmFromRms(window, HZ) >= 0.75);
  // and the window the page keeps is about twenty to thirty seconds
  assert.ok(K.CALM_WINDOW_S >= 20 && K.CALM_WINDOW_S <= 30);
});

test("breath rhythm: silence, a steady hum or too little to go on is neutral", () => {
  assert.equal(K.calmFromRms([], HZ), K.NEUTRAL);
  assert.equal(K.calmFromRms(breathing(6, 4), HZ), K.NEUTRAL);         // four seconds only
  assert.equal(K.calmFromRms(new Array(300).fill(QUIET), HZ), K.NEUTRAL);
  const hum = new Array(300).fill(0).map((_, i) => 0.01 * (1 + 0.02 * Math.sin(i)));
  assert.equal(K.calmFromRms(hum, HZ), K.NEUTRAL);
  assert.equal(K.NEUTRAL, 0.5);
  for (const c of [K.calmFromRms(talking(30), HZ), K.calmFromRms(breathing(6, 30), HZ)]) {
    assert.ok(c >= 0 && c <= 1);
  }
});

test("answered: the record becomes pending before sending, and reopening offers it again", () => {
  const out = LIT + 2 * H;
  const raw = K.pendingValue(LIT, "the call with Riley", out, "Harder than I hoped.");
  assert.deepEqual(JSON.parse(raw), { lit_at: LIT, intention: "the call with Riley", out_at: out,
    answer: "Harder than I hoped.", sent: true });
  const s = K.restore(raw, out + 5 * H);
  assert.deepEqual(s, { state: "sent", litAt: LIT, intention: "the call with Riley", outAt: out,
    answer: "Harder than I hoped." });
  // "Send it again" sends exactly what the first send did
  const launch = { id: "abcdefghijkl" };
  assert.equal(K.resend(launch, s), K.payload(launch, "the call with Riley", LIT, out, "Harder than I hoped."));
  // no intention is still fine
  const bare = K.restore(K.pendingValue(LIT, "", out, "It didn't happen."), out);
  assert.equal(bare.state, "sent");
  assert.equal(JSON.parse(K.resend(launch, bare)).intention, "");
  // a pending record missing its answer or out time is unreadable: a fresh candle
  for (const bad of [{ lit_at: LIT, sent: true, answer: "x" }, { lit_at: LIT, sent: true, out_at: out },
    { lit_at: LIT, sent: true, out_at: out, answer: "  " }]) {
    assert.deepEqual(K.restore(JSON.stringify(bad), out), { state: "unlit" });
  }
});

test("other entries' sent candles are swept at once; this entry's is kept to resend", () => {
  const now = LIT + 3 * H;
  const items = {
    candle_aaaaaaaaaaaa: K.pendingValue(LIT, "sent", LIT + H, "About what I expected."),
    candle_bbbbbbbbbbbb: K.litValue(LIT, "still lit"),
    candle_dddddddddddd: K.pendingValue(LIT, "mine", LIT + H, "Better than I feared."),
  };
  assert.deepEqual(K.staleCandles(items, now, "candle_dddddddddddd"), ["candle_aaaaaaaaaaaa"]);
});

// The words become the flame: their motes gather into places sampled from its shape.
test("teardrop places: a stable count, the same every time, all inside the flame", () => {
  for (const n of [0, 1, 37, 960]) {
    const a = K.teardropPoints(n, 7), b = K.teardropPoints(n, 7);
    assert.equal(a.length, n);
    assert.deepEqual(a, b);
  }
  const pts = K.teardropPoints(960, 7);
  for (let i = 1; i < pts.length; i++) assert.ok(pts[i].t >= pts[i - 1].t, "base first");
  for (const grow of [0.05, 0.4, 1, 1.15]) {
    const g = Math.min(1.2, grow), h = K.FLAME_H * g, rw = K.FLAME_R * Math.pow(g, 0.55);
    for (const p of pts) {
      assert.ok(p.t >= 0 && p.t <= 1);
      const q = K.teardropAt(p, grow);
      assert.ok(q.y <= -1 && q.y >= -1 - h - 1e-9, "between the wick and the tip");
      assert.ok(Math.abs(q.x) <= rw * K.flameWidth(p.t) + 1e-9, "inside the width at that height");
    }
  }
  // spread over the area: the lower half (the wide cup and body) holds most of them
  const low = pts.filter((p) => p.t < 0.5).length;
  assert.ok(low > 0.55 * pts.length && low < 0.85 * pts.length, `lower half ${low}`);
  // grown to nothing, every place is at the wick
  for (const p of pts.slice(0, 20)) { const q = K.teardropAt(p, 0); assert.ok(q.x === 0 && q.y === -1); }
});

test("spring step: under-damped overshoots once, then settles on the target", () => {
  let x = 0, v = 0, peak = 0;
  const k = 55, c = 2 * 0.5 * Math.sqrt(k);
  for (let i = 0; i < 60 * 4; i++) {
    [x, v] = K.springStep(x, v, 1, k, c, 1 / 60);
    peak = Math.max(peak, x);
  }
  assert.ok(peak > 1.05 && peak < 1.3, `overshoot ${peak}`);
  assert.ok(Math.abs(x - 1) < 0.005 && Math.abs(v) < 0.05, `settled ${x} ${v}`);
  // stable at the slowest frame the page allows (dt 0.05 s)
  x = 0; v = 0;
  for (let i = 0; i < 200; i++) [x, v] = K.springStep(x, v, 1, 70, 2 * 0.55 * Math.sqrt(70), 0.05);
  assert.ok(Math.abs(x - 1) < 0.01);
});
