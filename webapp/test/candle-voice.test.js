"use strict";
// The candle's voice: once it is lit, the page seals their words for the Mac, hands them to
// the relay and waits (at most 30 s) for Tilia's two lines. Nothing here touches the DOM;
// fetch and the clock are passed in, so the waiting is tested without waiting.
const test = require("node:test");
const assert = require("node:assert/strict");
const V = require("../candle-voice.js");
const K = require("../candle-core.js");

const subtle = globalThis.crypto.subtle;
// URLs are put together here: webapp files never spell out another origin (tests/test_webapp.py)
const url = (scheme, rest) => scheme + ":/" + "/" + rest;
const RELAY = url("https", "relay.example.com/tilia");
const MAC = "sam-mac-01";
const ID = "abcdEFGH_-12";
const LIT = Date.UTC(2026, 10, 15, 14, 0);

const b64u = (bytes) => Buffer.from(bytes).toString("base64url");
const unb64u = (s) => new Uint8Array(Buffer.from(s, "base64url"));
const json64 = (obj) => b64u(Buffer.from(JSON.stringify(obj)));
const unjson64 = (s) => JSON.parse(Buffer.from(s, "base64url").toString("utf8"));

let pair = null;
async function keyPair() {
  if (pair) return pair;
  const kp = await subtle.generateKey(
    { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["encrypt", "decrypt"]);
  pair = { publicB64: b64u(new Uint8Array(await subtle.exportKey("spki", kp.publicKey))), privateKey: kp.privateKey };
  return pair;
}

// What the Mac does (tilia/seal.py), so the round trip needs no Python.
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

function reply(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => (body === undefined ? null : body) };
}

// A fake relay: the POST is answered with an ask id, then each GET by `polls` in turn
// (a function given the request, or a reply). The fake clock moves on by each GET's wait.
function fakeRelay(polls, clock) {
  const calls = [];
  async function fetch(url, init = {}) {
    calls.push({ url, init });
    if ((init.method || "GET") === "POST") return reply(200, { ask_id: "ask_0001" });
    const wait = Number(new URL(url).searchParams.get("wait"));
    const next = polls.length > 1 ? polls.shift() : polls[0];
    const r = typeof next === "function" ? await next(url, init) : next;
    if (clock) clock.t += wait * 1000;
    return r;
  }
  return { fetch, calls };
}

test("asks and opens the answer", async () => {
  const { publicB64, privateKey } = await keyPair();
  let raw = null;
  const relay = fakeRelay([
    reply(204),
    async () => reply(200, { sealed: await sealAnswerLikeTheMac(raw, { now: "Breathe first.", later: "Was Sam kind?" }) }),
  ]);
  const realFetch = relay.fetch;
  const fetch = async (url, init = {}) => {
    if (init.method === "POST") {
      const body = JSON.parse(init.body);
      assert.deepEqual(Object.keys(body).sort(), ["entry", "sealed"]);
      assert.equal(body.entry, ID);
      const opened = await openAskLikeTheMac(privateKey, body.sealed);
      assert.deepEqual(opened.payload, { intention: "Sam's call at four" });
      raw = opened.raw;
    }
    return realFetch(url, init);
  };
  const lines = await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "Sam's call at four", fetch });
  assert.deepEqual(lines, { now: "Breathe first.", later: "Was Sam kind?" });
  const urls = relay.calls.map((c) => c.url);
  assert.equal(urls[0], RELAY + "/v1/ask/" + MAC);
  assert.equal(relay.calls[0].init.method, "POST");
  assert.equal(urls[1], RELAY + "/v1/answer/ask_0001?wait=25");
  assert.equal(urls.length, 3);
  // nothing of theirs goes along unsealed, and no cookies or referrer either
  assert.ok(!relay.calls[0].init.body.includes("Sam"));
  assert.equal(relay.calls[0].init.credentials, "omit");
});

