"""Check-ins: specific or silent. Find a real reason to text first, draft one message, and
send it only if a second call judges it not generic."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import ai, coach, entries, outbox, paths, profile, texts, worksheets
from .coach import DATA_NOTE, _hm, _tag

SIGNAL_FRESH = timedelta(hours=24)
INTENT_FRESH = timedelta(days=3)
TALKING = timedelta(minutes=30)
UNREPLIED_HALVES = 3
CHECKINS_IN_PROMPT = 10
USED_KEEP = 300
FOLLOWUP_MIN, FOLLOWUP_MAX = timedelta(hours=12), timedelta(hours=36)
FOLLOWUP_USER_TURNS = 4   # a "substantial" conversation
FOLLOWUP_TURNS = 12       # how much of it the drafter sees
CARE_QUIET = timedelta(hours=36)
CRISIS_RECENT = coach.EPISODE_GAP + timedelta(hours=72)  # episode ended within 72 h
CRISIS_IN_CONVO = "This conversation included a crisis moment; the episode is over."
CRISIS_LATELY = "A conversation in the last few days included a crisis moment; the episode is over."
CONVO_SCAN = 200          # turns searched for the last conversation
CARD_EVERY = timedelta(days=7)  # an unopened card counts too
CARD_AFTER_CRISIS = coach.EPISODE_GAP + timedelta(hours=96)  # episode ended over 96 h ago
CARD_TYPES = entries.CARD_TYPES  # spreads count as cards: one a week, whatever kind
SPREAD_EVERY = 4  # at most every 4th card offer is a spread (D13: keep it light)
CHECKIN_SPREAD = "spread_three"
_STATE, _USED = "checkin_state", "used_signal_ids"
_USED_KEYS = "used_checkin_keys"  # followup / care keys that already fired

# lower is stronger. A user-requested check-in (intent) is as strong as a due assignment.
_PRIO = {"assignment": 0, "intent": 0, "conflict": 1, "repair": 1, "unanswered_question": 1,
         "bid": 3, "pattern_hit": 3, "long_silence": 3, "followup": 3, "anchor": 4, "care": 5,
         "card": 5}

_WHEN = re.compile(r"\b(today|tonight|this (morning|afternoon|evening)|later|at \d{1,2}"
                   r"|\d{1,2}(:\d\d)? ?(am|pm)|\d{1,2}:\d\d)\b", re.I)
_TOMORROW = re.compile(r"\btomorrow\b", re.I)
_EVENT = re.compile(r"\b(meeting|interview|presentation|appointment|dinner|lunch|breakfast|"
                    r"date|call|doctor|dentist|therapy|session|exam|test|flight|party|talk|"
                    r"conversation|review|class|game|deadline|visit|see(ing)?)\b"
                    r"|\b\d{1,2}(:\d\d)? ?(am|pm)\b|\b\d{1,2}:\d\d\b", re.I)
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class Candidate:
    reason: str     # short label, stored in the checkins table (never raw text)
    context: str    # the specifics, used only inside prompts
    key: str = ""   # "a:<assignment id>", "s:<signal id>", "t:<turn id>",
    #                 "f:<last user turn id>" (followup), "c:<last turn id>" (care)
    #                 or "k:<date>" (card)
    prio: int = 9
    at: str = ""


# ---- small state -----------------------------------------------------------

def _state(store, now) -> dict:
    today = now.astimezone().date().isoformat()
    try:
        st = json.loads(store.get_meta(_STATE) or "{}")
    except ValueError:
        st = {}
    if not isinstance(st, dict):
        st = {}
    if st.get("day") != today:
        # "tried" is per day; a deferral can point at a later day, so it carries over
        defer = st.get("defer") if isinstance(st.get("defer"), dict) else {}
        st = {"day": today, "tried": [],
              "defer": {k: v for k, v in defer.items() if _future(v, now)}}
    return st


def _future(iso, now) -> bool:
    try:
        return datetime.fromisoformat(iso) > now
    except (TypeError, ValueError):
        return False


def _save_state(store, st) -> None:
    store.set_meta(_STATE, json.dumps(st))


def _used(store) -> set[int]:
    try:
        return set(json.loads(store.get_meta(_USED) or "[]"))
    except ValueError:
        return set()


def _used_keys(store) -> list[str]:
    try:
        keys = json.loads(store.get_meta(_USED_KEYS) or "[]")
    except ValueError:
        return []
    return keys if isinstance(keys, list) else []


def _available(store, now):
    st = _state(store, now)
    tried, defer = set(st["tried"]), st["defer"]

    def ok(key: str) -> bool:
        if key in tried:
            return False
        until = defer.get(key)
        return not (until and now < datetime.fromisoformat(until))
    return ok


# ---- candidates ------------------------------------------------------------

def _is_due(due: str | None, now) -> bool:
    if not due:
        return False
    try:
        if len(due) == 10:
            return date.fromisoformat(due) <= now.astimezone().date()
        d = datetime.fromisoformat(due)
        return (d if d.tzinfo else d.astimezone()) <= now
    except ValueError:
        return False


def _assignment_cands(store, now) -> list[Candidate]:
    out = []
    for a in store.open_assignments():
        if a.followed_up_at or not _is_due(a.due, now):
            continue
        out.append(Candidate(
            "assignment_due",
            f"Open {a.kind} #{a.id}, given {_hm(a.created)}, due {a.due}: {a.text}\n"
            "It's due and you haven't followed up on it yet.",
            f"a:{a.id}", _PRIO["assignment"], a.created))
    return out


def _question_context(store, cid, name, detail, now) -> str | None:
    """None when the question can't be found or has been answered since."""
    try:
        rowid = int(detail.split(":", 1)[1])
    except (IndexError, ValueError):
        return None
    msgs = store.text_msgs(cid, since=now - timedelta(days=8))
    q = next((m for m in msgs if m["rowid"] == rowid and not m["from_me"]), None)
    if q is None:
        return None
    after = datetime.fromisoformat(q["read_at"] or q["at"])
    if any(m["from_me"] and datetime.fromisoformat(m["at"]) > after for m in msgs):
        return None
    return (f"In the {name} thread, {name} asked something at {_hm(q['at'])}; it was read at "
            f"{_hm(q['read_at']) if q['read_at'] else 'an unknown time'} and there's been no "
            f"reply since. The message, for your understanding only (never quote it):\n"
            f"{q['text']}")


