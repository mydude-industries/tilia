"""Worksheet sheets (JSON, one per type), the payload check for what the page sends back,
and the compact one-line form of an entry. Pure functions; no I/O except reading sheets."""

import json
import re
import secrets
from datetime import datetime, timedelta

from . import paths

SPREADS = {
    "spread_three": ["What's behind you", "Where you are", "What's next"],
    "spread_cross": ["The heart of it", "What crosses it", "The root", "What's being asked of you",
                     "Where this is heading if nothing changes"],
    "spread_relationship": ["You", "Them", "The space between"],
}
TYPES = ("check_story", "ride_wave", "plan_talk", "urge_log", "week_back", "tarot", "blot",
         *SPREADS, "candle")
# Rituals have a page of their own (webapp/candle.html), not a sheet of cards. Their sheet
# names the saved fields, as text cards, so the mirror and the digest show them like any
# entry's answers. Never cards, never assignments, never in the offer rotation.
RITUALS = ("candle",)
RITUAL_PAGES = {"candle": "candle.html"}
CANDLE_INTENTION_MAX = 120  # the page keeps it to 60; a longer one is cut, not refused
CANDLE_ANSWER_MAX = 200     # the page's own-words field (webapp/candle-follow.js)
CANDLE_SPAN = timedelta(days=8)  # lit to blown out: an entry lives 7 days
CARD_TYPES = ("tarot", "blot", *SPREADS)
TAROT_TYPES = ("tarot", *SPREADS)  # entries with drawn cards and a sealed read
# How a proactive offer is delivered (rituals phase 1, D2): `gift` the card's picture in the
# chat, `pick` one of three face-down cards, `draw` Tilia's own card and short read, `quick`
# a one-question blot, `ritual` the full sheet. Gift and draw entries are complete when sent.
STYLES = ("gift", "pick", "draw", "quick", "ritual")
PAGE_MODES = ("pick", "quick")  # styles the page itself shows differently (`mode=` on the URL)
PAGE_MODE_TYPES = {"pick": "tarot", "quick": "blot"}  # the one sheet type each mode is for
MODE_TITLES = {"pick": "Pick one", "quick": "Quick blot"}
QUICK_PROMPT = "What's the first thing you see?"  # webapp/core.js QUICK_PROMPT
QUICK_MAX = 300  # the blot sheet's "what do you see first" card
THEMES = ("conflict", "closeness", "loss", "growth", "fear", "hope", "shame", "anger", "rest",
          "self-worth")
PAYLOAD_MAX = 4096
WORDING_MAX = 200
TTL = timedelta(days=7)

