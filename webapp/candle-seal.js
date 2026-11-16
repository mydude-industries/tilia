/* The candle's words, sealed for the owner's Mac only, and Tilia's two lines opened back.
   A one-time AES-256-GCM key seals the intention; the Mac's RSA-2048 public key (OAEP,
   SHA-256) wraps that key; the answer comes back under the same key, so only this page
   reads it. Matches tilia/seal.py. Wire format, base64url without padding everywhere:
     ask    = b64u(JSON {"k": b64u(RSA-OAEP(raw key)), "iv": b64u(12 bytes), "c": b64u(AES-GCM({"intention"}))})
     answer = b64u(JSON {"iv": b64u(12 bytes), "c": b64u(AES-GCM({"now", "later"}))})
   Uses crypto.subtle only (the browser, or globalThis.crypto in Node). */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.CandleSeal = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var KEY_BYTES = 32;
  var IV_BYTES = 12;
  var B64U = /^[A-Za-z0-9_-]*={0,2}$/;

  function cryptoApi() { return globalThis.crypto; }

  function b64u(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  /* Bytes from base64url with or without padding; throws on anything else. */
  function unb64u(text) {
    if (typeof text !== "string" || !B64U.test(text)) throw new Error("not base64url");
    text = text.replace(/=+$/, "");  // padding is optional: accepted, never required
    if (text.length % 4 === 1) throw new Error("not base64url");
    var s = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4));
    var out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  function json64(obj) { return b64u(new TextEncoder().encode(JSON.stringify(obj))); }

  function unjson64(text) {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(unb64u(text)));
  }

  function isObject(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }

  /* Seal under a given raw AES key. The page uses sealAsk; this seam lets the tests fix the
     key so the cross-language fixture can be checked. The returned key cannot be exported. */
  function sealAskWithRaw(publicB64, intention, raw) {
    var subtle = cryptoApi().subtle;
    var iv = cryptoApi().getRandomValues(new Uint8Array(IV_BYTES));
    var plain = new TextEncoder().encode(JSON.stringify({ intention: intention }));
    var spki;
    try { spki = unb64u(publicB64); } catch (e) { return Promise.reject(e); }
    return Promise.all([
      subtle.importKey("spki", spki, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]),
      subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]),
    ]).then(function (keys) {
      return Promise.all([
        subtle.encrypt({ name: "RSA-OAEP" }, keys[0], raw),
        subtle.encrypt({ name: "AES-GCM", iv: iv }, keys[1], plain),
      ]).then(function (out) {
        var sealed = json64({
          k: b64u(new Uint8Array(out[0])), iv: b64u(iv), c: b64u(new Uint8Array(out[1])),
        });
        return { sealed: sealed, key: keys[1] };
      });
    });
  }

  /* {sealed, key}: the intention sealed for the Mac, and the key that opens its answer. */
  function sealAsk(publicB64, intention) {
    var raw = cryptoApi().getRandomValues(new Uint8Array(KEY_BYTES));
    var done = function () { raw.fill(0); };
    return sealAskWithRaw(publicB64, intention, raw).then(
      function (r) { done(); return r; },
      function (e) { done(); throw e; });
  }

  /* {now, later} from a sealed answer, or null for anything malformed, tampered or empty. */
  function openAnswer(key, sealed) {
    return Promise.resolve().then(function () {
      var outer = unjson64(sealed);
      if (!isObject(outer)) return null;
      var iv = unb64u(outer.iv);
      var c = unb64u(outer.c);
      if (iv.length !== IV_BYTES) return null;
      return cryptoApi().subtle.decrypt({ name: "AES-GCM", iv: iv }, key, c).then(function (plain) {
        var lines = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(plain)));
        if (!isObject(lines) || typeof lines.now !== "string" || typeof lines.later !== "string") {
          return null;
        }
        return { now: lines.now, later: lines.later };
      });
    }).catch(function () { return null; });
  }

  return {
    sealAsk: sealAsk,
    openAnswer: openAnswer,
    _sealAskWithRaw: sealAskWithRaw,
  };
});
