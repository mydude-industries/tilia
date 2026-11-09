"use strict";
// The blot generator: a seed's pixels are frozen forever, on every engine.
// BLOT_SEEDS (default 200) sets how many seeds the quality checks run over.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "blot.js");
const B = require(SRC);
const GOLDEN = JSON.parse(fs.readFileSync(path.join(__dirname, "blot-golden.json"), "utf8"));
const N = Number(process.env.BLOT_SEEDS || 200);
const MARGIN = 31;

test("the API is the spike's, at generator version 1", () => {
  assert.equal(B.GEN, 1);
  assert.equal(B.SIZE, 512);
  for (const f of ["generate", "drawBlot", "hash"]) assert.equal(typeof B[f], "function");
});

test("the same seed gives the same pixels twice", () => {
  for (let s = 1; s <= 20; s++) {
    assert.equal(B.hash(B.generate(s).rgba), B.hash(B.generate(s).rgba), `seed ${s}`);
  }
});

test("seeds 1..20 match the golden hashes", () => {
  assert.deepEqual(GOLDEN.map((g) => g.seed), Array.from({ length: 20 }, (_, i) => i + 1));
  for (const g of GOLDEN) {
    const r = B.generate(g.seed);
    assert.equal(B.hash(r.mask), g.mask, `mask, seed ${g.seed}`);
    assert.equal(B.hash(r.rgba), g.rgba, `rgba, seed ${g.seed}`);
  }
});

test("no engine-dependent math in the source", () => {
  const code = fs.readFileSync(SRC, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(code, /Math\.random/);
  assert.doesNotMatch(code, /Math\.(sin|cos|tan|exp|pow|log|atan)/);
});

test(`quality over ${N} seeds`, () => {
  let passed = 0, green = 0;
  const S = B.SIZE;
  for (let s = 1; s <= N; s++) {
    const r = B.generate(s, { shade: false });
    if (r.palette === "green") green++;
    if (r.passed) {
      passed++;
      assert.ok(r.stats.coverage >= 0.22 && r.stats.coverage <= 0.5, `coverage ${r.stats.coverage}, seed ${s}`);
    }
    for (let y = 0; y < S; y++) {
      for (let x = 0; x < S; x++) {
        if (x >= MARGIN && x < S - MARGIN && y >= MARGIN && y < S - MARGIN) continue;
        if (r.mask[y * S + x]) assert.fail(`ink in the margin at ${x},${y}, seed ${s}`);
      }
    }
  }
  assert.ok(passed / N >= 0.99, `passed ${passed} of ${N}`);
  const share = green / N;
  assert.ok(share >= 0.25 && share <= 0.42, `green share ${share}`);
});