SHEET_DIR = paths.REPO_ROOT / "webapp" / "worksheets"
KINDS = ("pitch", "mood", "chips", "text", "slider", "reveal")
MOOD_PROMPT = "How do you feel right now?"
WHY_MAX = 140
TEXT_MAX = 600
TOTAL_TEXT_MAX = 1800
COMPACT_MAX = 1500
# R24: every text card offers 3-5 taps. "answer" taps are likely answers (tapping one is an
# answer, still editable); "starter" taps begin their own sentence and end with "…".
OPTION_MODES = ("answer", "starter")
OPTION_MAX = 80
OPTIONS_MIN, OPTIONS_MAX = 3, 5
ELLIPSIS = "…"
STARTER_WORDS = 4  # R25: a starter is the first few words only, never what happened
WORDING_BYTES = 1800  # the personalized wording as compact UTF-8 JSON, before base64
# D15: a drawn card's own answer taps (written by the sealed-read call) are 4, and one of
# them is always this plain way out.
NOT_LANDING = "It doesn't land for me"
CARD_OPTIONS = 4
FREE_FORM = ("blot",)  # D15: sheets whose text cards take no taps at all
_ID = re.compile(r"[A-Za-z0-9_-]{12}")
_CARD_ID = re.compile(r"[a-z0-9_]+")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_sheet(type_: str, sheet) -> None:
    def bad(msg):
        raise ValueError(f"sheet {type_}: {msg}")

    if (not isinstance(sheet, dict) or sheet.get("type") != type_ or not _int(sheet.get("v"))
            or sheet["v"] < 1):
        bad("needs type matching its file name and a version v of 1 or more")
    for k in ("title", "pitch"):
        if not isinstance(sheet.get(k), str) or not sheet[k].strip():
            bad(f"needs a {k}")
    cards = sheet.get("cards")
    if not isinstance(cards, list) or len(cards) < 3:
        bad("needs cards")
    seen, total = set(), 0
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            bad(f"card {i} is not an object")
        cid, kind = c.get("id"), c.get("kind")
        if not isinstance(cid, str) or not _CARD_ID.fullmatch(cid) or cid in seen:
            bad(f"card {i} needs a unique id like a_b")
        seen.add(cid)
        if kind not in KINDS:
            bad(f"card {cid} has unknown kind")
        if not isinstance(c.get("personal"), bool):
            bad(f"card {cid} needs personal true/false")
        for k in ("short", "prompt"):
            if not isinstance(c.get(k), str) or not c[k].strip():
                bad(f"card {cid} needs {k}")
        if (kind == "pitch") != (i == 0):
            bad("only card 1 is the pitch, and card 1 must be")
        if kind != "pitch":
            why = c.get("why")
            if (not isinstance(why, str) or not why.strip() or len(why) > WHY_MAX
                    or sum(why.count(ch) for ch in ".?!") != 1 or why.strip()[-1] not in ".?!"):
                bad(f"card {cid} needs a why of one sentence, up to {WHY_MAX} characters")
        if kind == "chips":
            chips = c.get("chips")
            if (not isinstance(chips, list) or not 2 <= len(chips) <= 8
                    or not all(isinstance(x, str) and x.strip() for x in chips)
                    or not isinstance(c.get("multi"), bool)):
                bad(f"card {cid} needs 2-8 chips and multi")
        elif kind == "text":
            m = c.get("max_chars")
            if not _int(m) or not 1 <= m <= TEXT_MAX:
                bad(f"card {cid} needs max_chars up to {TEXT_MAX}")
            total += m
            mode = c.get("option_mode")
            if type_ in FREE_FORM:  # D15: no taps at all, only their own words
                if "options" in c or "option_mode" in c:
                    bad(f"card {cid}: a {type_} card is free form, with no options")
                continue
            opts = _options(c.get("options"), mode)
            if mode not in OPTION_MODES or opts is None or opts != c["options"]:
                bad(f"card {cid} needs {OPTIONS_MIN}-{OPTIONS_MAX} one-line options of up to "
                    f"{OPTION_MAX} characters and an option_mode; starters end with {ELLIPSIS}")
        elif "options" in c or "option_mode" in c:
            bad(f"card {cid}: only text cards take options")
        elif kind == "slider":
            lab = c.get("labels")
            if (not _int(c.get("min")) or not _int(c.get("max")) or c["min"] >= c["max"]
                    or not isinstance(lab, list) or len(lab) != 2
                    or not all(isinstance(x, str) for x in lab)):
                bad(f"card {cid} needs min < max and two labels")
        elif kind == "mood":
            if c.get("prompt") != MOOD_PROMPT or c.get("min") != 0 or c.get("max") != 10:
                bad(f"mood card {cid} is fixed: 0-10 and its set prompt")
    if cards[1]["kind"] != "mood" or cards[-1]["kind"] != "mood":
        bad("card 2 and the last card must be mood")
    if total > TOTAL_TEXT_MAX:
        bad(f"max_chars add up to more than {TOTAL_TEXT_MAX}")


def _check_ritual(type_: str, sheet) -> None:
    """A ritual's sheet: type, version, title and pitch, and its saved fields as text cards
    (id, short, prompt, max_chars), never personal."""
    def bad(msg):
        raise ValueError(f"sheet {type_}: {msg}")

    if (not isinstance(sheet, dict) or sheet.get("type") != type_ or not _int(sheet.get("v"))
            or sheet["v"] < 1):
        bad("needs type matching its file name and a version v of 1 or more")
    for k in ("title", "pitch"):
        if not isinstance(sheet.get(k), str) or not sheet[k].strip():
            bad(f"needs a {k}")
    cards = sheet.get("cards")
    if not isinstance(cards, list) or not cards:
        bad("needs its fields as cards")
    seen = set()
    for c in cards:
        if (not isinstance(c, dict) or c.get("kind") != "text" or c.get("personal") is not False
                or not isinstance(c.get("id"), str) or not _CARD_ID.fullmatch(c["id"])
                or c["id"] in seen or not _int(c.get("max_chars"))
                or not all(isinstance(c.get(k), str) and c[k].strip()
                           for k in ("short", "prompt"))):
            bad("each field is a text card with a unique id, short, prompt and max_chars")
        seen.add(c["id"])


