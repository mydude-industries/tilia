"""The mirror: one self-contained page, mirror.html in the data folder (0600), that shows
every finished worksheet, card and blot. No server, no network: the CSS, the blot
generator (webapp/blot.js, verbatim), a small renderer and the data are all inline, and a
Content-Security-Policy refuses every load except inline code and data: images.

User text only ever travels as JSON inside <script type="application/json"> (every <, >
and & escaped), and the renderer puts it on the page with textContent, never as HTML."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from . import coach, entries, paths

ALIAS_NAME = "Tilia Mirror.html"
REPLY = "Open Tilia Mirror on your Mac. It's in your home folder."
WRITE_FAILED = ("I couldn't update Tilia Mirror just now, so the page on your Mac may be "
                "out of date. Try /mirror again in a bit.")
TAROT_DIR = paths.REPO_ROOT / "webapp" / "tarot"
BLOT_JS = paths.REPO_ROOT / "webapp" / "blot.js"
FLOURISH_JS = paths.REPO_ROOT / "webapp" / "flourish.js"
_CARD_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_SMALL = {"of", "the", "and"}


def path() -> Path:
    return paths.data_dir() / "mirror.html"


# ---- the data -------------------------------------------------------------------

def _local(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso).astimezone() if iso else None
    except ValueError:
        return None


def _sheet(type_: str) -> dict | None:
    try:
        return entries.load_sheet(type_)
    except ValueError:
        return None


def _answer_text(card: dict | None, a) -> str | None:
    if a is None or a == "" or a == []:
        return None
    if isinstance(a, list):
        return ", ".join(str(x) for x in a)
    if card is not None and card["kind"] == "slider":
        return f"{a} of {card['max']}"
    return str(a)


def _qa(sheet: dict | None, wording: dict | None, answers: list | None) -> list[dict]:
    answers = answers or []
    if sheet is None or not entries.fits(sheet, answers):
        # no sheet to pair them with (a type that has none yet), or one that has changed
        # since they answered (an older sheet version): the words alone
        return [{"q": "", "a": t} for t in (_answer_text(None, a) for a in answers
                                            if not isinstance(a, int)) if t]
    prompts = (wording or {}).get("prompts") or {}
    out = []
    for card, a in zip(entries.answer_cards(sheet), answers):
        if card["kind"] in ("mood", "reveal"):
            continue
        t = _answer_text(card, a)
        if t:
            out.append({"q": prompts.get(card["id"]) or card["prompt"], "a": t})
    return out


_MINOR = re.compile(r"(wands|cups|swords|pentacles)-\d+-([a-z]+)")


def _card_name(cid: str) -> str:
    """A name read from the id when the deck doesn't list it, as the page reads it:
    `cups-03-three` is the Three of Cups, `00-the-fool` the Fool."""
    m = _MINOR.fullmatch(cid)
    if m:
        return f"{m[2].capitalize()} of {m[1].capitalize()}"
    words = re.sub(r"^\d+-", "", cid).split("-")
    return " ".join(w if i and w in _SMALL else w.capitalize() for i, w in enumerate(words))


def _card(cid: str | None, art: dict) -> dict | None:
    if not cid or not _CARD_ID.fullmatch(cid):
        return None
    info = None
    fn = getattr(entries, "card_info", None)  # the deck (Task 9) may not be here yet
    if fn is not None:
        try:
            info = fn(cid)
        except Exception:
            info = None
    name = info.get("name") if isinstance(info, dict) else None
    out = {"name": name if isinstance(name, str) and name.strip() else _card_name(cid)}
    f = TAROT_DIR / f"{cid}.webp"
    if f.is_file():
        if cid not in art:
            art[cid] = "data:image/webp;base64," + base64.b64encode(f.read_bytes()).decode()
        out["art"] = cid
    return out


def _spread(e, sheet, art) -> dict:
    """Each position (n counts from 1) with its card and what they said there, and the
    "together" answer. `paired` is False when the answers don't fit the sheet; their
    words then show on their own, as for any worksheet."""
    said: dict[str, str | None] = {}
    paired = sheet is not None and entries.fits(sheet, e.answers or [])
    if paired:
        said = {c["id"]: _answer_text(c, a)
                for c, a in zip(entries.answer_cards(sheet), e.answers)}
    positions = entries.SPREADS[e.type]
    slots = []
    for n, cid in enumerate(entries.card_ids(e.card)):
        card = _card(cid, art)
        if card is None:
            continue
        slots.append({"n": n + 1, "pos": positions[n] if n < len(positions) else f"Card {n + 1}",
                      "card": card, "says": said.get(f"says_{n + 1}")})
    return {"layout": e.type, "slots": slots, "together": said.get("together"),
            "paired": paired}


def _happened(e, user_turns) -> str | None:
    """The first thing they said after the follow-up asked how it went."""
    at = _local(e.followup_at)
    if at is None:
        return None
    for t in user_turns:
        when = _local(t.at)
        if when is not None and when > at:
            return t.text
    return None


def _item(e, user_turns, art, episode: bool = False) -> dict:
    sheet = _sheet(e.type) if e.type in entries.TYPES else None
    when = _local(e.answered_at) or _local(e.created)
    card = _card(e.card, art) if e.type == "tarot" else None
    title = (card["name"] if card else None) or (sheet or {}).get("title") \
        or e.type.replace("_", " ").capitalize()
    item = {
        "id": e.id,
        "type": e.type,
        "title": title,
        "pitch": (e.wording or {}).get("pitch") or (sheet or {}).get("pitch"),
        "qa": _qa(sheet, e.wording, e.answers),
        "reply": e.reply,
        "mood": [e.mood_before, e.mood_after],
        "themes": [t for t in e.themes if t in entries.THEMES],
        "happened": _happened(e, user_turns),
        "day": when.day if when else None,
        "dow": f"{when:%a}" if when else "",
        "month": f"{when:%B %Y}" if when else "",
        "when": f"{when:%A} {when.day} {when:%B %Y}, {when:%H:%M}" if when else "",
    }
    if e.type == "blot" and e.seed is not None:
        item["seed"] = e.seed
    if card:
        item["card"] = card
    if e.type in entries.SPREADS:
        item["spread"] = _spread(e, sheet, art)
        if item["spread"].pop("paired"):
            item["qa"] = []  # the answers sit with their positions instead
        # The sealed read gets its own place when they were shown it (the reply opens
        # with it).
        if e.sealed_read and e.reply and e.sealed_read in e.reply:
            item["read"] = e.sealed_read
            item["reply"] = e.reply.split(e.sealed_read, 1)[1].strip() or None
    # Fix 4: a read that was held back (a crisis, or a reply that fell back) shows on its
    # own, labelled as written before they looked; never while a crisis episode is open.
    if (e.type in entries.TAROT_TYPES and e.sealed_read and "read" not in item
            and not (e.reply and e.sealed_read in e.reply) and not episode):
        item["held_read"] = e.sealed_read
    return item


def _patterns(store) -> dict | None:
    rows = store.entries(types=("patterns",), statuses=("done",))
    if not rows:
        return None
    newest = rows[-1]
    items = []
    for p in newest.answers or []:
        if not isinstance(p, dict) or not isinstance(p.get("text"), str):
            continue
        dates = []
        for d in p.get("dates") or []:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                dates.append(f"{dt.day} {dt:%b %Y}")
            except (TypeError, ValueError):
                continue
        items.append({"text": p["text"], "dates": dates})
    made = _local(newest.created)
    return {"items": items, "made": f"{made.day} {made:%B %Y}" if made else ""} if items else None


def data(store, now) -> dict:
    user_turns = [t for t in store.recent_turns(10**9) if t.role == "user"]
    art: dict[str, str] = {}
    done = [e for e in store.entries(statuses=("done",)) if e.type in entries.TYPES]
    episode = coach.in_episode(store, now)
    items = [_item(e, user_turns, art, episode) for e in reversed(done)]  # newest first
    n = now.astimezone()
    return {"made": f"{n:%A} {n.day} {n:%B %Y}, {n:%H:%M}", "patterns": _patterns(store),
            "entries": items, "art": art}


# ---- the page -------------------------------------------------------------------

def _json_for_script(obj) -> str:
    return (json.dumps(obj, ensure_ascii=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))


def render(store, now) -> str:
    blot_js = BLOT_JS.read_text(encoding="utf-8")
    flourish_js = FLOURISH_JS.read_text(encoding="utf-8")
    for name, src in (("blot.js", blot_js), ("flourish.js", flourish_js)):
        if "</script" in src.lower() or "<!--" in src:
            raise ValueError(f"{name} cannot be inlined as it is")
    return (_HEAD + "<script type=\"application/json\" id=\"data\">"
            + _json_for_script(data(store, now)) + "</script>\n<script>\n" + blot_js
            + "\n</script>\n<script>\n" + flourish_js
            + "\n</script>\n<script>\n" + _RENDER + "</script>\n</body>\n</html>\n")


def write(store, now) -> Path:
    """Atomic: a private (0600) temp file in the data folder, then os.replace."""
    html = render(store, now)
    dest = path()
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".mirror-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:  # closes fd whatever fails next
            os.fchmod(f.fileno(), 0o600)
            f.write(html)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def after_forget(store, now) -> None:
    """Rewrite the mirror after a /forget. If that fails, remove it: a stale page must
    never keep what was just forgotten."""
    try:
        write(store, now)
    except Exception:
        path().unlink(missing_ok=True)


def ensure_alias(home: Path | None = None) -> Path | None:
    """~/Tilia Mirror.html -> the mirror. Replaces only a symlink; a real file stays."""
    link = (Path.home() if home is None else home) / ALIAS_NAME
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        return None
    link.symlink_to(path())
    return link


_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex">
<title>Tilia Mirror</title>
<style>
:root {
  color-scheme: light;
  --paper: #fbfaf7;
  --paper-sunk: #f3f1ea;
  --ink: #1d1d1b;
  --ink-soft: #4a4843;
  --muted: #6f6d67;
  --rule: #d8d5cc;
  --green: #3f6b5a;
  --blot-paper: #f6f1e6;    /* blots and cards keep their own paper in both modes */
  --card-ink: #1d1d1b;
  --shade: 29, 29, 27;
  --serif: "New York", ui-serif, "Iowan Old Style", "Charter", "Noto Serif", Georgia, serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --s1: 4px; --s2: 8px; --s3: 16px; --s4: 24px; --s5: 32px; --s6: 48px;
  --r: 12px; --r-small: 8px;
  --date-col: 52px;
}
/* D16: dark, as the worksheet page (webapp/app.css): warm charcoal, cream, the green
   lifted to 7.5:1 on the ground. Change both together. */
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --paper: #23211d;
    --paper-sunk: #2c2a25;
    --ink: #ebe5d6;
    --ink-soft: #cdc6b6;
    --muted: #a29c8f;
    --rule: #45413a;
    --green: #8cbba5;
    --shade: 8, 7, 6;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.45 var(--sans);
  -webkit-font-smoothing: antialiased;
}
:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }
main { max-width: 760px; margin: 0 auto; padding: var(--s6) var(--s3) var(--s6); }

.label {
  margin: 0 0 var(--s2);
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--green);
}

header h1 {
  margin: 0;
  font: 500 40px/1.1 var(--serif);
  letter-spacing: -0.015em;
}
header p { margin: var(--s2) 0 0; max-width: 34em; color: var(--ink-soft); }
header .made { color: var(--muted); font-size: 14px; }

/* what keeps showing up: the weekly patterns */
.patterns { margin-top: var(--s5); padding: var(--s4) 0 0; border-top: 1px solid var(--ink); }
.patterns ol { margin: 0; padding: 0; list-style: none; counter-reset: p; }
.patterns li {
  counter-increment: p;
  display: grid;
  grid-template-columns: 28px 1fr;
  padding: var(--s3) 0;
  border-bottom: 1px solid var(--rule);
}
.patterns li::before {
  content: counter(p);
  font: italic 19px/1.35 var(--serif);
  color: var(--green);
}
.patterns .obs { margin: 0; font: 19px/1.4 var(--serif); text-wrap: pretty; }
.patterns .dates { grid-column: 2; margin: var(--s1) 0 0; font-size: 14px; color: var(--muted); font-variant-numeric: tabular-nums; }
.patterns .by { margin: var(--s2) 0 0; font-size: 14px; color: var(--muted); }

/* the timeline */
.month { margin: var(--s6) 0 0; padding-bottom: var(--s2); border-bottom: 1px solid var(--ink); font: italic 500 22px/1.2 var(--serif); }
.empty { margin-top: var(--s5); font: 19px/1.4 var(--serif); color: var(--ink-soft); }

details { border-bottom: 1px solid var(--rule); }
summary {
  list-style: none;
  display: grid;
  grid-template-columns: var(--date-col) 1fr auto 20px;
  align-items: center;
  gap: var(--s3);
  padding: var(--s3) var(--s2) var(--s3) 0;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: background-color 120ms ease-out;
}
summary::-webkit-details-marker { display: none; }
summary:hover { background: var(--paper-sunk); }
.date { align-self: start; text-align: center; font-variant-numeric: tabular-nums; }
.date b { display: block; font: 500 28px/1 var(--serif); }
.date span { display: block; margin-top: var(--s1); font-size: 13px; color: var(--muted); }
.what { min-width: 0; }
.what h3 { margin: 0; font: 500 20px/1.25 var(--serif); }
.what .gist { margin: 2px 0 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: var(--ink-soft); }
.what .feel { margin: 2px 0 0; font-size: 14px; color: var(--muted); font-variant-numeric: tabular-nums; }
.thumb { display: block; width: 56px; border-radius: var(--r-small); }
canvas.thumb { height: 56px; background: var(--blot-paper); }
img.thumb { height: 84px; object-fit: cover; }
.chev { justify-self: end; color: var(--muted); font-size: 22px; line-height: 1; transition: transform 120ms ease-out; }
details[open] .chev { transform: rotate(90deg); }

/* both sides */
.sides { display: grid; gap: var(--s4); padding: var(--s2) 0 var(--s5); }
.side > :last-child { margin-bottom: 0; }
.you .pitch { margin: 0 0 var(--s3); font: italic 17px/1.4 var(--serif); color: var(--ink-soft); }
.you .seen { display: block; width: 100%; max-width: 320px; margin: 0 0 var(--s4); border-radius: var(--r-small); }
.you canvas.seen { aspect-ratio: 1; height: auto; background: var(--blot-paper); }
.you img.seen { max-width: 220px; }
.you .cardname { margin: 0 0 var(--s3); font: italic 19px/1.3 var(--serif); }
dl { margin: 0; }
dt { font-size: 14px; color: var(--muted); }
dd { margin: var(--s1) 0 var(--s3); font: 18px/1.4 var(--serif); white-space: pre-wrap; overflow-wrap: anywhere; }
.tilia { align-self: start; padding: var(--s4) var(--s3); background: var(--paper-sunk); border-radius: var(--r); }
.tilia .reply { margin: 0 0 var(--s4); font: 18px/1.45 var(--serif); white-space: pre-wrap; overflow-wrap: anywhere; }
.tilia .after { margin: 0 0 var(--s4); font: 18px/1.4 var(--serif); white-space: pre-wrap; overflow-wrap: anywhere; }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: var(--s1) var(--s3); margin: 0; font-size: 15px; }
.facts dt { font-size: 15px; }
.facts dd { margin: 0; font: 15px/1.45 var(--sans); font-variant-numeric: tabular-nums; }

/* a spread, laid out as on the phone: three in a row; you and them with the space
   between in the middle; the cross as a centre pair, what's asked above, the root below,
   where it's heading to the right */
.fan { display: flex; }
.fan > * { flex: none; width: 34px; height: 51px; margin-left: -20px; border-radius: 4px;
  object-fit: cover; background: var(--blot-paper); box-shadow: 0 0 0 1px rgba(var(--shade), 0.10); }
.fan > :first-child { margin-left: 0; }
/* the same layouts as webapp/app.css (spreads); change both together */
.spread {
  --cw: 56px; --col: 84px;
  display: grid;
  grid-template-columns: repeat(3, var(--col));
  justify-content: center;
  gap: var(--s3) var(--s2);
  align-items: start;
  margin: 0 0 var(--s4);
  padding: 0;
  list-style: none;
}
.slot { display: flex; flex-direction: column; align-items: center; min-width: 0; text-align: center; }
.slot-card {
  display: flex; align-items: center; justify-content: center;
  width: var(--cw); aspect-ratio: 2 / 3; overflow: hidden;
  border-radius: 6px; background: var(--blot-paper);
  box-shadow: 0 0 0 1px rgba(var(--shade), 0.08), 0 1px 2px rgba(var(--shade), 0.06);
}
.slot-card img { display: block; width: 100%; height: 100%; object-fit: cover; }
.paper-face { background: var(--blot-paper); color: var(--card-ink); }
.slot-card .paper-face { padding: var(--s1); font: 10px/1.2 var(--serif); }
.slot-pos { margin-top: var(--s2); font: 14px/1.25 var(--serif); color: var(--ink-soft); text-wrap: balance; }
.slot-name { margin-top: 2px; font-size: 12px; line-height: 1.3; color: var(--muted); }
.spread-three { grid-template-areas: "p1 p2 p3"; }
.spread-relationship { grid-template-areas: "p1 p3 p2"; }
.spread-cross {
  grid-template-areas: "p4 p4 ." "p1 p2 p5" "p3 p3 .";
  grid-template-columns: var(--col) var(--col) calc(var(--col) + 12px);
}
.spread .p1 { grid-area: p1; } .spread .p2 { grid-area: p2; } .spread .p3 { grid-area: p3; }
.spread .p4 { grid-area: p4; } .spread .p5 { grid-area: p5; }
.spread-cross .p5 { justify-self: end; width: var(--col); }
.tilia .read { margin: 0 0 var(--s4); font: italic 18px/1.45 var(--serif); white-space: pre-wrap; overflow-wrap: anywhere; }
/* the spread outline: a small mark above Tilia's sealed read, and faint behind the page */
.flourish-mark { display: block; width: 44px; height: 34px; margin: 0 0 var(--s2); color: var(--muted); opacity: 0.85; }
.flourish-wm { position: fixed; right: -6vw; bottom: -4vh; width: 58vw; max-width: 520px; aspect-ratio: 1; color: var(--ink); opacity: 0.05; pointer-events: none; z-index: 0; }
#root { position: relative; z-index: 1; }

footer { margin-top: var(--s6); font-size: 14px; color: var(--muted); max-width: 36em; }

@media (min-width: 720px) {
  main { padding-left: var(--s5); padding-right: var(--s5); }
  header h1 { font-size: 48px; }
  .sides { grid-template-columns: 1fr 1fr; gap: var(--s5); padding-left: calc(var(--date-col) + var(--s3)); }
}
@media (max-width: 420px) {
  :root { --date-col: 40px; }
  summary { gap: 12px; }
  .date b { font-size: 24px; }
  .thumb { width: 48px; }
  canvas.thumb { height: 48px; }
  img.thumb { height: 72px; }
}
@media (max-width: 359px) {
  /* the cross's long names don't fit three across: a list, in the order they're read */
  .spread-cross { display: flex; flex-direction: column; gap: var(--s2); }
  .spread-cross .slot { display: grid; grid-template-columns: 40px 1fr; column-gap: 12px; text-align: left; --cw: 40px; }
  .spread-cross .slot-card { grid-row: 1 / span 2; }
  .spread-cross .slot-pos { margin: 0; align-self: end; }
  .spread-cross .slot-name { align-self: start; }
}
@media (prefers-reduced-motion: reduce) { summary, .chev { transition: none; } }
</style>
</head>
<body>
<main id="root">
<header>
<h1>Tilia Mirror</h1>
<p>Every worksheet, card and blot you&rsquo;ve finished, newest first. Tap one to see both sides: what you wrote, and what Tilia said back.</p>
<p class="made" id="made"></p>
</header>
</main>
<noscript><p style="max-width:760px;margin:0 auto;padding:0 16px">This page needs JavaScript to show your entries. It stays on this Mac either way.</p></noscript>
"""

