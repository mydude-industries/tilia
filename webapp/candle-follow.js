// The follow-up stage after the candle is out: the taps and what happens when one is
// picked. Owned by the follow module: candle.js only calls CandleFollow.init(el, onPick),
// CandleFollow.show() and CandleFollow.reset(). onPick(text) receives the chosen answer.
//
// The stage arrives as a quiet question: the space opens under the candle, the answers
// rise in one after another, and a softer fifth lets you say it yourself. A pick warms
// the chosen answer, the rest recede and fold away so "Noted." and the buttons below fit.
"use strict";

window.CandleFollow = (function () {
  var OPTIONS = ["Better than I feared.", "About what I expected.", "Harder than I hoped.", "It didn't happen."];
  var STAGGER = 80, LEAD = 120, RISE = 640, OPEN = 560, FOLD_AT = 760, FOLD = 620;

  var el, onPick, list, own, form, field, send, items = [];
  var picked = false, timers = [];

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

  function init(container, pick) {
    el = container; onPick = pick;
    el.textContent = "";
    el.classList.add("fu");
    list = make("div", "fu-list");
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", "How did it go?");

    OPTIONS.forEach(function (text) {
      var b = make("button", "fu-tap", text);
      b.type = "button";
      b.addEventListener("click", function () { choose(b, text); });
      list.appendChild(b);
    });

    own = make("button", "fu-own", "Say it in my own words");
    own.type = "button";
    own.addEventListener("click", openField);
    list.appendChild(own);

    form = make("form", "fu-form");
    form.setAttribute("autocomplete", "off");
    field = make("input", "fu-field");
    field.type = "text";
    field.maxLength = 200;
    field.placeholder = "However it went…";
    field.setAttribute("aria-label", "How it went, in your own words");
    field.setAttribute("enterkeyhint", "done");
    field.setAttribute("autocapitalize", "sentences");
    send = make("button", "fu-send", "Keep");
    send.type = "submit";
    send.setAttribute("aria-label", "Keep this answer");
    form.appendChild(field); form.appendChild(send);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = field.value.replace(/\s+/g, " ").trim();
      if (!text) { field.focus(); return; }
      field.blur();
      choose(form, text);
    });
    list.appendChild(form);

    el.appendChild(list);
    items = Array.prototype.slice.call(list.children);
    items.forEach(function (n, i) { n.style.setProperty("--i", i); });
    restore();
  }

  // the space opens smoothly (so the note below glides instead of jumping), then the
  // answers rise into it one after another
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
    }, Math.max(OPEN, LEAD + STAGGER * items.length + RISE) + 40);
  }

  function openField() {
    if (picked) return;
    form.hidden = false;
    own.hidden = true;
    form.classList.add("fu-opening");
    // focus inside the tap so the iPhone keyboard comes up, then keep the field in view
    // above it once the keyboard has slid in
    try { field.focus({ preventScroll: true }); } catch (e) { field.focus(); }
    field.scrollIntoView({ block: "center" });
    later(function () {
      form.classList.remove("fu-opening");
      if (!picked) field.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 360);
  }

  function choose(node, text) {
    if (picked) return;
    picked = true;
    el.classList.add("fu-picked");
    node.classList.add("fu-chosen");
    items.forEach(function (n) {
      if (n !== node) n.classList.add("fu-away");
      if (n.tagName === "BUTTON") n.disabled = true;
    });
    field.readOnly = true; send.disabled = true;
    if (node === form) field.value = text;
    node.setAttribute("aria-current", "true");
    onPick(text);
    // the receded answers fold away so the stage settles around the one that was chosen
    var away = items.filter(function (n) { return n !== node && !n.hidden; });
    if (calm()) { away.forEach(function (n) { n.hidden = true; }); return; }
    later(function () {
      away.forEach(function (n) {
        n.style.maxHeight = n.offsetHeight + "px";
        void n.offsetHeight;
        n.classList.add("fu-fold");
        n.style.maxHeight = "0px";
      });
      later(function () {
        away.forEach(function (n) { n.hidden = true; n.style.maxHeight = ""; n.classList.remove("fu-fold"); });
      }, FOLD + 40);
    }, FOLD_AT);
  }

  function restore() {
    timers.forEach(clearTimeout); timers = [];
    picked = false;
    el.classList.remove("on", "fu-in", "fu-picked");
    el.style.height = ""; el.style.overflow = ""; el.style.transition = "";
    el.hidden = true;
    items.forEach(function (n) {
      n.classList.remove("fu-chosen", "fu-away", "fu-fold", "fu-opening");
      n.removeAttribute("aria-current");
      n.style.maxHeight = "";
      n.hidden = n === form;
      if (n.tagName === "BUTTON") n.disabled = false;
    });
    field.value = ""; field.readOnly = false; send.disabled = false;
    if (document.activeElement === field) field.blur();
  }

  function reset() { if (el) restore(); }

  return { init: init, show: show, reset: reset };
})();
