"""Check-ins: specific or silent. Find a real reason to text first, draft one message, and
send it only if a second call judges it not generic."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import ai, coach, outbox, paths, profile, texts
from .coach import DATA_NOTE, _hm, _tag

SIGNAL_FRESH = timedelta(hours=24)
INTENT_FRESH = timedelta(days=3)
TALKING = timedelta(minutes=30)
UNREPLIED_HALVES = 3
CHECKINS_IN_PROMPT = 10
USED_KEEP = 300
_STATE, _USED = "checkin_state", "used_signal_ids"

# lower is stronger
_PRIO = {"assignment": 0, "conflict": 1, "repair": 1, "unanswered_question": 1,
         "intent": 2, "bid": 3, "pattern_hit": 3, "long_silence": 3, "anchor": 4}

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
    key: str = ""   # "a:<assignment id>", "s:<signal id>" or "t:<turn id>"
    prio: int = 9
    at: str = ""


# ---- small state -----------------------------------------------------------

def _state(store, now) -> dict:
    today = now.astimezone().date().isoformat()
    try:
        st = json.loads(store.get_meta(_STATE) or "{}")
    except ValueError:
        st = {}
    if not isinstance(st, dict) or st.get("day") != today:
        st = {"day": today, "tried": [], "defer": {}}
    return st


def _save_state(store, st) -> None:
    store.set_meta(_STATE, json.dumps(st))


def _used(store) -> set[int]:
    try:
        return set(json.loads(store.get_meta(_USED) or "[]"))
    except ValueError:
        return set()


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
                out.append(Candidate(
                    "intent", f"At {_hm(s['at'])} they told you they would: {s['detail']}",
                    key, _PRIO["intent"], s["at"]))
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


def candidates(store, cfg, now) -> list[Candidate]:
    """Every current reason to check in, strongest first. Makes no Claude call."""
    ok = _available(store, now)
    cands = _assignment_cands(store, now) + _signal_cands(store, cfg, now)
    anchor = _anchor_cand(store, now)
    if anchor:
        cands.append(anchor)
    cands = [c for c in cands if ok(c.key)]
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
        coach._settings(cfg),
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
        m = _HHMM.match(nb.strip())
        if m:
            when = now.astimezone().replace(hour=int(m[1]), minute=int(m[2]),
                                            second=0, microsecond=0)
            if when <= now:
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


def _defer(store, c: Candidate, now, until) -> None:
    st = _state(store, now)
    st["defer"][c.key] = until.isoformat()
    _save_state(store, st)


def maybe_send(store, cfg, now, run=subprocess.run) -> bool:
    """At most one draft and one generic test. True only if a check-in was queued.
    LimitError / NotLoggedIn / MissingCLI propagate to the loop.

    A check-in goes to the loop's outbox, which retries for up to 6 h, so it is recorded
    as sent (sent=1, a coach turn, its signal used) when queued. Only silence or a
    not-before time leave the reason for later; a generic draft retires it."""
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
            _defer(store, c, now, later)  # the right moment is later today
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
        outbox.enqueue(store, msg, now)
        store.add_checkin(c.reason, msg, True)
        store.add_turn("coach", msg, "ok")
        _consume(store, c, now, sent=True, final=True)
    return True