# Builds the page from the JSON above with textContent only. No "</" may appear here.
_RENDER = r"""(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('data').textContent);
  var root = document.getElementById('root');
  document.body.appendChild(Flourish.svg('spread', 'flourish-wm'));

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function add(parent, child) { parent.appendChild(child); return child; }
  function feel(m) {
    if (!m || (m[0] === null && m[1] === null)) return null;
    var f = function (x) { return x === null ? '?' : x; };
    return f(m[0]) + ' → ' + f(m[1]);
  }
  function blotCanvas(cls, px, seed, queue) {
    var c = el('canvas', cls);
    c.width = px; c.height = px;
    c.setAttribute('role', 'img');
    c.setAttribute('aria-label', 'The ink blot');
    queue.push(function () { window.drawBlot(c, seed); });
    return c;
  }
  function cardImg(cls, card) {
    var i = el('img', cls);
    i.src = D.art[card.art];
    i.alt = card.name;
    return i;
  }
  // a card's art, or its name on the card's paper when the art isn't here
  function face(cls, card) {
    return card.art ? cardImg(cls, card) : el('span', 'paper-face' + (cls ? ' ' + cls : ''), card.name);
  }
  function spreadEl(sp) {
    var ol = el('ol', 'spread spread-' + sp.layout.replace('spread_', ''));
    sp.slots.forEach(function (x) {
      var li = add(ol, el('li', 'slot p' + x.n));
      add(add(li, el('span', 'slot-card')), face('', x.card));
      add(li, el('span', 'slot-pos', x.pos));
      add(li, el('span', 'slot-name', x.card.name));
    });
    return ol;
  }

  document.getElementById('made').textContent = 'Made ' + D.made + '. Only on this Mac.';

  if (D.patterns) {
    var ps = add(root, el('section', 'patterns'));
    add(ps, el('h2', 'label', 'What keeps showing up'));
    var ol = add(ps, el('ol'));
    D.patterns.items.forEach(function (p) {
      var li = add(ol, el('li'));
      add(li, el('p', 'obs', p.text));
      if (p.dates.length) add(li, el('p', 'dates', p.dates.join(', ')));
    });
    add(ps, el('p', 'by', 'Noticed by Tilia from your entries, ' + D.patterns.made + '.'));
  }

  var thumbs = [];
  if (!D.entries.length) {
    add(root, el('p', 'empty', 'Nothing here yet. When you finish a worksheet, card or blot, it shows up here.'));
  }
  var month = null;
  D.entries.forEach(function (e) {
    if (e.month !== month) {
      month = e.month;
      add(root, el('h2', 'month', month));
    }
    var d = add(root, el('details'));
    var s = add(d, el('summary'));
    var date = add(s, el('div', 'date'));
    add(date, el('b', null, e.day));
    add(date, el('span', null, e.dow));
    var what = add(s, el('div', 'what'));
    add(what, el('h3', null, e.title));
    var gist = e.qa.length ? e.qa[0].a : ((e.spread && e.spread.together) || e.reply || '');
    if (gist) add(what, el('p', 'gist', gist));
    var f = feel(e.mood);
    if (f) add(what, el('p', 'feel', 'feeling ' + f));
    if (e.seed !== undefined) add(s, blotCanvas('thumb', 112, e.seed, thumbs));
    else if (e.card && e.card.art) add(s, cardImg('thumb', e.card));
    else if (e.spread && e.spread.slots.length) {
      var fan = add(s, el('span', 'fan'));
      fan.setAttribute('aria-hidden', 'true');
      e.spread.slots.slice(0, 3).forEach(function (x) { add(fan, face('', x.card)); });
    }
    else add(s, el('span'));
    var chev = add(s, el('span', 'chev', '›'));
    chev.setAttribute('aria-hidden', 'true');

    var built = false;
    d.addEventListener('toggle', function () {
      if (!d.open || built) return;
      built = true;
      var later = [];
      var sides = add(d, el('div', 'sides'));
      var you = add(sides, el('section', 'side you'));
      add(you, el('h4', 'label', 'You'));
      if (e.pitch) add(you, el('p', 'pitch', e.pitch));
      if (e.seed !== undefined) add(you, blotCanvas('seen', 512, e.seed, later));
      if (e.card) {
        if (e.card.art) add(you, cardImg('seen', e.card));
        add(you, el('p', 'cardname', e.card.name));
      }
      if (e.spread) {
        add(you, spreadEl(e.spread));
        var sdl = add(you, el('dl'));
        e.spread.slots.forEach(function (x) {
          if (!x.says) return;
          add(sdl, el('dt', null, x.pos + ' · ' + x.card.name));
          add(sdl, el('dd', null, x.says));
        });
        if (e.spread.together) {
          add(sdl, el('dt', null, 'All of them together'));
          add(sdl, el('dd', null, e.spread.together));
        }
      }
      if (e.qa.length) {
        var dl = add(you, el('dl'));
        e.qa.forEach(function (x) {
          if (x.q) add(dl, el('dt', null, x.q));
          add(dl, el('dd', null, x.a));
        });
      }
      var t = add(sides, el('section', 'side tilia'));
      if (e.read || e.held_read) {  // the sealed read first, then what Tilia said after theirs
        add(t, Flourish.svg('mark'));
        add(t, el('h4', 'label', e.read ? "Tilia's read, sealed before you looked"
                                        : "Tilia's read, written before you looked"));
        add(t, el('p', 'read', e.read || e.held_read));
        if (e.reply) add(t, el('h4', 'label', 'Then Tilia said'));
      } else {
        add(t, el('h4', 'label', 'Tilia'));
      }
      if (e.reply) add(t, el('p', 'reply', e.reply));
      if (e.happened) {
        add(t, el('h4', 'label', 'What happened'));
        add(t, el('p', 'after', e.happened));
      }
      var facts = add(t, el('dl', 'facts'));
      add(facts, el('dt', null, 'When'));
      add(facts, el('dd', null, e.when));
      if (f) {
        add(facts, el('dt', null, 'Feeling'));
        add(facts, el('dd', null, f + ' (0 to 10)'));
      }
      if (e.themes.length) {
        add(facts, el('dt', null, 'Themes'));
        add(facts, el('dd', null, e.themes.join(', ')));
      }
      later.forEach(function (fn) { fn(); });
    });
  });

  add(root, el('footer', null, 'This page loads nothing from the internet and sends nothing anywhere. ' +
    '/forget in Telegram removes entries here too.'));

  (function next() {  // draw the blot thumbnails one at a time, so the page stays responsive
    var fn = thumbs.shift();
    if (!fn) return;
    fn();
    setTimeout(next, 0);
  })();
})();
"""