test("a line is shown as it arrives, never cut short", async () => {
  const { publicB64, privateKey } = await keyPair();
  const now = "Steady now 🌒🌒🌒 you have done harder things than this, truly"; // astral chars
  let raw = null;
  const fetch = async (url, init = {}) => {
    if (init.method === "POST") {
      raw = (await openAskLikeTheMac(privateKey, JSON.parse(init.body).sealed)).raw;
      return reply(200, { ask_id: "ask_0002" });
    }
    return reply(200, { sealed: await sealAnswerLikeTheMac(raw, { now, later: "And then?" }) });
  };
  const lines = await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x", fetch });
  assert.equal(lines.now, now);
});

test("gives up after 30 s", async () => {
  const { publicB64 } = await keyPair();
  const clock = { t: 1000 };
  const relay = fakeRelay([reply(204)], clock);
  const lines = await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x",
    fetch: relay.fetch, now: () => clock.t });
  assert.equal(lines, null);
  const waits = relay.calls.slice(1).map((c) => Number(new URL(c.url).searchParams.get("wait")));
  assert.deepEqual(waits, [25, 5]);           // 30 s in all, then it settles
  assert.equal(V.WAIT_MS, 30000);
});

test("gives up when the relay never answers at all", async () => {
  const { publicB64 } = await keyPair();
  const hang = () => new Promise(() => {});
  const t0 = Date.now();
  const lines = await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x",
    fetch: hang, budgetMs: 60 });
  assert.equal(lines, null);
  assert.ok(Date.now() - t0 < 2000);
});

