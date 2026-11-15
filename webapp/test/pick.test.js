"use strict";
// Pick one and quick blot (rituals phase 1, D2): the deal, the line under the chosen card,
// and what goes back to Tilia.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../core.js");
const LINES = require("../card-lines.js");

const MANIFEST = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "tarot", "manifest.json"), "utf8"));
const DECK = MANIFEST.filter((e) => e.id !== "card-back");
const IDS = DECK.map((e) => e.id);
const URL_PICK = "file:///tilia/v0.2.3/?t=tarot&v=3&id=abcdefghijkl&seed=424242&mode=pick&lang=en";
const URL_QUICK = "file:///tilia/v0.2.3/?t=blot&v=3&id=abcdefghijkl&seed=99&mode=quick&lang=en";

test("parseLaunch reads mode, only pick or quick", () => {
  assert.equal(C.parseLaunch(URL_PICK).mode, "pick");
  assert.equal(C.parseLaunch(URL_QUICK).mode, "quick");
  assert.equal(C.parseLaunch("file:///t/?t=tarot&v=3&id=abcdefghijkl&card=00-the-fool").mode, null);
  assert.equal(C.parseLaunch("file:///t/?t=tarot&v=3&id=abcdefghijkl&mode=ritual").mode, null);
});

test("same seed same cards: reopening the page deals the same three", () => {
  const seed = C.parseLaunch(URL_PICK).seed;
  const first = C.dealPick(DECK, seed);
  assert.equal(first.length, 3);
  assert.equal(new Set(first).size, 3);
  for (const id of first) assert.ok(IDS.includes(id), id);
  // opened again: a fresh copy of the deck, the same seed
  assert.deepEqual(C.dealPick(JSON.parse(JSON.stringify(MANIFEST)), seed), first);
  assert.deepEqual(C.dealPick(DECK, seed), first);
  assert.equal(DECK.length, 78);  // the deal never changes the deck it was given
});

test("the deal never includes the back, and different seeds deal differently", () => {
  const seen = new Set();
  const hands = new Set();
  for (let s = 1; s <= 400; s++) {
    const hand = C.dealPick(MANIFEST, s);
    assert.ok(!hand.includes("card-back"));
    hand.forEach((id) => seen.add(id));
    hands.add(hand.join());
  }
  assert.ok(hands.size > 390, hands.size);
  assert.ok(seen.size > 70, seen.size);  // the whole deck is in play
});

test("too small a deck deals what it can; the page then says a piece is missing", () => {
  assert.deepEqual(C.dealPick([], 5), []);
  assert.equal(C.dealPick(DECK.slice(0, 2), 5).length, 2);
  assert.deepEqual(C.dealPick([{ id: "card-back" }, { id: "Bad Id" }, null], 5), []);
});

test("every card has one line: plain, short, never a prediction", () => {
  assert.deepEqual(Object.keys(LINES).sort(), IDS.slice().sort());
  for (const [id, line] of Object.entries(LINES)) {
    assert.equal(typeof line, "string", id);
    assert.ok(line.trim() === line && line.length >= 20 && line.length <= 130, `${id}: ${line.length}`);
    assert.ok(!/[\n\r]/.test(line), id);
    assert.ok(/[.]$/.test(line), id);
    assert.ok(!/\byou will\b|\byou'll\b|\bwill happen\b|\bis coming\b|\bsoon\b|\bdestiny\b|\bfate\b/i.test(line), id);
    assert.ok(!/\b(they|he|she) (feels?|thinks?|wants?|loves?)\b/i.test(line), id);  // never what someone else feels
    assert.ok(!/\btherap|counsel|professional\b/i.test(line), id);
    assert.ok(/\bnotice\b/i.test(line), `${id}: points at noticing`);
  }
});

test("flip shows the line: the chosen card's name and its line", () => {
  const [id] = C.dealPick(DECK, 7);
  const face = C.pickFace(DECK, LINES, id);
  assert.equal(face.id, id);
  assert.equal(face.name, DECK.find((e) => e.id === id).name);
  assert.equal(face.line, LINES[id]);
  const star = C.pickFace(DECK, LINES, "17-the-star");
  assert.equal(star.name, "The Star");
  assert.match(star.line, /notice/i);
  // a card the lines don't know still gets a name and a gentle line, never a blank
  const odd = C.pickFace([{ id: "x-1", name: "Odd" }], {}, "x-1");
  assert.equal(odd.name, "Odd");
  assert.ok(odd.line.length > 10);
});

test("payload shape: pick sends the card, quick sends the answer", () => {
  const p = JSON.parse(C.pickPayload(C.parseLaunch(URL_PICK), "17-the-star"));
  assert.deepEqual(p, { v: 1, type: "tarot", id: "abcdefghijkl", mode: "pick", card: "17-the-star" });
  const q = JSON.parse(C.quickPayload(C.parseLaunch(URL_QUICK), "  a moth with torn wings  "));
  assert.deepEqual(q, { v: 1, type: "blot", id: "abcdefghijkl", mode: "quick", answer: "a moth with torn wings" });
  const long = JSON.parse(C.quickPayload(C.parseLaunch(URL_QUICK), "x".repeat(900)));
  assert.equal(long.answer.length, C.QUICK_MAX);
  assert.equal(C.QUICK_MAX, 300);
  assert.equal(C.QUICK_PROMPT, "What's the first thing you see?");
});