def load_sheet(type_: str) -> dict:
    if type_ not in TYPES:
        raise ValueError(f"unknown sheet type {type_!r}")
    try:
        sheet = json.loads((SHEET_DIR / f"{type_}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"sheet {type_} cannot be read: {e}") from e
    (_check_ritual if type_ in RITUALS else _check_sheet)(type_, sheet)
    return sheet


def available_types() -> list[str]:
    return [t for t in TYPES if (SHEET_DIR / f"{t}.json").is_file()]


def answer_cards(sheet: dict) -> list[dict]:
    return [c for c in sheet["cards"] if c["kind"] != "pitch"]


def parse_payload(data: str) -> dict | None:
    if not isinstance(data, str) or len(data.encode("utf-8", "replace")) > PAYLOAD_MAX:
        return None
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    if (not isinstance(obj, dict) or obj.get("v") != 1 or isinstance(obj.get("v"), bool)
            or obj.get("type") not in TYPES):
        return None
    tid = obj.get("id")
    if not isinstance(tid, str) or not _ID.fullmatch(tid):
        return None
    if obj["type"] in RITUALS:  # the candle: its own fields, never a mode or answers
        return obj if candle_answers(obj) is not None else None
    if "mode" in obj:  # pick one / quick blot: its own fields, checked by mode_answers
        return obj if obj["mode"] in PAGE_MODES else None
    return obj if isinstance(obj.get("answers"), list) else None


def is_mode(entry) -> bool:
    """A pick-one or quick-blot entry: answered by one choice or one line, not the sheet."""
    return PAGE_MODE_TYPES.get(entry.style) == entry.type


def mode_answers(mode: str, obj: dict) -> tuple[str | None, list] | None:
    """What a pick-one or quick-blot payload keeps: (the chosen card, answers). Pick one
    sends a card the deck has; quick blot one line of words, trimmed and cut to QUICK_MAX.
    None when it doesn't hold that."""
    if mode == "pick":
        cid = obj.get("card")
        if not isinstance(cid, str) or card_info(cid) is None:
            return None
        return cid, []
    answer = obj.get("answer")
    if mode != "quick" or not isinstance(answer, str):
        return None
    return None, [answer.strip()[:QUICK_MAX]]


def _ms_local(ms) -> datetime | None:
    if not _int(ms) or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def candle_answers(obj: dict) -> list[str] | None:
    """What a blown-out candle keeps, in the candle sheet's field order: [intention (may be
    empty), lit at, out at (local "YYYY-MM-DD HH:MM"), what they told Tilia (may be empty:
    the field is optional; an old page sent one of its taps)]. The page sends the times as
    epoch milliseconds. Its `now` and `later` are never read: Tilia's lines are the ones
    this Mac saved on the entry. None when it doesn't hold that: no answer string, a time
    that isn't one, blown out before it was lit, a mode, or a worksheet's answers."""
    if "mode" in obj or "answers" in obj:
        return None
    intention, answer = obj.get("intention", ""), obj.get("answer")
    if intention is None:
        intention = ""
    if not isinstance(intention, str) or not isinstance(answer, str):
        return None
    lit, out = _ms_local(obj.get("lit_at")), _ms_local(obj.get("out_at"))
    if lit is None or out is None or out < lit or out - lit > CANDLE_SPAN:
        return None
    one = lambda s, n: " ".join(s.split())[:n]  # noqa: E731
    return [one(intention, CANDLE_INTENTION_MAX), f"{lit:%Y-%m-%d %H:%M}",
            f"{out:%Y-%m-%d %H:%M}", one(answer, CANDLE_ANSWER_MAX)]


STYLE_LABELS = {"gift": "a gift", "pick": "you picked", "draw": "Tilia's draw",
                "quick": "quick look"}  # ritual and old (NULL) entries say nothing


SEEN_NOW, SEEN_LATER = 1, 2  # entries.voice_seen bits: the lines the page says it spelled


def candle_seen(obj: dict, entry) -> int:
    """Which of Tilia's saved lines the page spelled: a bit for each line whose text the
    payload echoes exactly. The text itself always comes from the entry; the page only
    tells us whether the smoke showed it (a late answer, after the page gave up, never was)."""
    seen = 0
    if entry.voice_now and obj.get("now") == entry.voice_now:
        seen |= SEEN_NOW
    if entry.voice_later and obj.get("later") == entry.voice_later:
        seen |= SEEN_LATER
    return seen


CANDLE_TAPS = ("Better than I feared.", "About what I expected.", "Harder than I hoped.",
               "It didn't happen.")  # an older page's answers to "How did it go?"


def candle_saved(entry) -> tuple[str, datetime, datetime, str] | None:
    """A candle entry's (intention, lit, out, answer) as saved by candle_answers, or None."""
    a = entry.answers
    if entry.type != "candle" or not isinstance(a, list) or len(a) != 4 \
            or not all(isinstance(x, str) for x in a):
        return None
    try:
        lit = datetime.strptime(a[1], "%Y-%m-%d %H:%M")
        out = datetime.strptime(a[2], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return a[0], lit, out, a[3]


def burned_text(lit: datetime, out: datetime) -> str:
    """How long it burned, as "2 h 5 min" / "30 min" / "2 h"."""
    mins = max(0, int((out - lit).total_seconds() // 60))
    h, m = divmod(mins, 60)
    return " ".join(([f"{h} h"] if h else []) + ([f"{m} min"] if m or not h else []))


def compact_entry(sheet: dict, entry) -> str:
    """An entry's answers in short form, as a user turn: a pick-one names the card they
    chose, a quick blot what they saw first, a candle what it was for and what they said;
    anything else is compact()."""
    if entry.type == "candle":
        a = entry.answers if isinstance(entry.answers, list) and len(entry.answers) == 4 \
            else ["", "", "", ""]
        parts = ([f"for: {a[0]}"] if a[0] else []) + [f"said: {a[3]}" if a[3] else "said nothing"]
        return "[candle] " + "; ".join(parts)
    if not is_mode(entry):
        return compact(sheet, entry.answers)
    if entry.style == "pick":
        name = (card_info(entry.card or "") or {}).get("name") or entry.card or "a card"
        return f"[pick one] chose {name}"
    said = next((a for a in entry.answers or [] if isinstance(a, str) and a), "(nothing)")
    return f"[quick blot] first thing I saw: {said}"


def _one(card: dict, a):
    kind = card["kind"]
    if kind == "text":
        return a.strip()[:card["max_chars"]] if isinstance(a, str) else ""
    if kind == "chips":
        if not isinstance(a, list):
            return []
        out = []
        for x in a:
            if isinstance(x, str) and x in card["chips"] and x not in out:
                out.append(x)
        return out[:8] if card["multi"] else out[:1]
    if kind in ("mood", "slider"):
        return a if _int(a) and card["min"] <= a <= card["max"] else None
    return None


def fits(sheet: dict, answers) -> bool:
    """True when stored answers pair with this sheet's cards, one for one and kind for kind
    (as validate_answers leaves them). An entry answered against an older version of a sheet
    may not: its words are then shown on their own, never under the wrong question."""
    cards = answer_cards(sheet)
    if not isinstance(answers, list) or len(answers) != len(cards):
        return False
    for c, a in zip(cards, answers):
        kind = c["kind"]
        ok = (isinstance(a, str) if kind == "text" else isinstance(a, list) if kind == "chips"
              else a is None or _int(a) if kind in ("mood", "slider") else a is None)
        if not ok:
            return False
    return True


def validate_answers(sheet: dict, answers) -> list | None:
    cards = answer_cards(sheet)
    if not isinstance(answers, list) or len(answers) != len(cards):
        return None
    return [_one(c, a) for c, a in zip(cards, answers)]


def moods(sheet: dict, answers) -> tuple[int | None, int | None]:
    if not fits(sheet, answers):  # positions can't be trusted: see unpaired_moods
        return None, None
    vals = [a for c, a in zip(answer_cards(sheet), answers) if c["kind"] == "mood"]
    if not vals:
        return None, None
    ok = lambda x: x if _int(x) else None  # noqa: E731
    return ok(vals[0]), ok(vals[-1])


def compact(sheet: dict, answers) -> str:
    paired = fits(sheet, answers)
    parts = [] if paired else [  # answered against an older sheet: the words alone
        ", ".join(map(str, a)) if isinstance(a, list) else a
        for a in answers or [] if isinstance(a, (str, list)) and a]
    for c, a in zip(answer_cards(sheet), answers) if paired else ():
        if c["kind"] in ("mood", "reveal") or a in (None, "", []):
            continue
        parts.append(f"{c['short']}: {', '.join(a) if isinstance(a, list) else a}")
    before, after = moods(sheet, answers)
    if before is not None or after is not None:
        f = lambda x: "?" if x is None else x  # noqa: E731
        parts.append(f"feeling {f(before)}→{f(after)}")
    return f"[{sheet['title'].lower()}] " + "; ".join(parts)[:COMPACT_MAX - len(sheet["title"]) - 3]


def _line(s) -> str | None:
    if (isinstance(s, str) and 1 <= len(s.strip()) <= WORDING_MAX
            and not any(ch in s for ch in "\n\r  \x0b\x0c\x85")):
        return s.strip()
    return None


def _options(raw, mode) -> list | None:
    """3-5 distinct one-line options of up to OPTION_MAX characters; for "starter", each
    ends with an ellipsis ("..." becomes "…"). None if any one fails."""
    if not isinstance(raw, list) or not OPTIONS_MIN <= len(raw) <= OPTIONS_MAX:
        return None
    out = []
    for o in raw:
        o = _line(o)
        if o is None or len(o) > OPTION_MAX or o in out:
            return None
        if mode == "starter":
            if o.endswith("..."):
                o = o[:-3].rstrip() + ELLIPSIS
            if (not o.endswith(ELLIPSIS) or len(o) < 2
                    or len(o[:-1].split()) > STARTER_WORDS):
                return None
        out.append(o)
    return out


def _size(wording: dict) -> int:
    return len(json.dumps(wording, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def card_answer_ids(sheet: dict) -> list[str]:
    """D15: the "what does this one say" cards of a tarot entry or a spread: the text card
    right after each drawn card. Their taps are written for the card by the sealed-read
    call (clean_card_options), never by personalize."""
    if sheet.get("type") not in TAROT_TYPES:
        return []
    cards = sheet["cards"]
    return [b["id"] for a, b in zip(cards, cards[1:])
            if a["kind"] == "reveal" and b["kind"] == "text" and b.get("option_mode") == "answer"]


def _plain(s: str) -> str:
    return s.strip().rstrip(".!").replace("\u2019", "'").lower()


def clean_card_options(sheet: dict, raw) -> dict | None:
    """D15: the sealed-read call's taps for each drawn card, kept only when they are safe
    to show: per card, CARD_OPTIONS distinct one-line taps of up to OPTION_MAX characters,
    the last always NOT_LANDING (added if missing; extras are cut). A card whose taps fail
    keeps the sheet's generic ones. None if nothing is kept."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for cid in card_answer_ids(sheet):
        opts = raw.get(cid)
        if not isinstance(opts, list):
            continue
        own = [o for o in opts if not (isinstance(o, str) and _plain(o) == _plain(NOT_LANDING))]
        own = _options(own[:CARD_OPTIONS - 1], "answer") if len(own) >= CARD_OPTIONS - 1 \
            else None
        if own is not None:
            out[cid] = own + [NOT_LANDING]
    return out or None


def clean_wording(sheet: dict, raw, card_options: bool = False) -> dict | None:
    """Tilia's wording for one entry, kept only where it is safe to show: the pitch, the
    prompts of personal cards, and tap options for personal text cards (starters only on
    starter cards). With card_options (the sealed-read call, D15), the drawn cards' own
    answer taps too. Options go first, last card first, if the whole is over WORDING_BYTES."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    pitch = _line(raw.get("pitch"))
    if pitch:
        out["pitch"] = pitch
    personal = {c["id"]: c for c in sheet["cards"] if c.get("personal")}
    prompts = raw.get("prompts")
    if isinstance(prompts, dict):
        kept = {k: v for k, v in ((k, _line(v)) for k, v in prompts.items())
                if k in personal and v}
        if kept:
            out["prompts"] = kept
    options = raw.get("options")
    if isinstance(options, dict):
        kept = {}
        tailored = set(personal) | (set(card_answer_ids(sheet)) if card_options else set())
        for c in sheet["cards"]:  # in card order, so the size cut drops the last ones
            # R25: only answer cards are tailored; a starter card's starters stay generic
            if (c["id"] in tailored and c.get("kind") == "text"
                    and c.get("option_mode") == "answer" and c["id"] in options):
                o = _options(options[c["id"]], "answer")
                if o:
                    kept[c["id"]] = o
        if kept:
            out["options"] = kept
    # The whole wording fits WORDING_BYTES: taps go first, then prompts, last card first.
    for key in ("options", "prompts"):
        part = out.get(key)
        if part is None:
            continue
        ordered = [c["id"] for c in sheet["cards"] if c["id"] in part]
        while ordered and _size(out) > WORDING_BYTES:
            part.pop(ordered.pop())
        if not part:
            del out[key]
    return out or None


def unpaired(raw) -> list | None:
    """R26: answers that don't pair with the current sheet (a page opened before the sheet
    changed), kept as they came but bounded: strings trimmed and cut to TEXT_MAX, lists of
    up to 8 such strings, whole numbers; anything else becomes None. None if not a list."""
    if not isinstance(raw, list):
        return None

    def one(a):
        if isinstance(a, str):
            return a.strip()[:TEXT_MAX]
        if isinstance(a, list):
            return [x.strip()[:TEXT_MAX] for x in a if isinstance(x, str) and x.strip()][:8]
        return a if _int(a) else None
    return [one(a) for a in raw]


def unpaired_moods(answers: list) -> tuple[int | None, int | None]:
    """Every sheet version opens and closes with a 0-10 feeling card, so the first and
    last answers are the feelings even when nothing else pairs."""
    ok = lambda x: x if _int(x) and 0 <= x <= 10 else None  # noqa: E731
    return (ok(answers[0]), ok(answers[-1])) if answers else (None, None)


def new_token() -> str:
    return secrets.token_urlsafe(9)


def new_seed() -> int:
    return secrets.randbelow(2**31 - 1) + 1


# ---- blots: the palette, as webapp/blot.js picks it (generator 1) ------------------

_M32 = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    return (a * b) & _M32


def _mix32(x: int) -> int:
    x &= _M32
    x ^= x >> 16
    x = _imul(x, 0x7FEB352D)
    x ^= x >> 15
    x = _imul(x, 0x846CA68B)
    return x ^ (x >> 16)


def _mulberry32_first(seed: int) -> float:
    a = (seed + 0x6D2B79F5) & _M32
    t = _imul(a ^ (a >> 15), 1 | a)
    t = ((t + _imul(t ^ (t >> 7), 61 | t)) & _M32) ^ t
    return ((t ^ (t >> 14)) & _M32) / 4294967296


def blot_palette(seed: int) -> str:
    """"green" (green and gold ink) for about 1 seed in 3, else "black"."""
    return "green" if _mulberry32_first(_mix32(seed ^ 0x7A11A)) < 1 / 3 else "black"


DECK_DIR = paths.REPO_ROOT / "webapp" / "tarot"
CARD_BACK = "card-back"


def available_cards() -> list[dict]:
    """Deck manifest entries whose WebP exists, without the card back."""
    try:
        man = json.loads((DECK_DIR / "manifest.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(man, list):
        return []
    return [e for e in man if isinstance(e, dict) and e.get("id") != CARD_BACK
            and isinstance(e.get("file"), str) and (DECK_DIR / e["file"]).is_file()]


def card_info(card_id: str) -> dict | None:
    return next((e for e in available_cards() if e["id"] == card_id), None)


CARD_LINES = paths.REPO_ROOT / "webapp" / "card-lines.js"


def card_lines() -> dict[str, str]:
    """Tilia's one line per card, as Pick one shows it: the JSON object between the LINES
    markers in the page's own card-lines.js. Empty if it can't be read."""
    try:
        text = CARD_LINES.read_text(encoding="utf-8")
        raw = json.loads(text.split("/* LINES */", 1)[1].split("/* /LINES */", 1)[0])
    except (OSError, IndexError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(raw, dict) else {}


def card_line(card_id: str) -> str | None:
    return card_lines().get(card_id)


def card_ids(card: str | None) -> list[str]:
    """The drawn ids an entry holds: one for a single card, joined with `.` for a spread."""
    return card.split(".") if card else []
