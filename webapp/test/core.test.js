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

test("makeDraft stamps the time and names each answer's card", () => {
  const d = JSON.parse(C.makeDraft(3, [1, "x"], NOW, ["m1", "t"]));
  assert.deepEqual(d, { t: NOW, i: 3, a: [1, "x"], k: ["m1", "t"] });
  assert.deepEqual(JSON.parse(C.makeDraft(3, [1], NOW)), { t: NOW, i: 3, a: [1] });
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

/* ---------- R24: tap-first text cards ---------- */

const TAPS = {
  type: "check_story", v: 2, title: "T", pitch: "p",
  cards: [
    { id: "pitch", kind: "pitch", prompt: "p", personal: false },
    { id: "happened", kind: "text", prompt: "What happened?", personal: true, max_chars: 300,
      option_mode: "starter", options: ["They said…", "I walked in and…", "It started when…"] },
    { id: "story", kind: "text", prompt: "What did it mean?", personal: true, max_chars: 300,
      option_mode: "answer", options: ["They're upset with me", "I messed up", "I don't know"] },
    { id: "friend", kind: "text", prompt: "A friend?", personal: false, max_chars: 300,
      option_mode: "answer", options: ["Go easy", "Ask them", "Sleep on it"] },
    { id: "reads", kind: "chips", prompt: "r", personal: true, chips: ["a", "b"], multi: true },
  ],
};
const MINE = ["She's angry with me", "I said the wrong thing", "I don't know yet"];

test("applyWording takes tailored options for personal answer cards only", () => {
  const out = C.applyWording(TAPS, { options: {
    story: MINE,
    friend: MINE,                                      // not personal
    reads: MINE,                                       // not a text card
    happened: ["Then…", "They said…", "It started when…"],  // R25: starters never tailored
  } });
  assert.deepEqual(out.cards[2].options, MINE);
  assert.deepEqual(out.cards[1].options, TAPS.cards[1].options);
  assert.deepEqual(out.cards[3].options, TAPS.cards[3].options);
  assert.equal(out.cards[4].options, undefined);
  assert.deepEqual(TAPS.cards[2].options, ["They're upset with me", "I messed up", "I don't know"]);
});

test("applyWording keeps the generic options when the tailored ones don't fit the rules", () => {
  for (const bad of [
    MINE.slice(0, 2), MINE.concat(["a", "b", "c"]), MINE.slice(0, 2).concat(["x".repeat(81)]),
    MINE.slice(0, 2).concat(["two\nlines"]), MINE.slice(0, 2).concat([7]), "nope",
    [MINE[0], MINE[0], MINE[1]],
  ]) {
    const out = C.applyWording(TAPS, { options: { story: bad } });
    assert.deepEqual(out.cards[2].options, TAPS.cards[2].options, JSON.stringify(bad));
  }
  const told = C.applyWording(TAPS, { options: { happened: ["Riley ignored you", "She was cold", "You were rude"] } });
  assert.deepEqual(told.cards[1].options, TAPS.cards[1].options);
  // a starter is at most 4 words before its ellipsis
  assert.equal(C.cleanOptions(["Then…", "They said…", "When Riley got home from work…"], "starter"), null);
  assert.deepEqual(C.cleanOptions(["Then...", "They said…", "I walked in and…"], "starter"),
    ["Then…", "They said…", "I walked in and…"]);
});

test("tapOption: an answer fills the box, a starter begins it, typed words are never lost", () => {
  const starter = TAPS.cards[1], answer = TAPS.cards[2];
  assert.equal(C.tapOption(answer, "", "I messed up"), "I messed up");
  assert.equal(C.tapOption(answer, "I messed up", "I don't know"), "I don't know");  // swap
  assert.equal(C.tapOption(answer, "I messed up", "I messed up"), "");              // untap
  assert.equal(C.tapOption(answer, "she was quiet", "I messed up"), "she was quiet. I messed up");
  assert.equal(C.tapOption(answer, "she was quiet. ", "I messed up"), "she was quiet. I messed up");
  assert.equal(C.tapOption(answer, "was she quiet?", "I messed up"), "was she quiet? I messed up");
  assert.equal(C.tapOption(starter, "", "They said…"), "They said ");
  assert.equal(C.tapOption(starter, "They said ", "It started when…"), "It started when ");
  assert.equal(C.tapOption(starter, "They said", "They said…"), "");
  assert.equal(C.tapOption(starter, "Riley left.", "It started when…"), "Riley left. It started when ");
  assert.equal(C.tapOption(starter, "Riley left", "Then…"), "Riley left. Then ");
  assert.equal(C.tapOption(starter, "Riley left.\n", "It started when…"), "Riley left.\nIt started when ");
});

test("pickedOption names the tap the box still holds", () => {
  const starter = TAPS.cards[1], answer = TAPS.cards[2];
  assert.equal(C.pickedOption(answer, "I messed up"), "I messed up");
  assert.equal(C.pickedOption(answer, "I messed up a bit"), null);
  assert.equal(C.pickedOption(starter, "They said "), "They said…");
  assert.equal(C.pickedOption(starter, "They said she was tired"), null);
  assert.equal(C.pickedOption(answer, ""), null);
});

test("the closing feeling card names where they started", () => {
  assert.equal(C.moodPrompt(3), "You started at 3. Where are you now?");
  assert.equal(C.moodPrompt(0), "You started at 0. Where are you now?");
  assert.equal(C.moodPrompt(null), null);
  assert.equal(C.moodPrompt(undefined), null);
});

/* ---------- R26: a draft from before the sheet changed keeps its words ---------- */

const V2 = [
  { id: "mood_start", kind: "mood", min: 0, max: 10 },
  { id: "happened", kind: "text", max_chars: 20 },
  { id: "story", kind: "text", max_chars: 300 },
  { id: "evidence", kind: "text", max_chars: 300 },
  { id: "feel", kind: "chips", chips: ["Calm", "Sad"], multi: true },
  { id: "mood_end", kind: "mood", min: 0, max: 10 },
];

test("restoreAnswers maps by card id when the draft names them", () => {
  const d = { t: NOW, i: 2, a: [4, "she left", ["Sad", "Gone"], "old card words", 12],
              k: ["mood_start", "story", "feel", "sure_text", "mood_end"] };
  assert.deepEqual(C.restoreAnswers(V2, d),
    [4, "old card words", "she left", "", ["Sad"], null]);
});

test("restoreAnswers keeps a same-shape draft by position", () => {
  const d = { t: NOW, i: 2, a: [3, "facts", "story", "", [], 6] };
  assert.deepEqual(C.restoreAnswers(V2, d), [3, "facts", "story", "", [], 6]);
});

test("restoreAnswers never throws words away when the count changed", () => {
  // a v1 draft (no ids, 9 answers) against a v2 sheet: its words go to the empty text
  // cards in order, cut to fit; nothing is paired by position
  const d = { t: NOW, i: 5, a: [3, "Riley went quiet at dinner", "she's angry", 70, "",
                                ["They were stressed"], "she was tired", "", 6] };
  assert.deepEqual(C.restoreAnswers(V2, d),
    [null, "Riley went quiet at", "she's angry", "They were stressed\nshe was tired", [], null]);
});