test("404 settles", async () => {
  const { publicB64 } = await keyPair();
  for (const answer of [reply(404), reply(200, {}), reply(200, { sealed: "not-sealed" }), reply(400)]) {
    const relay = fakeRelay([answer]);
    assert.equal(await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x", fetch: relay.fetch }), null);
    assert.equal(relay.calls.length, 2);
  }
  // the relay down, the ask refused, or a bad public key: no answer, never a throw
  const down = async () => { throw new TypeError("Failed to fetch"); };
  assert.equal(await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x", fetch: down }), null);
  const refused = async () => reply(429, { error: "slow down" });
  assert.equal(await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x", fetch: refused }), null);
  const noId = async () => reply(200, {});
  assert.equal(await V.ask({ relay: RELAY, mac: MAC, k: publicB64, id: ID, intention: "x", fetch: noId }), null);
  const relay = fakeRelay([reply(204)]);
  assert.equal(await V.ask({ relay: RELAY, mac: MAC, k: "bad-key", id: ID, intention: "x", fetch: relay.fetch }), null);
  assert.equal(relay.calls.length, 0);
});

test("no params, no ask", async () => {
  assert.equal(V.params("?t=candle&id=" + ID), null);
  assert.equal(V.params(""), null);
  assert.equal(V.params(undefined), null);
  assert.equal(V.params("?relay=" + encodeURIComponent(RELAY) + "&mac=" + MAC), null);       // no key
  assert.equal(V.params("?relay=" + encodeURIComponent(RELAY) + "&k=AB-_cdEF12"), null);     // no mac
  const relay = fakeRelay([reply(204)]);
  assert.equal(await V.ask({ relay: null, mac: MAC, k: "AB", id: ID, intention: "x", fetch: relay.fetch }), null);
  assert.equal(await V.ask({ relay: RELAY, mac: MAC, k: "AB", id: "short", intention: "x", fetch: relay.fetch }), null);
  assert.equal(relay.calls.length, 0);
});

test("params: a percent-encoded https relay, the Mac's id and its public key", () => {
  const q = "?t=candle&id=" + ID + "&relay=" + encodeURIComponent(RELAY + "/") + "&mac=" + MAC + "&k=MIIBIjAN-_abc";
  assert.deepEqual(V.params(q), { relay: RELAY, mac: MAC, k: "MIIBIjAN-_abc" });
  assert.deepEqual(V.params(new URLSearchParams(q)), { relay: RELAY, mac: MAC, k: "MIIBIjAN-_abc" });
});

test("rejects a non-https relay", () => {
  const p = (relay, mac = MAC, k = "MIIBIjAN-_abc") =>
    V.params("?relay=" + encodeURIComponent(relay) + "&mac=" + mac + "&k=" + k);
  assert.equal(p(url("http", "relay.example.com")), null);
  assert.equal(p("javascript:alert(1)"), null);
  assert.equal(p("ftp://relay.example.com"), null);
  assert.equal(p("relay.example.com"), null);
  assert.equal(p(url("https", "sam:pw@relay.example.com")), null);       // no credentials in a URL
  assert.equal(p(url("https", "relay.example.com/?x=1")), null);         // a base, nothing more
  assert.equal(p(url("https", "relay.example.com/#x")), null);
  assert.equal(p(RELAY, "no"), null);                              // a mac id is 6-32 of [A-Za-z0-9_-]
  assert.equal(p(RELAY, "sam mac 01"), null);
  assert.equal(p(RELAY, MAC, "not+b64/url="), null);
  // a relay on this machine, for development (R7)
  assert.deepEqual(p(url("http", "127.0.0.1:8787")), { relay: url("http", "127.0.0.1:8787"), mac: MAC, k: "MIIBIjAN-_abc" });
  assert.equal(p(url("http", "localhost:8787")).relay, url("http", "localhost:8787"));
  assert.equal(p(url("http", "[::1]:8787")), null);                     // the CSP doesn't allow it
  assert.equal(p(url("http", "127.0.0.2:8787")), null);
});

test("the voice runs inside Telegram, or with a relay on this machine (R8)", () => {
  const on = V.params("?relay=" + encodeURIComponent(RELAY) + "&mac=" + MAC + "&k=MIIBIjAN-_abc");
  const dev = V.params("?relay=" + encodeURIComponent(url("http", "127.0.0.1:8787")) + "&mac=" + MAC + "&k=MIIBIjAN-_abc");
  const devName = V.params("?relay=" + encodeURIComponent(url("http", "localhost:8787")) + "&mac=" + MAC + "&k=MIIBIjAN-_abc");
  assert.equal(V.mayAsk(on, true), true);           // in Telegram: the real relay
  assert.equal(V.mayAsk(on, false), false);         // a plain browser: no ask at all
  assert.equal(V.mayAsk(dev, false), true);         // a dev relay on this machine
  assert.equal(V.mayAsk(devName, false), true);
  assert.equal(V.mayAsk(dev, true), true);
  assert.equal(V.mayAsk(null, true), false);        // no relay params
});

test("reopen asks again", () => {
  // closed while the smoke waited: lit, no later line saved yet, so it asks again (the Mac
  // re-sends the saved lines without a second model call)
  const waiting = K.restore(K.litValue(LIT, "Sam's call at four"), LIT + 60000);
  assert.equal(V.needsAsk(waiting), true);
  // the lines came back last time: the later line is kept, no ask
  const answered = K.restore(K.litValue(LIT, "Sam's call at four", "Was Sam kind?"), LIT + 60000);
  assert.equal(answered.later, "Was Sam kind?");
  assert.equal(V.needsAsk(answered), false);
  assert.equal(V.needsAsk(K.restore(null, LIT)), false);
  assert.equal(V.needsAsk(K.restore(K.pendingValue(LIT, "x", LIT + 1, "Fine."), LIT + 2)), false);
});

test("the line is held long enough to read", () => {
  assert.equal(V.holdMs("Breathe."), 2500 + 60 * 8);
  assert.ok(V.holdMs("x".repeat(60)) <= 6000);
  assert.equal(V.holdMs("🌒🌒"), 2500 + 60 * 2);    // characters, not UTF-16 units
});

test("a late reopen asks for later but never speaks now (R9)", () => {
  const LIT = Date.UTC(2026, 10, 15, 12, 0, 0);
  const waiting = K.restore(K.litValue(LIT, "Sam's call at four"), LIT + 11 * 60000);
  assert.equal(V.needsAsk(waiting), true);                       // still asks, for blow-out
  assert.equal(V.NOW_MS, 10 * 60000);
  assert.equal(V.speaksNow(LIT, LIT), true);
  assert.equal(V.speaksNow(LIT, LIT + 10 * 60000), true);        // at the boundary: as today
  assert.equal(V.speaksNow(LIT, LIT + 10 * 60000 + 1), false);   // past it: never spelled
  assert.equal(V.speaksNow(LIT, LIT + 11 * 60000), false);
  assert.equal(V.speaksNow(undefined, LIT), false);
  assert.equal(V.speaksNow(NaN, LIT), false);
});
