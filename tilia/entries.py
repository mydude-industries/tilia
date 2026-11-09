"""Worksheet sheets (JSON, one per type), the payload check for what the page sends back,
and the compact one-line form of an entry. Pure functions; no I/O except reading sheets."""

import json
import re
import secrets
from datetime import timedelta

from . import paths

SPREADS = {
    "spread_three": ["What's behind you", "Where you are", "What's next"],
    "spread_cross": ["The heart of it", "What crosses it", "The root", "What's being asked of you",
                     "Where this is heading if nothing changes"],
    "spread_relationship": ["You", "Them", "The space between"],
}
TYPES = ("check_story", "ride_wave", "plan_talk", "urge_log", "week_back", "tarot", "blot",
         *SPREADS)
CARD_TYPES = ("tarot", "blot", *SPREADS)
TAROT_TYPES = ("tarot", *SPREADS)  # entries with drawn cards and a sealed read
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
_ID = re.compile(r"[A-Za-z0-9_-]{12}")
_CARD_ID = re.compile(r"[a-z0-9_]+")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_sheet(type_: str, sheet) -> None:
    def bad(msg):
        raise ValueError(f"sheet {type_}: {msg}")

    if not isinstance(sheet, dict) or sheet.get("type") != type_ or sheet.get("v") != 1:
        bad("needs type matching its file name and v 1")
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


def load_sheet(type_: str) -> dict:
    if type_ not in TYPES:
        raise ValueError(f"unknown sheet type {type_!r}")
    try:
        sheet = json.loads((SHEET_DIR / f"{type_}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"sheet {type_} cannot be read: {e}") from e
    _check_sheet(type_, sheet)
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
    if not isinstance(tid, str) or not _ID.fullmatch(tid) or not isinstance(obj.get("answers"), list):
        return None
    return obj


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


def validate_answers(sheet: dict, answers) -> list | None:
    cards = answer_cards(sheet)
    if not isinstance(answers, list) or len(answers) != len(cards):
        return None
    return [_one(c, a) for c, a in zip(cards, answers)]


def moods(sheet: dict, answers) -> tuple[int | None, int | None]:
    vals = [a for c, a in zip(answer_cards(sheet), answers) if c["kind"] == "mood"]
    if not vals:
        return None, None
    ok = lambda x: x if _int(x) else None  # noqa: E731
    return ok(vals[0]), ok(vals[-1])


def compact(sheet: dict, answers) -> str:
    parts = []
    for c, a in zip(answer_cards(sheet), answers):
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


def clean_wording(sheet: dict, raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    pitch = _line(raw.get("pitch"))
    if pitch:
        out["pitch"] = pitch
    prompts = raw.get("prompts")
    if isinstance(prompts, dict):
        personal = {c["id"] for c in sheet["cards"] if c.get("personal")}
        kept = {k: v for k, v in ((k, _line(v)) for k, v in prompts.items())
                if k in personal and v}
        if kept:
            out["prompts"] = kept
    return out or None


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


def card_ids(card: str | None) -> list[str]:
    """The drawn ids an entry holds: one for a single card, joined with `.` for a spread."""
    return card.split(".") if card else []
