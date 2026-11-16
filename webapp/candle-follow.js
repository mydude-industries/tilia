// The stage after the candle is out: one quiet, optional field for anything they want to
// tell Tilia, and Done. Owned by the follow module: candle.js only calls
// CandleFollow.init(el, onDone), CandleFollow.show() and CandleFollow.reset().
// onDone(text) receives what they typed, or "" when they typed nothing (that's fine).
//
// It arrives softly under the candle once the smoke has finished speaking. Done warms the
// field (or, with nothing typed, lets it settle) and hands the words over.
"use strict";

window.CandleFollow = (function () {
  var OPEN = 620, LEAD = 140;

  var el, onDone, form, field, send;
  var done = false, timers = [];

  function make(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function calm() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function init(container, cb) {
    el = container; onDone = cb;
    el.textContent = "";
    el.classList.add("fu");
    var wrap = make("div", "fu-wrap");

    var label = make("label", "fu-ask", "Anything to tell Tilia?");
    label.setAttribute("for", "fu-field");
    wrap.appendChild(label);

    form = make("form", "fu-form");
    form.setAttribute("autocomplete", "off");
    field = make("input", "fu-field");
    field.id = "fu-field";
    field.type = "text";
    field.maxLength = window.CandleCore.ANSWER_MAX;   // = tilia/entries.py CANDLE_ANSWER_MAX
    field.placeholder = "Optional";
    field.setAttribute("enterkeyhint", "done");
    field.setAttribute("autocapitalize", "sentences");
    send = make("button", "fu-send", "Done");
    send.type = "submit";
    form.appendChild(field); form.appendChild(send);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      finish();
    });
    wrap.appendChild(form);
    el.appendChild(wrap);
    restore();
  }

  // the space opens smoothly (so the note below glides instead of jumping), then the
  // question and its field rise into it
  function show() {
    if (!el || !el.hidden) return;
    el.hidden = false;
    el.classList.add("on");
    if (calm()) { el.classList.add("fu-in"); return; }
    var h = el.offsetHeight;   // layout height; scrollHeight would count the risen offset
    el.style.height = "0px";
    el.style.overflow = "hidden";
    void el.offsetHeight;
    el.style.transition = "height " + OPEN + "ms cubic-bezier(.25,.7,.3,1)";
    el.style.height = h + "px";
    later(function () { el.classList.add("fu-in"); }, LEAD);
    later(function () {
      el.style.transition = ""; el.style.height = ""; el.style.overflow = "";
    }, OPEN + LEAD + 900);
  }

  function finish() {
    if (done) return;
    done = true;
    var text = field.value.replace(/\s+/g, " ").trim();
    field.value = text;
    field.blur();
    field.readOnly = true; send.disabled = true;
    el.classList.add(text ? "fu-said" : "fu-quiet");
    onDone(text);
  }

  function restore() {
    timers.forEach(clearTimeout); timers = [];
    done = false;
    el.classList.remove("on", "fu-in", "fu-said", "fu-quiet");
    el.style.height = ""; el.style.overflow = ""; el.style.transition = "";
    el.hidden = true;
    field.value = ""; field.readOnly = false; send.disabled = false;
    if (document.activeElement === field) field.blur();
  }

  function reset() { if (el) restore(); }

  return { init: init, show: show, reset: reset };
})();
