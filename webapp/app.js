/* Tilia worksheet page: one card per screen, answers go back with sendData. */
(function () {
  "use strict";

  var C = window.TiliaCore;
  var tg = (window.Telegram && window.Telegram.WebApp) || null;
  var plain = !tg || tg.platform === "unknown";
  var launch = C.parseLaunch(location.href);

  var $sheet = document.getElementById("sheet");
  var $bar = document.getElementById("bar");
  var $back = document.getElementById("back");
  var $progress = document.getElementById("progress");
  var $diag = document.getElementById("diag");

  var sheet = null;
  var cards = [];      // every card, pitch first
  var asked = [];      // the answer cards (everything but the pitch), in payload order
  var answers = [];
  var at = 0;          // index into cards
  var saveTimer = null;
  var deck = [];       // tarot/manifest.json: id, name, file, symbols per card
  var turned = {};     // index into cards -> that reveal's card has been turned over
  var reveals = [];    // a spread's reveal cards, one per position, in order

  /* ---------- small helpers ---------- */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function versionAtLeast(v) {
    try {
      return !plain && tg.isVersionAtLeast(v);
    } catch (e) {
      return false;
    }
  }

  function emptyAnswer(card) {
    if (card.kind === "text") return "";
    if (card.kind === "chips") return [];
    return null;
  }

  function answerIndex(card) {
    return asked.indexOf(card);
  }

  /* ---------- drafts: CloudStorage first, the webview's localStorage if that fails ---------- */

  var local = {
    keys: function () {
      try {
        var out = [];
        for (var n = 0; n < window.localStorage.length; n++) out.push(window.localStorage.key(n));
        return out;
      } catch (e) { return []; }
    },
    get: function (k) {
      try { return window.localStorage.getItem(k); } catch (e) { return null; }
    },
    set: function (k, v) {
      try { window.localStorage.setItem(k, v); } catch (e) { /* no storage; the draft lives in memory */ }
    },
    remove: function (k) {
      try { window.localStorage.removeItem(k); } catch (e) { /* nothing to clear */ }
    },
  };

  var useCloud = versionAtLeast("6.9") && !!tg.CloudStorage;

  function cloudCall(method, args, done) {
    try {
      tg.CloudStorage[method].apply(tg.CloudStorage, args.concat([function (err, val) {
        done(err || null, val);
      }]));
    } catch (e) {
      done(e);
    }
  }

  function saveDraft() {
    var key = C.draftKey(launch.id);
    var value = C.makeDraft(at, answers, Date.now());
    if (!useCloud) return local.set(key, value);
    cloudCall("setItem", [key, value], function (err) {
      if (err) local.set(key, value);
    });
  }

  function saveSoon() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraft, 400);
  }

  // The newer of the cloud and local drafts: a cloud save that failed leaves an older
  // cloud copy behind, and the local one written instead must win.
  function loadDraft(done) {
    var key = C.draftKey(launch.id);
    if (!useCloud) return done(C.readDraft(local.get(key)));
    // If CloudStorage never answers, open with the local draft rather than hang.
    var settled = false;
    function once(d) {
      if (settled) return;
      settled = true;
      done(d);
    }
    setTimeout(function () { once(C.readDraft(local.get(key))); }, 1500);
    cloudCall("getItem", [key], function (err, val) {
      once(C.newestDraft(err ? null : val, local.get(key)));
    });
  }

  // Abandoned drafts hold personal text, so none outlives an open entry's 7 days.
  // Runs after the page is up and never holds it back.
  function sweepDrafts() {
    var keep = C.draftKey(launch.id), now = Date.now();
    try {
      var items = {};
      local.keys().forEach(function (k) { if (k && k.slice(0, 2) === "d_") items[k] = local.get(k); });
      C.staleDrafts(items, now, keep).forEach(local.remove);
    } catch (e) { /* nothing to sweep */ }
    if (!useCloud) return;
    cloudCall("getKeys", [], function (err, keys) {
      if (err || !Array.isArray(keys)) return;
      var drafts = keys.filter(function (k) { return k.slice(0, 2) === "d_" && k !== keep; });
      if (!drafts.length) return;
      cloudCall("getItems", [drafts], function (err2, vals) {
        if (err2 || !vals) return;
        var stale = C.staleDrafts(vals, now, keep);
        if (stale.length) cloudCall("removeItems", [stale], function () {});
      });
    });
  }

  function clearDraft() {
    clearTimeout(saveTimer);
    var key = C.draftKey(launch.id);
    local.remove(key);
    if (useCloud) cloudCall("removeItem", [key], function () {});
  }

  function restore(d) {
    if (!d || d.a.length !== asked.length) return;
    for (var i = 0; i < asked.length; i++) {
      var c = asked[i], a = d.a[i];
      if (c.kind === "text" && typeof a === "string") answers[i] = a.slice(0, c.max_chars);
      else if (c.kind === "chips" && Array.isArray(a)) {
        answers[i] = a.filter(function (x) { return c.chips.indexOf(x) >= 0; });
      } else if ((c.kind === "mood" || c.kind === "slider") && typeof a === "number"
                 && a >= c.min && a <= c.max) answers[i] = Math.round(a);
    }
    if (C.remaining(launch, answers) < 0) answers = asked.map(emptyAnswer);
    else if (typeof d.i === "number" && d.i > 0 && d.i < cards.length) at = d.i;
  }

  /* ---------- the bar: Back and progress ---------- */

  function renderBar() {
    var card = cards[at];
    $bar.hidden = card.kind === "pitch";
    $progress.textContent = "";
    var n = answerIndex(card);
    asked.forEach(function (c, i) {
      var li = el("li");
      if (i < n) li.className = "done";
      else if (i === n) { li.className = "now"; li.setAttribute("aria-current", "step"); }
      $progress.appendChild(li);
    });
    $progress.setAttribute("aria-label", "Card " + (n + 1) + " of " + asked.length);
    if (versionAtLeast("6.1") && tg.BackButton) {
      if (at > 0) tg.BackButton.show(); else tg.BackButton.hide();
    }
  }

  function go(i) {
    clearTimeout(saveTimer);
    at = Math.max(0, Math.min(cards.length - 1, i));
    saveDraft();
    render();
    window.scrollTo(0, 0);
  }

  $back.addEventListener("click", function () { go(at - 1); });
  if (versionAtLeast("6.1") && tg.BackButton) {
    try { tg.BackButton.onClick(function () { go(at - 1); }); } catch (e) { /* older client */ }
  }

  /* ---------- inputs, one per kind ---------- */

  function chipsInput(card, i) {
    var wrap = el("div", "chips");
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", card.multi ? "Pick any that fit" : "Pick one");
    card.chips.forEach(function (label) {
      var b = el("button", "chip", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(answers[i].indexOf(label) >= 0));
      b.addEventListener("click", function () {
        var cur = answers[i];
        var on = cur.indexOf(label) >= 0;
        var next = on ? cur.filter(function (x) { return x !== label; })
                      : (card.multi ? cur.concat([label]) : [label]);
        var trial = answers.slice();
        trial[i] = next;
        if (C.remaining(launch, trial) < 0) {
          full.hidden = false;
          return;
        }
        full.hidden = true;
        answers[i] = next;
        Array.prototype.forEach.call(wrap.children, function (c) {
          c.setAttribute("aria-pressed", String(next.indexOf(c.textContent) >= 0));
        });
        saveSoon();
      });
      wrap.appendChild(b);
    });
    if (card.multi) wrap.appendChild(el("p", "hint", "Tap any that fit."));
    var full = el("p", "hint full", "No room left for that one. Shorten an earlier answer to add it.");
    full.hidden = true;
    full.setAttribute("role", "status");
    wrap.appendChild(full);
    return wrap;
  }

  function textInput(card, i) {
    var wrap = el("div", "write");
    var box = el("textarea", "box");
    box.rows = 5;
    box.maxLength = card.max_chars;
    box.value = answers[i];
    box.setAttribute("aria-label", card.short);
    box.placeholder = "Write as much or as little as you like.";
    var left = el("p", "left");
    var last = box.value;

    function count() {
      var bytes = C.remaining(launch, answers);
      var chars = card.max_chars - box.value.length;  // the same units maxLength counts
      var n = Math.min(bytes, chars);
      left.textContent = n + " left";
      left.classList.toggle("low", n <= 30);
    }

    box.addEventListener("input", function () {
      var trial = answers.slice();
      trial[i] = box.value;
      if (C.remaining(launch, trial) < 0) {
        // Keep as much of the new text as fits, trimming back from the caret.
        var v = box.value, caret = box.selectionStart;
        var start = Math.max(0, caret - (v.length - last.length));
        var added = Array.from(v.slice(start, caret));
        do {
          added.pop();
          trial[i] = v.slice(0, start) + added.join("") + v.slice(caret);
        } while (added.length && C.remaining(launch, trial) < 0);
        if (C.remaining(launch, trial) < 0) trial[i] = last;
        box.value = trial[i];
        caret = start + added.join("").length;
        box.setSelectionRange(caret, caret);
      }
      last = box.value;
      answers[i] = box.value;
      count();
      saveSoon();
    });
    count();
    wrap.appendChild(box);
    wrap.appendChild(left);
    return wrap;
  }

  function moodInput(card, i) {
    var wrap = el("div", "mood");
    var row = el("div", "scale");
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", card.prompt);
    for (var v = card.min; v <= card.max; v++) {
      (function (v) {
        var b = el("button", "step", String(v));
        b.type = "button";
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(answers[i] === v));
        b.addEventListener("click", function () {
          answers[i] = answers[i] === v ? null : v;
          Array.prototype.forEach.call(row.children, function (c) {
            c.setAttribute("aria-checked", String(answers[i] === Number(c.textContent)));
          });
          saveSoon();
        });
        row.appendChild(b);
      })(v);
    }
    var ends = el("div", "ends");
    ends.appendChild(el("span", null, "0 is low"));
    ends.appendChild(el("span", null, "10 is good"));
    wrap.appendChild(row);
    wrap.appendChild(ends);
    return wrap;
  }

  function sliderInput(card, i) {
    var wrap = el("div", "slider");
    var val = el("p", "value");
    var range = el("input", "range");
    range.type = "range";
    range.min = card.min;
    range.max = card.max;
    range.step = 1;
    range.setAttribute("aria-label", card.short);
    var mid = Math.round((card.min + card.max) / 2);
    range.value = answers[i] === null ? mid : answers[i];

    function show() {
      var set = answers[i] !== null;
      wrap.classList.toggle("unset", !set);
      val.textContent = set ? String(answers[i]) : "Not set yet";
    }
    range.addEventListener("input", function () {
      answers[i] = Number(range.value);
      show();
      saveSoon();
    });
    var ends = el("div", "ends");
    ends.appendChild(el("span", null, card.labels[0]));
    ends.appendChild(el("span", null, card.labels[1]));
    show();
    wrap.appendChild(val);
    wrap.appendChild(range);
    wrap.appendChild(ends);
    return wrap;
  }

  function whyToggle(card) {
    var frag = document.createDocumentFragment();
    var b = el("button", "why", "Why this?");
    b.type = "button";
    b.setAttribute("aria-expanded", "false");
    var p = el("p", "because", card.why);
    p.hidden = true;
    p.id = "why-" + card.id;
    b.setAttribute("aria-controls", p.id);
    b.addEventListener("click", function () {
      p.hidden = !p.hidden;
      b.setAttribute("aria-expanded", String(!p.hidden));
    });
    frag.appendChild(b);
    frag.appendChild(p);
    return frag;
  }

  /* ---------- the blot ---------- */

  // Nudges for the eye, one at a time, in this order. They never offer a meaning.
  var NUDGES = ["Look at the white space.", "What's moving?", "Turn your phone upside down.",
                "Is it one thing or two?"];

  function isBlot() {
    return sheet.type === "blot";
  }

  // The canvas goes in with the card; the blot is drawn after the card has painted
  // (about 30 ms on a laptop, longer on a phone), and once per seed: drawBlot caches it.
  function blotCanvas(cls) {
    var c = el("canvas", "blot " + cls);
    c.width = window.TiliaBlot.SIZE;
    c.height = window.TiliaBlot.SIZE;
    c.setAttribute("role", "img");
    c.setAttribute("aria-label", "An ink blot, mirrored down the middle");
    requestAnimationFrame(function () {
      setTimeout(function () {
        try {
          window.TiliaBlot.drawBlot(c, launch.seed);
          c.classList.add("drawn");
        } catch (e) { /* the questions still work without the picture */ }
      }, 0);
    });
    return c;
  }

  function helpToSee(nudges) {
    var frag = document.createDocumentFragment();
    var b = el("button", "why", "Help me see it");
    b.type = "button";
    var p = el("p", "nudge");
    p.hidden = true;
    p.setAttribute("role", "status");
    var n = 0;
    b.addEventListener("click", function () {
      p.textContent = nudges[n];
      p.hidden = false;
      n += 1;
      if (n === nudges.length) b.hidden = true;
    });
    frag.appendChild(p);
    frag.appendChild(b);
    return frag;
  }

  /* ---------- the tarot card ---------- */

  // When the deck lists no things to look at for a card, the eye gets these instead,
  // one at a time. Like the blot's, they point at the picture and never say what it means.
  var CARD_NUDGES = ["Look at the hands.", "What's in the background?",
                     "Where is the light coming from?", "Who is looking where?"];
  var CARD_BACK = "card-back";

  function isTarot() {
    return sheet.type === "tarot";
  }

  // The spreads, by sheet type: each lays its positions out its own way (app.css).
  var LAYOUTS = {
    spread_three: "three",
    spread_cross: "cross",
    spread_relationship: "relationship",
  };

  function isSpread() {
    return Object.prototype.hasOwnProperty.call(LAYOUTS, sheet.type);
  }

  function firstReveal() {
    return cards.findIndex(function (c) { return c.kind === "reveal"; });
  }

  function deckEntry(id) {
    for (var n = 0; n < deck.length; n++) if (deck[n] && deck[n].id === id) return deck[n];
    return null;
  }

  // The card's own things from the deck, one at a time, then the question; or the
  // general nudges. Only what is in the picture: no names of what it stands for.
  function cardNudges(id) {
    var info = deckEntry(id);
    var things = info && Array.isArray(info.symbols) ? info.symbols.filter(function (x) {
      return typeof x === "string" && x;
    }) : [];
    return things.length ? things.concat(["Which one grabs you?"]) : CARD_NUDGES;
  }

  // The deck's name for a card; in a spread, a name read from the id when the deck
  // doesn't list it ("cups-03-three" is the Three of Cups), so a face is never blank.
  function cardName(id) {
    var info = deckEntry(id);
    if (info && typeof info.name === "string") return info.name;
    return isSpread() ? idName(id) : "";
  }

  function idName(id) {
    var cap = function (w) { return w.charAt(0).toUpperCase() + w.slice(1); };
    var minor = /^(wands|cups|swords|pentacles)-\d+-([a-z]+)$/.exec(id);
    if (minor) return cap(minor[2]) + " of " + cap(minor[1]);
    return id.replace(/^\d+-/, "").split("-").map(function (w, n) {
      return n && (w === "of" || w === "the") ? w : cap(w);
    }).join(" ");
  }

  // The face; if its art fails to load, the card's name on paper instead, never a blank.
  function cardImage(cls, id) {
    var img = el("img", cls);
    img.alt = cardName(id) || "The card";
    img.decoding = "async";
    img.addEventListener("error", function () {
      var paper = el("div", "paper-face" + (cls ? " " + cls : ""), cardName(id) || "The card");
      if (img.parentNode) img.parentNode.replaceChild(paper, img);
    });
    img.src = "tarot/" + id + ".webp";
    return img;
  }

  // The deck's own back when it ships one; otherwise (or if it fails to load) a plain
  // ink-on-paper back drawn in CSS.
  function cardBack() {
    var drawn = el("div", "drawn-back");
    var back = deckEntry(CARD_BACK);
    if (!back || typeof back.file !== "string") return drawn;
    var img = el("img");
    img.src = "tarot/" + back.file;
    img.alt = "";
    img.addEventListener("error", function () {
      if (img.parentNode) img.parentNode.replaceChild(drawn, img);
    });
    return img;
  }

  // A card id the deck doesn't list: face down on the drawn back, and the questions go on.
  function renderMissingCard(s) {
    var face = el("div", "tarot");
    var back = el("span", "face face-back");
    back.appendChild(el("div", "drawn-back"));
    face.appendChild(back);
    s.appendChild(face);
    s.appendChild(el("p", "hint lead", "This card couldn't load."));
    var next = el("button", "next", "Next");
    next.type = "button";
    next.addEventListener("click", function () { go(at + 1); });
    s.appendChild(next);
  }

  // A spread's cards, laid out by position: "down" (all face down, named: the pitch),
  // "mini" (small and unnamed above each card's turn; the turned ones face up) or "up"
  // (all face up, named: the card about the whole spread).
  function spreadMap(mode) {
    var ol = el("ol", "spread spread-" + LAYOUTS[sheet.type] + " spread-" + mode);
    ol.setAttribute("aria-label", mode === "up" ? "The whole spread, face up"
      : "Where the cards lie");
    reveals.forEach(function (rc, n) {
      var id = launch.cards[n];
      var li = el("li", "slot p" + (n + 1));
      var up = mode === "up" || (mode === "mini" && turned[cards.indexOf(rc)]);
      if (mode === "mini" && cards[at] === rc) {
        li.classList.add("here");
        li.setAttribute("aria-current", "step");
      }
      var face = el("span", "slot-card");
      face.appendChild(up ? cardImage("", id) : cardBack());
      li.appendChild(face);
      if (mode === "mini") {
        li.setAttribute("aria-label", rc.prompt + (up ? ": " + cardName(id) : ""));
      } else {
        li.appendChild(el("span", "slot-pos", rc.prompt));
        if (up) li.appendChild(el("span", "slot-name", cardName(id)));
      }
      ol.appendChild(li);
    });
    return ol;
  }

  // Above a spread card's question: that card, small, and where it lies.
  function spreadCardAt(n) {
    var id = launch.cards[n];
    var row = el("div", "card-at");
    row.appendChild(cardImage("tarot-small", id));
    var words = el("div");
    words.appendChild(el("p", "card-at-pos", reveals[n].prompt));
    words.appendChild(el("p", "card-at-name", cardName(id)));
    row.appendChild(words);
    return row;
  }

  function renderTarotReveal(s, card, id) {
    var spread = isSpread();
    if (!spread && !deckEntry(id)) return renderMissingCard(s);
    var flipped = !!turned[at];
    var turn = el("button", "tarot" + (flipped ? " flipped" : ""));
    turn.type = "button";
    turn.setAttribute("aria-label", "The card, face down. Tap to turn it over.");
    var faces = el("span", "faces");
    var back = el("span", "face face-back");
    back.appendChild(cardBack());
    var front = el("span", "face face-front");
    front.appendChild(cardImage("", id));
    faces.appendChild(back);
    faces.appendChild(front);
    turn.appendChild(faces);
    s.appendChild(turn);

    var name = el("p", "card-name", cardName(id));
    s.appendChild(name);
    var tap = el("p", "hint lead", "Tap the card to turn it over.");
    s.appendChild(tap);
    var lead = el("p", "hint lead", card.prompt);
    s.appendChild(lead);
    var help = el("div", "help");
    help.appendChild(helpToSee(cardNudges(id)));
    s.appendChild(help);
    var next = el("button", "next", "Next");
    next.type = "button";
    next.addEventListener("click", function () { go(at + 1); });
    s.appendChild(next);

    function show() {
      turn.classList.toggle("flipped", flipped);
      turn.disabled = flipped;
      if (flipped) turn.setAttribute("aria-label", cardName(id) || "The card, face up");
      name.hidden = !flipped || !cardName(id);
      tap.hidden = flipped;
      lead.hidden = !flipped;
      help.hidden = !flipped;
      next.hidden = !flipped;
    }
    turn.addEventListener("click", function () {
      flipped = turned[at] = true;
      if (spread) {  // the small map above turns this card over too
        var map = s.querySelector(".spread-mini");
        if (map) s.replaceChild(spreadMap("mini"), map);
      }
      show();
    });
    show();
  }

  function renderReveal(card) {
    var i = answerIndex(card);
    var s = el("div", "card reveal");
    s.appendChild(el("p", "label", (i + 1) + " of " + asked.length + " · " + card.short));
    if (isSpread()) {
      s.classList.add("spread-reveal");
      s.appendChild(spreadMap("mini"));
      renderTarotReveal(s, card, launch.cards[reveals.indexOf(card)]);
      return s;
    }
    if (isTarot()) {
      renderTarotReveal(s, card, launch.card);
      return s;
    }
    s.appendChild(blotCanvas("full"));
    s.appendChild(el("p", "hint lead", card.prompt));
    s.appendChild(helpToSee(NUDGES));
    var next = el("button", "next", "Next");
    next.type = "button";
    next.addEventListener("click", function () { go(at + 1); });
    s.appendChild(next);
    return s;
  }

  /* ---------- screens ---------- */

  function renderPitch(card) {
    var s = el("div", "card pitch");
    s.appendChild(el("p", "label", sheet.title));
    s.appendChild(el("h1", "q", card.prompt));
    if (isSpread()) s.appendChild(spreadMap("down"));
    s.appendChild(el("p", "hint", asked.length + " short cards. Skip any you like."));
    var go1 = el("button", "next", "Begin");
    go1.type = "button";
    go1.addEventListener("click", function () { go(1); });
    s.appendChild(go1);
    return s;
  }

  function renderQuestion(card) {
    var i = answerIndex(card);
    var s = el("div", "card");
    s.appendChild(el("p", "label", (i + 1) + " of " + asked.length + " · " + card.short));
    // A blot's questions keep the blot in view, small, above the question.
    if (isBlot() && card.kind !== "mood") s.appendChild(blotCanvas("small"));
    // and a card's, once it has been turned over
    if (isTarot() && turned[firstReveal()] && deckEntry(launch.card) && card.kind !== "mood") {
      s.appendChild(cardImage("tarot-small", launch.card));
    }
    // A spread card's question sits under that card; "all together" under the whole spread.
    if (isSpread() && card.kind !== "mood") {
      var prev = reveals.indexOf(cards[at - 1]);
      if (prev >= 0 && turned[at - 1]) s.appendChild(spreadCardAt(prev));
      else if (card.id === "together") s.appendChild(spreadMap("up"));
    }
    var q = el("h1", "q", card.prompt);
    s.appendChild(q);
    if (card.why) s.appendChild(whyToggle(card));
    if (card.kind === "chips") s.appendChild(chipsInput(card, i));
    else if (card.kind === "text") s.appendChild(textInput(card, i));
    else if (card.kind === "mood") s.appendChild(moodInput(card, i));
    else if (card.kind === "slider") s.appendChild(sliderInput(card, i));
    var last = at === cards.length - 1;
    var next = el("button", "next", last ? "Send to Tilia" : "Next");
    next.type = "button";
    next.addEventListener("click", function () { if (last) send(); else go(at + 1); });
    s.appendChild(next);
    return s;
  }

  function render() {
    renderBar();
    var card = cards[at];
    $sheet.textContent = "";
    $sheet.appendChild(card.kind === "pitch" ? renderPitch(card)
      : card.kind === "reveal" ? renderReveal(card) : renderQuestion(card));
  }

  function showMessage(title, body) {
    $bar.hidden = true;
    $sheet.textContent = "";
    var s = el("div", "card");
    s.appendChild(el("h1", "q", title));
    if (body) s.appendChild(el("p", "hint", body));
    $sheet.appendChild(s);
  }

  /* ---------- send ---------- */

  function send() {
    if (C.remaining(launch, answers) < 0) {
      showMessage("This is a little too long to send.",
                  "Go back and shorten one of your answers, then send again.");
      return;
    }
    var payload = C.buildPayload(launch, answers);
    clearDraft();
    if (plain) {
      $bar.hidden = true;
      $sheet.textContent = "";
      var s = el("div", "card");
      s.appendChild(el("p", "label", "Outside Telegram"));
      s.appendChild(el("h1", "q", "This is what Tilia would get."));
      s.appendChild(el("p", "hint", C.byteLength(payload) + " of " + C.PAYLOAD_MAX + " bytes"));
      s.appendChild(el("pre", "payload", payload));
      $sheet.appendChild(s);
      return;
    }
    tg.sendData(payload);
  }

  /* ---------- device check (diag=1) ---------- */

  function diagnose() {
    $diag.hidden = false;
    var rows = [
      ["tw found", launch.wording ? "yes, decoded" :
        (new URLSearchParams(location.hash.slice(1)).has("tw") ? "present, not decodable" : "no")],
      ["CloudStorage", "checking…"],
      ["isFullscreen", String(!!(tg && tg.isFullscreen))],
      ["version", tg ? String(tg.version) : "no Telegram"],
      ["platform", tg ? String(tg.platform) : "no Telegram"],
    ];
    var dl = el("dl");
    var cells = {};
    rows.forEach(function (r) {
      dl.appendChild(el("dt", null, r[0]));
      cells[r[0]] = dl.appendChild(el("dd", null, r[1]));
    });
    var cloudCell = cells.CloudStorage;
    if (tg && tg.onEvent) {
      tg.onEvent("fullscreenChanged", function () {
        cells.isFullscreen.textContent = String(!!tg.isFullscreen);
      });
    }
    $diag.textContent = "";
    $diag.appendChild(el("h2", null, "Device check"));
    $diag.appendChild(dl);

    if (!versionAtLeast("6.9") || !tg.CloudStorage) {
      cloudCell.textContent = "not available (needs 6.9)";
      return;
    }
    var stamp = String(Date.now());
    cloudCall("setItem", ["diag", stamp], function (err) {
      if (err) { cloudCell.textContent = "set failed: " + err; return; }
      cloudCall("getItem", ["diag"], function (err2, val) {
        cloudCell.textContent = err2 ? "get failed: " + err2 :
          (val === stamp ? "round-trip ok" : "round-trip mismatch");
      });
    });
  }

  /* ---------- start ---------- */

  function start() {
    if (tg && !plain) {
      try { tg.ready(); } catch (e) { /* keep going */ }
      try { tg.expand(); } catch (e) { /* keep going */ }
      if (versionAtLeast("8.0")) {
        try { tg.requestFullscreen(); } catch (e) { /* not on this client */ }
      }
    }
    if (launch.diag) diagnose();

    if (!/^[a-z_]+$/.test(launch.type || "") || !/^[A-Za-z0-9_-]{12}$/.test(launch.id || "")) {
      showMessage("This link is missing a piece.", "Ask Tilia to send it again.");
      return;
    }
    fetch("worksheets/" + launch.type + ".json", { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(function (raw) {
        if (raw.type !== "tarot" && !LAYOUTS[raw.type]) return raw;
        // The deck's names, things to look at, and back. Without it the card still shows.
        return fetch("tarot/manifest.json", { cache: "no-cache" })
          .then(function (r) { return r.ok ? r.json() : []; })
          .catch(function () { return []; })
          .then(function (man) {
            deck = Array.isArray(man) ? man : [];
            return raw;
          });
      })
      .then(function (raw) {
        if (raw.type === "blot" && !(launch.seed >= 1 && window.TiliaBlot)) {
          showMessage("This link is missing a piece.", "Ask Tilia to send it again.");
          return;
        }
        if (raw.type === "tarot" && (launch.cards.length !== 1
            || !/^[a-z0-9-]+$/.test(launch.card || ""))) {
          showMessage("This link is missing a piece.", "Ask Tilia to send it again.");
          return;
        }
        var positions = raw.cards.filter(function (c) { return c.kind === "reveal"; }).length;
        if (LAYOUTS[raw.type] && (launch.cards.length !== positions
            || !launch.cards.every(function (id, n) {
              return /^[a-z0-9-]+$/.test(id) && launch.cards.indexOf(id) === n;
            }))) {
          showMessage("This link is missing a piece.", "Ask Tilia to send it again.");
          return;
        }
        sheet = C.applyWording(raw, launch.wording);
        cards = sheet.cards;
        reveals = cards.filter(function (c) { return c.kind === "reveal"; });
        asked = cards.filter(function (c) { return c.kind !== "pitch"; });
        answers = asked.map(emptyAnswer);
        document.title = sheet.title + " · Tilia";
        loadDraft(function (draft) {
          if (draft) restore(draft);
          // Coming back past a card: it was already turned over.
          cards.forEach(function (c, n) { if (c.kind === "reveal" && n < at) turned[n] = true; });
          render();
          setTimeout(function () {
            try { sweepDrafts(); } catch (e) { /* the sweep is housekeeping only */ }
          }, 0);
        });
      })
      .catch(function () {
        showMessage("This worksheet didn't load.", "Close it and tap Open again in the chat.");
      });
  }

  start();
})();
