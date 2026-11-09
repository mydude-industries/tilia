"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const C = require("../core.js");

const b64 = (obj) => Buffer.from(JSON.stringify(obj), "utf8").toString("base64url");
const BASE = "file:///tilia/v0.2.0/?t=check_story&v=1&id=abcdefghijkl&lang=en";
const TW = b64({ pitch: "About Riley" });

test("parseLaunch reads the query and tw next to Telegram's params", () => {
  for (const frag of [`#tw=${TW}&tgWebAppData=x&tgWebAppVersion=8.0`, `#tgWebAppVersion=8.0&tw=${TW}`]) {
    const l = C.parseLaunch(BASE + frag);
    assert.equal(l.type, "check_story");
    assert.equal(l.v, 1);
    assert.equal(l.id, "abcdefghijkl");
    assert.equal(l.lang, "en");
    assert.equal(l.card, null);
    assert.equal(l.seed, null);
    assert.equal(l.diag, false);
    assert.equal(l.wording.pitch, "About Riley");
  }
});

test("parseLaunch reads card, seed and diag", () => {
  const l = C.parseLaunch("file:///tilia/?t=blot&v=1&id=abcdefghijkl&seed=42&card=c_1&lang=en&diag=1");
  assert.equal(l.seed, 42);
  assert.equal(l.card, "c_1");
  assert.equal(l.diag, true);
  assert.equal(l.wording, null);
});

test("parseLaunch reads dotted cards", () => {
  const l = C.parseLaunch("file:///tilia/?t=spread_three&v=1&id=abcdefghijkl" +
    "&card=00-the-fool.cups-03-three.21-the-world&lang=en");
  assert.deepEqual(l.cards, ["00-the-fool", "cups-03-three", "21-the-world"]);
  assert.equal(l.card, "00-the-fool");  // the first, for single-card code
  const one = C.parseLaunch("file:///tilia/?t=tarot&v=1&id=abcdefghijkl&card=00-the-fool");
  assert.deepEqual(one.cards, ["00-the-fool"]);
  assert.equal(one.card, "00-the-fool");
  assert.deepEqual(C.parseLaunch(BASE).cards, []);
});

test("a malformed tw gives null wording", () => {
  for (const bad of ["#tw=%%%", "#tw=bm90IGpzb24", "#tw=" + b64([1, 2]), "#tw="]) {
    assert.equal(C.parseLaunch(BASE + bad).wording, null, bad);
  }
});

const SHEET = {
  type: "check_story", v: 1, title: "T", pitch: "generic pitch",
  cards: [
    { id: "pitch", kind: "pitch", prompt: "generic pitch card", personal: false },
    { id: "story", kind: "text", prompt: "generic story", personal: true, max_chars: 300 },
    { id: "facts", kind: "text", prompt: "generic facts", personal: false, max_chars: 300 },
    { id: "reads", kind: "chips", prompt: "generic reads", personal: true, chips: ["a", "b"], multi: true },
  ],
};

test("applyWording keeps the structure and replaces only personal wording", () => {
  const out = C.applyWording(SHEET, {
    pitch: "P".repeat(250),
    prompts: { story: "About Riley's text", facts: "ignored, not personal", nope: "unknown id", reads: 7 },
  });
  assert.deepEqual(out.cards.map((c) => [c.id, c.kind]), SHEET.cards.map((c) => [c.id, c.kind]));
  assert.deepEqual(out.cards[3].chips, ["a", "b"]);
  assert.equal(out.pitch.length, 200);
  assert.equal(out.cards[0].prompt.length, 200);
  assert.equal(out.cards[1].prompt, "About Riley's text");
  assert.equal(out.cards[2].prompt, "generic facts");
  assert.equal(out.cards[3].prompt, "generic reads");
  assert.equal(SHEET.pitch, "generic pitch");
  assert.equal(SHEET.cards[1].prompt, "generic story");
  assert.deepEqual(C.applyWording(SHEET, null), SHEET);
});

test("buildPayload gives the exact string", () => {
  const l = C.parseLaunch(BASE);
  assert.equal(C.buildPayload(l, [3, "x", null]),
    '{"v":1,"type":"check_story","id":"abcdefghijkl","answers":[3,"x",null]}');
});

test("budget counts UTF-8 bytes", () => {
  assert.equal(C.byteLength("é"), 2);
  assert.equal(C.byteLength("😀"), 4);
  assert.equal(C.PAYLOAD_MAX, 4096);
  const l = C.parseLaunch(BASE);
  const shell = C.byteLength(C.buildPayload(l, [""]));
  const answers = ["a".repeat(4097 - shell)];
  assert.equal(C.byteLength(C.buildPayload(l, answers)), 4097);
  assert.equal(C.remaining(l, answers), -1);
  assert.equal(C.remaining(l, ["a".repeat(4096 - shell)]), 0);
});

test("draftKey", () => {
  assert.equal(C.draftKey("abcdefghijkl"), "d_abcdefghijkl");
});

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.UTC(2026, 10, 8, 12);

test("makeDraft stamps the time", () => {
  const d = JSON.parse(C.makeDraft(3, [1, "x"], NOW));
  assert.deepEqual(d, { t: NOW, i: 3, a: [1, "x"] });
});

test("readDraft accepts only a stamped draft", () => {
  assert.deepEqual(C.readDraft(C.makeDraft(2, [], NOW)), { t: NOW, i: 2, a: [] });
  for (const bad of [null, "", "nope", "[1]", '{"a":[]}', '{"t":"x","a":[]}', '{"t":1}']) {
    assert.equal(C.readDraft(bad), null, String(bad));
  }
});

test("newestDraft picks the later stamp, whichever store it came from", () => {
  const old = C.makeDraft(1, ["old"], NOW - DAY);
  const fresh = C.makeDraft(2, ["new"], NOW);
  assert.deepEqual(C.newestDraft(old, fresh).a, ["new"]);
  assert.deepEqual(C.newestDraft(fresh, old).a, ["new"]);
  assert.deepEqual(C.newestDraft(null, fresh).a, ["new"]);
  assert.deepEqual(C.newestDraft("garbage", old).a, ["old"]);
  assert.equal(C.newestDraft(null, undefined), null);
});

test("staleDrafts lists d_ keys older than 7 days or unreadable, never the open one", () => {
  const items = {
    d_fresh000000: C.makeDraft(1, [], NOW - 6 * DAY),
    d_old0000000: C.makeDraft(1, [], NOW - 7 * DAY - 1),
    d_edge0000000: C.makeDraft(1, [], NOW - 7 * DAY),
    d_broken00000: "not json",
    d_open0000000: C.makeDraft(1, [], NOW - 30 * DAY),
    diag: "123",
    other: C.makeDraft(1, [], NOW - 30 * DAY),
  };
  assert.deepEqual(C.staleDrafts(items, NOW, "d_open0000000").sort(),
    ["d_broken00000", "d_old0000000"]);
  assert.equal(C.DRAFT_TTL_MS, 7 * DAY);
});