def _signal_cands(store, cfg, now) -> list[Candidate]:
    names = {t["chat_id"]: t["name"] for t in cfg.threads}
    used = _used(store)
    out = []
    for s in store.signals_since(now - max(SIGNAL_FRESH, INTENT_FRESH)):
        if s["id"] in used:
            continue
        age = now - datetime.fromisoformat(s["at"])
        key = f"s:{s['id']}"
        if s["chat_id"] == 0:
            if s["kind"] == "intent" and age <= INTENT_FRESH:
                out.append(Candidate("intent", _intent_context(s, cfg, now), key,
                                     _PRIO["intent"], s["at"]))
            continue
        name = names.get(s["chat_id"])
        if name is None or s["kind"] not in _PRIO or age > SIGNAL_FRESH:
            continue
        if s["kind"] == "unanswered_question":
            ctx = _question_context(store, s["chat_id"], name, s["detail"], now)
            if ctx is None:
                continue
        elif s["kind"] == "long_silence":
            last = store.text_msgs(s["chat_id"], limit=1)
            hours = int((now - datetime.fromisoformat(last[0]["at"])).total_seconds() // 3600) \
                if last else 24
            ctx = (f"The {name} thread usually talks daily and has been silent for about "
                   f"{hours} hours.")
        else:
            ctx = f"In the {name} thread (noticed {_hm(s['at'])}): {s['kind']}: {s['detail']}"
        out.append(Candidate(f"signal:{s['kind']}", ctx, key, _PRIO[s["kind"]], s["at"]))
    return out


def _day_hm(iso_or_dt) -> str:
    d = datetime.fromisoformat(iso_or_dt) if isinstance(iso_or_dt, str) else iso_or_dt
    return d.astimezone().strftime("%Y-%m-%d %H:%M (%A)")


def _intent_context(s, cfg, now) -> str:
    return (f"Noted {_day_hm(s['at'])}: {s['detail']}\n"
            f"It is now {_day_hm(now)}. Either they asked you to check on them, or they told "
            "you they'd do this; either way they want you to follow up. Read any time in it "
            "(\"tonight\", \"tomorrow\", \"at 6\") against when it was noted. If that moment "
            "hasn't come yet, return no message and set not_before to it: \"HH:MM\" for later "
            "today, \"YYYY-MM-DD HH:MM\" for a later day. Their quiet hours are "
            f"{int(cfg.quiet_start):02d}:00-{int(cfg.quiet_end):02d}:00 and nothing is sent then: "
            "if the moment falls inside them, defer to just before they start, not into them.")


def _convo_lines(turns) -> str:
    return "\n".join(f"[{_hm(t.at)}] {'person' if t.role == 'user' else f'tilia [{t.state}]'}: "
                     f"{t.text}" for t in turns)


def _has_crisis(turns) -> bool:
    return any(t.state == "crisis" for t in turns)


def _recent_crisis(store, now, within=CRISIS_RECENT) -> bool:
    """A crisis episode that ended within `within` minus EPISODE_GAP (or is still going)."""
    last = store.get_meta(coach._CRISIS_LAST)
    try:
        if last and now - datetime.fromisoformat(last) < within:
            return True
    except ValueError:
        pass
    return any(t.state == "crisis" and now - datetime.fromisoformat(t.at) < within
               for t in store.recent_turns(CONVO_SCAN))


def _last_conversation(store) -> list:
    """The most recent idle period (turns less than TALKING apart) with a user turn in it."""
    turns = store.recent_turns(CONVO_SCAN)
    segs, cur = [], []
    for t in turns:
        if cur and datetime.fromisoformat(t.at) - datetime.fromisoformat(cur[-1].at) >= TALKING:
            segs.append(cur)
            cur = []
        cur.append(t)
    if cur:
        segs.append(cur)
    return next((s for s in reversed(segs) if any(t.role == "user" for t in s)), [])


def _followup_cand(store, now) -> Candidate | None:
    """A substantial conversation that ended 12-36 h ago, with nothing from either side since."""
    seg = _last_conversation(store)
    users = [t for t in seg if t.role == "user"]
    if len(users) < FOLLOWUP_USER_TURNS:
        return None
    end = datetime.fromisoformat(seg[-1].at)
    if not FOLLOWUP_MIN <= now - end <= FOLLOWUP_MAX:
        return None
    if any(r["sent"] and datetime.fromisoformat(r["at"]) > end
           for r in store.recent_checkins(CHECKINS_IN_PROMPT)):
        return None
    return Candidate(
        "followup",
        f"Your last real conversation ended {_day_hm(end)}; nothing from either of you since. "
        f"Its last turns:\n{_convo_lines(seg[-FOLLOWUP_TURNS:])}\n"
        + (CRISIS_IN_CONVO + " Follow the crisis rule." if _has_crisis(seg) else
           "Anchor on the single most important open thread in it: a decision they were "
           "weighing, a talk they were dreading, a feeling they named."),
        f"f:{users[-1].id}", _PRIO["followup"], seg[-1].at)


def _care_cand(store, cfg, now) -> Candidate | None:
    """The floor: no message either way for 36 h. Keyed on the person's last message, so an
    unanswered care check-in is never followed by another until they write again."""
    last = store.recent_turns(1)
    if not last or now - datetime.fromisoformat(last[0].at) < CARE_QUIET:
        return None
    seg = _last_conversation(store)
    users = [t for t in seg if t.role == "user"]
    if not users:
        return None
    asg = "\n".join(f"#{a.id} [{a.kind}] {a.text} (due {a.due or 'no date'})"
                    for a in store.open_assignments())
    return Candidate(
        "care",
        f"No message either way since {_day_hm(last[0].at)}.\n"
        f"Their goal, in their words: {cfg.goal or '(not set)'}\n"
        f"Open assignments:\n{asg or '(none)'}\n"
        f"The last conversation's last turns:\n{_convo_lines(seg[-FOLLOWUP_TURNS:]) or '(none)'}\n"
        "Anchor on one concrete thing here (their goal, an open thread, an assignment). "
        "This is not permission for a general hello."
        + ("\n" + CRISIS_IN_CONVO + " Follow the crisis rule." if _has_crisis(seg) else ""),
        f"c:{users[-1].id}", _PRIO["care"], last[0].at)


def _anchor_cand(store, now) -> Candidate | None:
    """Something time-bound the person mentioned for today, in their own words."""
    today = now.astimezone().date()
    hits = []
    for t in store.recent_turns(coach.TURNS_IN_PROMPT):
        if t.role != "user":
            continue
        day = datetime.fromisoformat(t.at).astimezone().date()
        timed = (day == today and _WHEN.search(t.text)) or \
                (day == today - timedelta(days=1) and _TOMORROW.search(t.text))
        if timed and _EVENT.search(t.text):
            hits.append(t)
    if not hits:
        return None
    ctx = "\n".join(f"[{_hm(t.at)}] they said: {t.text}" for t in hits[-3:])
    return Candidate("anchor", "Something they mentioned for today:\n" + ctx,
                     f"t:{hits[-1].id}", _PRIO["anchor"], hits[-1].at)


def _card_type(store) -> str:
    """Alternate: tarot unless the last card was tarot (a spread counts as tarot), or there
    is no deck or tarot sheet. A tarot turn becomes a three-card spread when the last
    SPREAD_EVERY - 1 cards hold no spread and the deck and sheet allow it."""
    last = store.entries(types=CARD_TYPES)
    if (not entries.available_cards() or "tarot" not in entries.available_types()
            or (last and last[-1].type in entries.TAROT_TYPES)):
        return "blot"
    recent = last[-(SPREAD_EVERY - 1):]
    if (len(recent) == SPREAD_EVERY - 1 and not any(e.type in entries.SPREADS for e in recent)
            and CHECKIN_SPREAD in entries.available_types()
            and len(entries.available_cards()) >= len(entries.SPREADS[CHECKIN_SPREAD])):
        return CHECKIN_SPREAD
    return "tarot"


def _card_cand(store, cfg, now) -> Candidate | None:
    """Now and then a card or a blot, sent first (D7). Never within a week of another one
    (opened or not), never in or within 96 h after a crisis episode, never after a
    struggling turn: a card in a dark moment reads as a cruel joke. Never on a fresh
    install either: like care, it needs them to have written at least once."""
    if not (cfg.cards and cfg.worksheets):
        return None
    turns = store.recent_turns(CONVO_SCAN)
    if not any(t.role == "user" for t in turns):
        return None
    if any(now - datetime.fromisoformat(e.created) < CARD_EVERY
           for e in store.entries(types=CARD_TYPES)):
        return None
    if _recent_crisis(store, now, CARD_AFTER_CRISIS):
        return None
    last_coach = next((t for t in reversed(turns) if t.role == "coach"), None)
    if last_coach is not None and last_coach.state == "struggling":
        return None
    type_ = _card_type(store)
    if type_ not in entries.available_types():  # no sheet, no button: nothing to invite to
        return None
    what = {"tarot": "a tarot card, drawn for them (they read it first, then you)",
            "blot": "an ink blot, made for them (they say what they see, then you respond)",
            CHECKIN_SPREAD: "a small three-card spread, drawn for them (they read each card "
                            "first, then you)",
            }.get(type_)
    if what is None:  # a card type with no invite wording: never mislabel it, just skip
        return None
    return Candidate(
        "card",
        f"It's been a while, so you have {what}. It rides as a button under your message. "
        "Write one light line inviting them to look, nothing more: nothing heavy, no "
        "question about their feelings, no assignment. If your most recent state was "
        "struggling, return no message.",
        f"k:{now.astimezone().date().isoformat()}", _PRIO["card"], "")


def candidates(store, cfg, now) -> list[Candidate]:
    """Every current reason to check in, strongest first. Makes no Claude call."""
    avail, used = _available(store, now), set(_used_keys(store))

    def ok(c):
        return avail(c.key) and c.key not in used
    cands = _assignment_cands(store, now) + _signal_cands(store, cfg, now)
    cands += [c for c in (_followup_cand(store, now), _anchor_cand(store, now)) if c]
    if not cands:  # before the availability filter: a reason tried or deferred today still counts
        care = _care_cand(store, cfg, now)
        cands = [care] if care else []
    card = _card_cand(store, cfg, now)
    if card:  # the lowest: its empty `at` loses every tie, care's included
        cands.append(card)
    cands = [c for c in cands if ok(c)]
    if cands and _recent_crisis(store, now):
        for c in cands:
            if CRISIS_IN_CONVO not in c.context:
                c.context += "\n" + CRISIS_LATELY + " Follow the crisis rule."
    cands.sort(key=lambda c: c.at, reverse=True)  # newest first within a priority
    cands.sort(key=lambda c: c.prio)
    return cands


# ---- gates -----------------------------------------------------------------

def effective_cap(store, cfg) -> int:
    cap = max(0, int(cfg.checkin_cap))
    if cap and store.unreplied_streak() >= UNREPLIED_HALVES:
        cap = max(1, cap // 2)
    return cap


def _quiet(cfg, now) -> bool:
    h, qs, qe = now.astimezone().hour, cfg.quiet_start, cfg.quiet_end
    if qs == qe:
        return False
    return (h >= qs or h < qe) if qs > qe else (qs <= h < qe)


def _paused(cfg, now) -> bool:
    if not cfg.paused_until:
        return False
    try:
        return now < datetime.fromisoformat(cfg.paused_until)
    except (TypeError, ValueError):
        return False


def _talking(store, now) -> bool:
    if store.stats()["pending"]:
        return True
    stamps = [t.at for t in store.recent_turns(1)]
    if store.get_meta("awaiting_at"):
        stamps.append(store.get_meta("awaiting_at"))
    return any(now - datetime.fromisoformat(s) < TALKING for s in stamps)


def _held(store, now) -> bool:
    """Set by the coach after a turn whose state couldn't be read (see coach._fallback)."""
    until = store.get_meta("checkins_hold_until")
    try:
        return bool(until) and now < datetime.fromisoformat(until)
    except ValueError:
        return False


def _blocked(store, cfg, now) -> bool:
    return (cfg.chat_id is None or _quiet(cfg, now) or _paused(cfg, now) or _held(store, now)
            or store.checkins_today(now) >= effective_cap(store, cfg)
            or coach._episode(store, now)[0]           # in a crisis the conversation is the support
            or store.get_meta("mode") == "rehearsal"
            or _talking(store, now))


# ---- prompts ---------------------------------------------------------------

def _header(now) -> str:
    return f"Current local time: {now.astimezone().strftime('%Y-%m-%d %H:%M (%A)')}\n{DATA_NOTE}"


def _profile() -> str:
    return "PROFILE (your working notes on this person)\n" + \
        _tag("profile", profile.read(), "(empty: you are just getting to know them)")


def _reason(c: Candidate) -> str:
    return f"REASON: {c.reason}\n" + _tag("reason", c.context)


def _recent_checkins(store, now) -> str:
    rows = [f"[{_hm(r['at'])}] {'answered' if r['replied'] else 'no reply'}: {r['text']}"
            for r in store.recent_checkins(CHECKINS_IN_PROMPT * 3)
            if r["sent"] and now - datetime.fromisoformat(r["at"]) <= timedelta(days=14)]
    return "RECENT CHECK-INS (texts you sent first, last 14 days)\n" + \
        _tag("checkins", "\n".join(rows[-CHECKINS_IN_PROMPT:]))


def build_draft_prompt(store, cfg, now, c: Candidate) -> str:
    return "\n\n".join([
        _header(now),
        coach._settings(cfg, now),
        _profile(),
        _corrections(store),
        coach._assignments(store),
        coach._signals(store, cfg, now),
        _recent_texts(store, cfg, now),
        coach._conversation(store),
        _recent_checkins(store, now),
        _reason(c),
        "Write the check-in now, or decide not to. Reply with the JSON object only.",
    ])


def _corrections(store) -> str:
    return ("CORRECTIONS (facts from the person; these override the profile)\n"
            + _tag("corrections", "\n".join(f"- {x}" for x in store.corrections())))


def _recent_texts(store, cfg, now) -> str:
    return ("RECENT TEXTS (48h) from the threads the person shared; \"me\" is the person\n"
            + _tag("texts", texts.recent_for_prompt(store, cfg, now)))


def build_test_prompt(store, cfg, now, c: Candidate, draft: str, why: str = "") -> str:
    """The judge sees everything the drafter saw, so a real specific is never mistaken
    for a made-up one."""
    return "\n\n".join([
        _header(now),
        _profile(),
        _corrections(store),
        coach._assignments(store),
        coach._signals(store, cfg, now),
        _recent_texts(store, cfg, now),
        coach._conversation(store),
        _reason(c),
        "DRAFT (the message the coach wants to send first)\n" + _tag("draft", draft),
        "DRAFTER'S NOTE (what the coach says the draft is anchored to; check it against "
        "the context above)\n" + _tag("why", why),
        "Judge the draft. Reply with the JSON object only.",
    ])


def _system(name: str) -> str:
    return (paths.REPO_ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


# ---- sending ---------------------------------------------------------------

def _parse_draft(obj, now) -> tuple[str | None, datetime | None]:
    if not isinstance(obj, dict):
        return None, None
    msg = obj.get("message")
    msg = msg.strip() if isinstance(msg, str) and msg.strip() else None
    nb, when = obj.get("not_before"), None
    if msg is None and isinstance(nb, str):
        nb = nb.strip()
        m = _HHMM.match(nb)
        if m:
            when = now.astimezone().replace(hour=int(m[1]), minute=int(m[2]),
                                            second=0, microsecond=0)
        else:
            try:  # "YYYY-MM-DD HH:MM": a later day
                when = datetime.strptime(nb, "%Y-%m-%d %H:%M").astimezone()
            except ValueError:
                when = None
        if when is not None and when <= now:
            when = None
    return msg, when


def _consume(store, c: Candidate, now, sent: bool, final: bool) -> None:
    """Always: tried for today. `final` (sent, or judged generic) also retires a signal for
    good; silence leaves it for another day (an intent can be days out)."""
    st = _state(store, now)
    st["tried"].append(c.key)
    st["defer"].pop(c.key, None)
    _save_state(store, st)
    kind, _, ref = c.key.partition(":")
    if kind == "s" and final:
        used = sorted(_used(store) | {int(ref)})[-USED_KEEP:]
        store.set_meta(_USED, json.dumps(used))
    elif kind == "a" and sent:
        store.mark_followed_up(int(ref), now)
    elif (kind in ("f", "k", "c") and final) or kind == "c":  # care: silence is final too
        store.set_meta(_USED_KEYS, json.dumps((_used_keys(store) + [c.key])[-USED_KEEP:]))


def _defer(store, c: Candidate, now, until) -> None:
    st = _state(store, now)
    st["defer"][c.key] = until.isoformat()
    _save_state(store, st)


def _offer(store, cfg, now, c: Candidate, run):
    """The button that rides with a check-in, if any: a fresh card or blot for `card`, the
    open worksheet of a due worksheet assignment. An intent check-in that follows up an
    entry's plan marks that entry, so the mirror shows plan -> what happened."""
    kind, _, ref = c.key.partition(":")
    if c.reason == "card":
        return worksheets.prepare(store, cfg, now, _card_type(store), source="checkin", run=run)
    if kind == "a":
        a = next((a for a in store.open_assignments() if a.id == int(ref)), None)
        if a is not None and a.kind == "worksheet":
            e = next((e for e in store.entries(statuses=("open",))
                      if e.assignment_id == a.id), None)
            if e is not None:
                try:
                    return worksheets.offer_for(store, e)
                except ValueError:  # its sheet is gone: the nudge goes without a button
                    return None
    elif kind == "s":
        for e in store.entries():
            if e.followup_signal == int(ref):
                store.set_entry_followup_at(e.id, now.isoformat())
    return None


def maybe_send(store, cfg, now, run=subprocess.run) -> bool:
    """At most one draft and one generic test. True only if a check-in was queued.
    LimitError / NotLoggedIn / MissingCLI propagate to the loop.

    A check-in goes to the loop's outbox, which retries for up to 6 h, so it is recorded
    as sent (sent=1, a coach turn, its signal used) when queued. Only silence or a
    not-before time leave the reason for later; a generic draft retires it.

    A `card` check-in that passes makes one more AI call: a tarot card's sealed read
    (worksheets.prepare -> seal), inside the transaction. Its failure never stops the card."""
    if _blocked(store, cfg, now):
        return False
    cands = candidates(store, cfg, now)
    if not cands:
        return False
    c = cands[0]
    draft = ai.ask_json(cfg, build_draft_prompt(store, cfg, now, c), _system("checkin"), run=run)
    msg, later = _parse_draft(draft, now)
    if msg is None:
        if later is not None:
            _defer(store, c, now, later)  # the right moment is later (today or a later day)
        else:
            _consume(store, c, now, sent=False, final=False)  # silence, for today
        return False
    why = draft.get("why") if isinstance(draft.get("why"), str) else ""
    verdict = ai.ask_json(cfg, build_test_prompt(store, cfg, now, c, msg, why),
                          _system("generic_test"), run=run)
    if not (isinstance(verdict, dict) and verdict.get("generic") is False):
        store.add_checkin(c.reason, msg, False)  # generic, or no clear verdict: discard
        _consume(store, c, now, sent=False, final=True)
        return False
    with store.atomic():
        offer = _offer(store, cfg, now, c, run)
        if c.reason == "card" and offer is None:  # the sheet went unreadable: no card to send
            _consume(store, c, now, sent=False, final=True)
            return False
        outbox.enqueue(store, msg, now, markup=offer.markup if offer else None)
        store.add_checkin(c.reason, msg, True)
        store.add_turn("coach", msg, "ok")
        _consume(store, c, now, sent=True, final=True)
    return True
