"use strict";
// Sealing the candle's words for the Mac only (RSA-OAEP-SHA256 over a one-time AES-256-GCM
// key) and opening Tilia's two lines sealed back under that key. The fixtures in
// tests/fixtures/relay/ cross languages: this test seals the fixed intention to the test
// public key (ask.json), pytest opens it and seals the lines back (answer.json), and this
// test opens those. Each side writes its fixture only when it is missing.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const S = require("../candle-seal.js");

const subtle = globalThis.crypto.subtle;
const FIX = path.join(__dirname, "..", "..", "tests", "fixtures", "relay");
const PUBLIC = path.join(FIX, "test-public-key.txt");
const PRIVATE = path.join(FIX, "test-private-key.pem");   // a test-only key
const ASK = path.join(FIX, "ask.json");
const ANSWER = path.join(FIX, "answer.json");
const INTENTION = "Sam's call at four";                    // Sam is a fake test person
const FIXED_KEY = Uint8Array.from({ length: 32 }, (_, i) => i);  // pytest checks this key

const b64u = (bytes) => Buffer.from(bytes).toString("base64url");
const unb64u = (s) => new Uint8Array(Buffer.from(s, "base64url"));
const json64 = (obj) => b64u(Buffer.from(JSON.stringify(obj)));
const unjson64 = (s) => JSON.parse(Buffer.from(s, "base64url").toString("utf8"));

async function keyPair() {
  const kp = await subtle.generateKey(
    { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["encrypt", "decrypt"]);
  return { publicB64: b64u(new Uint8Array(await subtle.exportKey("spki", kp.publicKey))), privateKey: kp.privateKey };
}

// What the Mac does (tilia/seal.py), written out so the JS round trip needs no Python.
async function openAskLikeTheMac(privateKey, sealed) {
  const outer = unjson64(sealed);
  const raw = new Uint8Array(await subtle.decrypt({ name: "RSA-OAEP" }, privateKey, unb64u(outer.k)));
  const aes = await subtle.importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
  const plain = await subtle.decrypt({ name: "AES-GCM", iv: unb64u(outer.iv) }, aes, unb64u(outer.c));
  return { raw, payload: JSON.parse(new TextDecoder().decode(plain)) };
}

async function sealAnswerLikeTheMac(raw, obj) {
  const aes = await subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt"]);
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const c = await subtle.encrypt({ name: "AES-GCM", iv }, aes, Buffer.from(JSON.stringify(obj)));
  return json64({ iv: b64u(iv), c: b64u(new Uint8Array(c)) });
}

function fixedKey() {
  return subtle.importKey("raw", FIXED_KEY, "AES-GCM", false, ["encrypt", "decrypt"]);
}

test("round trip with a generated key", async () => {
  const { publicB64, privateKey } = await keyPair();
  const { sealed, key } = await S.sealAsk(publicB64, "Riley's walk");
  assert.match(sealed, /^[A-Za-z0-9_-]+$/);
  const outer = unjson64(sealed);
  assert.deepEqual(Object.keys(outer).sort(), ["c", "iv", "k"]);
  assert.equal(unb64u(outer.iv).length, 12);
  const { raw, payload } = await openAskLikeTheMac(privateKey, sealed);
  assert.equal(raw.length, 32);
  assert.deepEqual(payload, { intention: "Riley's walk" });
  assert.equal(key.extractable, false);
  const answer = await sealAnswerLikeTheMac(raw, { now: "Breathe first.", later: "How did it go?" });
  assert.deepEqual(await S.openAnswer(key, answer), { now: "Breathe first.", later: "How did it go?" });
});

test("each ask gets its own key and IV", async () => {
  const { publicB64, privateKey } = await keyPair();
  const a = await openAskLikeTheMac(privateKey, (await S.sealAsk(publicB64, "x")).sealed);
  const b = await openAskLikeTheMac(privateKey, (await S.sealAsk(publicB64, "x")).sealed);
  assert.notDeepEqual(a.raw, b.raw);
});

test("sealAsk rejects a bad public key", async () => {
  await assert.rejects(S.sealAsk("not-a-key", "x"));
});

test("writes fixtures", async () => {
  const publicB64 = fs.readFileSync(PUBLIC, "utf8").trim();
  if (!fs.existsSync(ASK)) {
    const { sealed } = await S._sealAskWithRaw(publicB64, INTENTION, FIXED_KEY);
    fs.writeFileSync(ASK, JSON.stringify({ intention: INTENTION, sealed }, null, 2) + "\n");
  }
  // the committed ask opens with the test private key to the fixed intention and key
  const pem = fs.readFileSync(PRIVATE, "utf8").replace(/-----[^-]+-----|\s/g, "");
  const priv = await subtle.importKey("pkcs8", Buffer.from(pem, "base64"),
    { name: "RSA-OAEP", hash: "SHA-256" }, false, ["decrypt"]);
  const { raw, payload } = await openAskLikeTheMac(priv, JSON.parse(fs.readFileSync(ASK, "utf8")).sealed);
  assert.deepEqual(payload, { intention: INTENTION });
  assert.deepEqual(raw, FIXED_KEY);
});

test("opens answer.json written by Python", async (t) => {
  if (!fs.existsSync(ANSWER)) return t.skip("answer.json not written yet: run uv run pytest tests/test_seal.py");
  const fixture = JSON.parse(fs.readFileSync(ANSWER, "utf8"));
  assert.deepEqual(await S.openAnswer(await fixedKey(), fixture.sealed),
    { now: fixture.now, later: fixture.later });
});

test("openAnswer returns null, never throws, on anything wrong", async () => {
  const key = await fixedKey();
  const good = await sealAnswerLikeTheMac(FIXED_KEY, { now: "a", later: "b" });
  const outer = unjson64(good);
  const c = unb64u(outer.c); c[0] ^= 1;
  const tampered = json64({ iv: outer.iv, c: b64u(c) });
  const otherKey = await subtle.importKey("raw", new Uint8Array(32), "AES-GCM", false, ["decrypt"]);
  const bad = [
    undefined, null, 7, "", "!!!", "{}", json64({}), json64([]), json64({ iv: outer.iv }),
    json64({ iv: "AAAA", c: outer.c }), tampered, good.slice(0, -3),
    await sealAnswerLikeTheMac(FIXED_KEY, {}),
    await sealAnswerLikeTheMac(FIXED_KEY, { now: "a" }),
    await sealAnswerLikeTheMac(FIXED_KEY, { now: "a", later: 3 }),
    await sealAnswerLikeTheMac(FIXED_KEY, ["a", "b"]),
  ];
  for (const s of bad) assert.equal(await S.openAnswer(key, s), null, String(s));
  assert.equal(await S.openAnswer(otherKey, good), null);
  assert.equal(await S.openAnswer(null, good), null);
  assert.deepEqual(await S.openAnswer(key, good), { now: "a", later: "b" });
  const padded = good + "=".repeat((4 - good.length % 4) % 4);
  assert.deepEqual(await S.openAnswer(key, padded), { now: "a", later: "b" });
});
