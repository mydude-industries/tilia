"""Worksheet offers: make the entry, personalize its wording (one AI call), build the button.

coach.py imports this module inside a function; this module imports coach as a module."""

import secrets
import subprocess
from datetime import datetime

from . import ai, coach, entries, paths, profile
from .claude import CoachReply, LimitError, MissingCLI, NotLoggedIn, UnsupportedCLI
from .webapp import Offer, make_offer

READ_MAX = 1200
SPREAD_READ_MAX = 2000
_RNG = secrets.SystemRandom()
EXPIRED = "That one had expired, so I didn't keep your answers. Ask me for a fresh one any time."
INVALID = ("That one didn't come through right, so I couldn't read your answers. "
           "Want to tell me here instead?")
_AI_DOWN = (LimitError, NotLoggedIn, MissingCLI, UnsupportedCLI)


def _system(name: str = "worksheet") -> str:
    return (paths.REPO_ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def personalize(cfg, sheet: dict, focus: str, run=subprocess.run) -> dict | None:
    """The sheet's pitch and personal prompts, reworded for this person's situation, and
    tailored answer taps for its personal reflection cards (R24, R25: a card's sentence
    starters are never tailored). None when the call fails, the AI is unreachable (limit,
    login, missing CLI), or its answer doesn't pass clean_wording: generic wording is used
    then. The coach's reply has already succeeded by the time this runs, so nothing here may
    undo it."""
    def line(c):
        out = f"{c['id']}: {c['prompt']} (why: {c['why']})"
        if c["kind"] == "text" and c["option_mode"] == "answer":  # R25: never starters
            out += f" [answers: {' | '.join(c['options'])}]"
        return out
    cards = [line(c) for c in sheet["cards"] if c.get("personal")]
    prompt = "\n\n".join([
        coach.DATA_NOTE,
        "PROFILE (your working notes on this person)\n"
        + coach._tag("profile", profile.read(), "(empty)"),
        "FOCUS (the real situation this worksheet is about)\n" + coach._tag("focus", focus),
        f"WORKSHEET: {sheet['title']}\nPitch:\n" + coach._tag("pitch", sheet["pitch"]),
        "PROMPTS TO REWRITE (id: prompt (why: what the card is for) [its generic taps])\n"
        + coach._tag("cards", "\n".join(cards)),
        "Reply with the JSON object only.",
    ])
    try:
        raw = ai.ask_json(cfg, prompt, _system(), run=run)
    except (RuntimeError, ValueError, *_AI_DOWN):
        return None
    return entries.clean_wording(sheet, raw)


# ---- tarot: the draw and the sealed read ------------------------------------------

def draw(rng=_RNG) -> dict | None:
    """One card from the deck, uniformly, upright only (never the back). None: no deck."""
    cards = entries.available_cards()
    return rng.choice(cards) if cards else None


def draw_many(n: int, rng=_RNG) -> list[dict] | None:
    """n distinct cards from the deck, upright only (never the back). None: too few cards."""
    cards = entries.available_cards()
    return rng.sample(cards, n) if n <= len(cards) else None


def _card_text(info: dict) -> str:
    return f"{info['name']} ({info.get('number') or 'unnumbered'})\nScene: {info.get('scene', '')}"


_SUITS = ("wands", "cups", "swords", "pentacles")


def _spread_facts(ids: list[str]) -> str:
    """R20: what the spread holds, counted here so the read never miscounts: cards per
    suit, how many majors, and any rank two or more minor cards share. Deck ids carry it:
    `cups-03-three` is a minor (suit, rank), `00-the-fool` a major."""
    suits = dict.fromkeys(_SUITS, 0)
    ranks: dict[str, int] = {}
    majors = 0
    for cid in ids:
        suit, _, rest = cid.partition("-")
        if suit in suits:
            suits[suit] += 1
            rank = rest.partition("-")[2].capitalize()
            ranks[rank] = ranks.get(rank, 0) + 1
        else:
            majors += 1
    repeats = [f"{r} ({n} cards)" for r, n in ranks.items() if n > 1]
    absent = [s for s, n in suits.items() if not n]
    return "\n".join([
        "cards per suit: " + ", ".join(f"{s} {n}" for s, n in suits.items()),
        "no cards from: " + (", ".join(absent) or "none, every suit is here"),
        f"major arcana: {majors} of {len(ids)}",
        "repeated ranks: " + (", ".join(repeats) or "none"),
    ])


def _cards_section(entry) -> tuple[str, str, int] | None:
    """The drawn cards as the sealed-read prompt shows them, its system prompt and its
    length limit. None when a card isn't in the deck (or the spread doesn't add up)."""
    infos = [entries.card_info(i) for i in entries.card_ids(entry.card)]
    if not infos or None in infos:
        return None
    if entry.type in entries.SPREADS:
        positions = entries.SPREADS[entry.type]
        if len(positions) != len(infos):
            return None
        rows = [f"{n}. {pos}: {_card_text(info)}"
                for n, (pos, info) in enumerate(zip(positions, infos), 1)]
        return ("SPREAD (drawn for them, position by position; they haven't looked yet)\n"
                + coach._tag("spread", "\n\n".join(rows))
                + "\n\nFACTS (counted for you: use these counts, don't count again)\n"
                + coach._tag("facts", _spread_facts(entries.card_ids(entry.card))),
                "spread_read", SPREAD_READ_MAX)
    if len(infos) != 1:
        return None
    return ("CARD (drawn for them; they haven't looked yet)\n"
            + coach._tag("card", _card_text(infos[0])), "tarot_read", READ_MAX)


def seal(store, cfg, now, entry, run=subprocess.run) -> None:
    """Write Tilia's own read of the entry's card or spread before they look, and store it
    once (D5, D13). An entry that already has a read keeps it: no call is made. A failed
    call, the AI being down (limit, login, missing CLI) or unreadable output leaves it NULL,
    and the cards still go out: the coach says so plainly when it answers. As with
    personalize, the coach's reply has already succeeded, so nothing here may undo it."""
    entry = store.get_entry(entry.id)
    section = _cards_section(entry)
    if entry.sealed_read is not None or section is None:
        return
    cards, system, limit = section
    prompt = "\n\n".join([
        coach.DATA_NOTE,
        cards,
        "PROFILE (your working notes on this person)\n"
        + coach._tag("profile", profile.read(), "(empty)"),
        coach._conversation(store),
        "Reply with the JSON object only.",
    ])
    try:
        raw = ai.ask_json(cfg, prompt, _system(system), run=run)
    except (RuntimeError, ValueError, *_AI_DOWN):
        return
    read = raw.get("read") if isinstance(raw, dict) else None
    if isinstance(read, str) and read.strip() and len(read.strip()) <= limit:
        store.set_sealed_read(entry.id, read.strip())


def _draw_for(type: str) -> tuple[str, list[dict] | None]:
    """The type that can really be dealt, and its cards: a spread the deck can't fill
    becomes a single card, and a single card with no deck becomes a blot."""
    if type in entries.SPREADS:
        cards = draw_many(len(entries.SPREADS[type]))
        if cards is not None:
            return type, cards
        type = "tarot"
    if type == "tarot":
        card = draw()
        return ("tarot", [card]) if card else ("blot", None)
    return type, None


def prepare(store, cfg, now, type: str, *, focus=None, source="ask", assignment_id=None,
            run=subprocess.run) -> Offer | None:
    """Create an open entry for this worksheet and return its button. None if the sheet
    can't be read. A blot gets a fresh seed and no wording. A tarot entry or a spread gets
    its drawn cards (ids joined with `.`) and its sealed read before the button exists; a
    spread the deck can't fill becomes one card, and with no deck at all a blot."""
    type, cards = _draw_for(type)
    try:
        sheet = entries.load_sheet(type)
    except ValueError:
        return None
    blot = type == "blot"
    seed = entries.new_seed() if blot else None
    cid = ".".join(c["id"] for c in cards) if cards else None
    wording = personalize(cfg, sheet, focus, run) if focus and not blot else None
    token = entries.new_token()
    eid = store.add_entry(type, token, source, wording=wording, card=cid, seed=seed,
                          assignment_id=assignment_id, turn_id=store.last_turn_id())
    if cid:
        seal(store, cfg, now, store.get_entry(eid), run)
    return make_offer(sheet, token, card=cid, seed=seed, wording=wording)


def offer_for(store, entry) -> Offer:
    """The button for an entry that already exists, from its stored wording, card and seed."""
    return make_offer(entries.load_sheet(entry.type), entry.token, card=entry.card,
                      seed=entry.seed, wording=entry.wording)


def again(store) -> Offer | None:
    """The newest open entry's button, or None if nothing is open."""
    for e in reversed(store.entries(statuses=("open",))):
        try:
            return offer_for(store, e)
        except ValueError:  # its sheet is gone; try the next one
            continue
    return None


# ---- answers coming back ---------------------------------------------------------

def _ignored(store) -> None:
    n = int(store.get_meta("ignored_payloads") or 0) + 1
    store.set_meta("ignored_payloads", str(n))


def expire(store, now) -> int:
    """Open entries older than entries.TTL become expired (their sealed read goes too)."""
    return store.expire_entries(now, entries.TTL)


def accept(store, now, data: str) -> str:
    """Match a web_app_data payload to its entry and keep the answers.
    Returns "ok" | "unknown" | "invalid" | "expired" | "answered"."""
    obj = entries.parse_payload(data)
    e = store.entry_by_token(obj["id"]) if obj else None
    if e is None:
        _ignored(store)
        return "invalid" if obj is None else "unknown"
    try:
        sheet = entries.load_sheet(e.type)
    except ValueError:
        return "invalid"
    if e.type != obj["type"]:
        return "invalid"
    # R26: answers that don't pair with this sheet came from a page opened before it
    # changed. Keep their words, bounded and unpaired, rather than lose them.
    paired = entries.fits(sheet, obj["answers"])
    answers = (entries.validate_answers(sheet, obj["answers"]) if paired
               else entries.unpaired(obj["answers"]))
    if e.status == "open" and datetime.fromisoformat(e.created) < now.astimezone() - entries.TTL:
        expire(store, now)  # after a long sleep the payload can beat the slow pass
        e = store.get_entry(e.id)
    if e.status == "expired":
        return "expired"
    if e.status in ("answered", "done"):
        return "answered"
    store.answer_entry(e.id, answers, *(entries.moods(sheet, answers) if paired
                                        else entries.unpaired_moods(answers)))
    return "ok"


def reply_due(store) -> list:
    """Entries with answers in and no reply yet, oldest answer first."""
    return sorted(store.entries(statuses=("answered",)), key=lambda e: (e.answered_at or "", e.id))


def answer(store, cfg, now, entry, run=subprocess.run) -> CoachReply:
    """The coach's reply to a filled-in worksheet: its answers go in as one user turn.
    The caller wraps this (and queueing the reply) in store.atomic()."""
    sheet = entries.load_sheet(entry.type)
    r = coach.respond(store, cfg, now, [entries.compact(sheet, entry.answers)], run=run,
                      mode="entry", entry=entry)
    store.finish_entry(entry.id, r.reply, r.themes, store.last_turn_id("user"), r.intent_signal)
    if entry.assignment_id is not None and entry.assignment_id in {
            a.id for a in store.open_assignments()}:
        store.close_assignment(entry.assignment_id, "done")
    r.remove_keyboard = True
    return r


GAVE_UP = ("I couldn't put a proper reply together for that one, but your answers are saved "
           "in your mirror. Want to talk it through here?")


def give_up(store, now, entry) -> CoachReply:
    """After repeated failed tries: finish the entry with an honest line instead of a
    reply, so it stops waiting. Its answers stay (the mirror shows them); a sealed read
    stays stored and unshown. The caller wraps this in store.atomic()."""
    try:
        said = entries.compact(entries.load_sheet(entry.type), entry.answers)
    except ValueError:  # its sheet is gone; the answers are still in the entry
        said = f"[{entry.type.replace('_', ' ')}]"
    store.add_turn("user", said)
    store.add_turn("coach", GAVE_UP, "struggling")  # unknown, as when a state can't be read
    store.finish_entry(entry.id, GAVE_UP, [], store.last_turn_id("user"))
    if entry.assignment_id is not None and entry.assignment_id in {
            a.id for a in store.open_assignments()}:
        store.close_assignment(entry.assignment_id, "done")
    return CoachReply(GAVE_UP, "struggling", remove_keyboard=True)
