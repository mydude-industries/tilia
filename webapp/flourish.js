// The spread outline: ten blank cards, a six-card cross inside a circle and a four-card
// staff, drawn as one fine ink line. Geometry from tarot/flourish/tarot-spread.svg. It is
// drawn in currentColor, so CSS decides the ink; strokes keep their width at any size.
"use strict";

window.Flourish = (function () {
  var NS = "http://www.w3.org/2000/svg";
  var CIRCLE = [360, 500, 313];
  var RECTS = [[290, 455, 140, 90, 4], [315, 205, 90, 140, 4], [315, 655, 90, 140, 4], [115, 430, 90, 140, 4], [515, 430, 90, 140, 4], [810, 205, 90, 140, 4], [810, 355, 90, 140, 4], [810, 505, 90, 140, 4], [810, 655, 90, 140, 4]];
  var PATHS = [["", "M315 455 V434 Q315 430 319 430 H401 Q405 430 405 434 V455 M315 545 V566 Q315 570 319 570 H401 Q405 570 405 566 V545", 0], ["centre-dot", "M360 500 h0", 3.2], ["circle-dot", "M360 187 h0", 3.2], ["left-ticks", "M19 500 H35 M25 489 L34 493 M25 511 L34 507", 0]];

  function node(name, attrs) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  // kind: "spread" (the whole drawing, 1000x1000) or "mark" (cards only, small).
  function svg(kind, cls) {
    var mark = kind === "mark";
    var root = node("svg", { viewBox: mark ? "100 190 815 620" : "0 0 1000 1000", "aria-hidden": "true", focusable: "false" });
    root.setAttribute("class", "flourish flourish-" + kind + (cls ? " " + cls : ""));
    var g = node("g", { fill: "none", stroke: "currentColor", "stroke-width": mark ? 1 : 1.2,
      "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" });
    if (!mark) g.appendChild(node("circle", { cx: CIRCLE[0], cy: CIRCLE[1], r: CIRCLE[2], "vector-effect": "non-scaling-stroke" }));
    RECTS.forEach(function (r) {
      g.appendChild(node("rect", { x: r[0], y: r[1], width: r[2], height: r[3], rx: r[4], "vector-effect": "non-scaling-stroke" }));
    });
    PATHS.forEach(function (p) {
      if (mark && p[2]) return;  // the dots and ticks are too small to read at mark size
      var a = { d: p[1], "vector-effect": "non-scaling-stroke" };
      if (p[2]) a["stroke-width"] = p[2];
      g.appendChild(node("path", a));
    });
    root.appendChild(g);
    return root;
  }

  return { svg: svg };
})();
