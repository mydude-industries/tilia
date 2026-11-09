"""The weekly look back: one model call over the finished entries that writes 3-6 dated
observations about the person ("what keeps showing up"). Stored as a `patterns` entry,
shown on the mirror and in the coach prompt."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

from . import ai, coach, entries, paths, profile
from .coach import DATA_NOTE, _tag

EVERY = timedelta(days=7)
TEXT_MAX = 200
MIN_ITEMS, MAX_ITEMS = 3, 6
PROMPT_N = 60  # the newest finished entries the pass reads
AT = "patterns_at"  # meta: when the last pass ran; /forget clears it


def _date(e) -> str:
    return datetime.fromisoformat(e.answered_at or e.created).astimezone().strftime("%Y-%m-%d")


def _previous(store) -> str:
    rows = store.entries(types=("patterns",), statuses=("done",))
    return "\n".join(
        f"- {p['text']} ({', '.join(d for d in p.get('dates') or [] if isinstance(d, str))})"
        for p in (rows[-1].answers or [] if rows else [])
        if isinstance(p, dict) and isinstance(p.get("text"), str))


def build_prompt(store, done, now) -> str:
    return "\n\n".join([
        f"Current local time: {now.astimezone().strftime('%Y-%m-%d %H:%M (%A)')}\n{DATA_NOTE}",
        "PROFILE (the coach's notes on the person)\n"
        + _tag("profile", profile.read(), "(empty)"),
        "PREVIOUS PATTERNS (what you wrote last time)\n"
        + _tag("previous_patterns", _previous(store)),
        "ENTRIES (finished worksheets, cards and blots, oldest first)\n"
        + _tag("entries", "\n".join(coach.entry_line(e, _date(e)) for e in done)),
        "Reply with the JSON object only.",
    ])


def _clean(obj, dates: set[str]) -> list[dict]:
    raw = obj.get("patterns") if isinstance(obj, dict) else None
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("text"), str):
            continue
        text = " ".join(p["text"].split())
        ds = p.get("dates")
        if not text or len(text) > TEXT_MAX or not isinstance(ds, list):
            continue
        kept = []
        for d in ds:
            if isinstance(d, str) and d in dates and d not in kept:
                kept.append(d)
        if kept:
            out.append({"text": text, "dates": kept})
    return out[:MAX_ITEMS] if len(out) >= MIN_ITEMS else []


def maybe_run(store, cfg, now, run=subprocess.run) -> bool:
    """At most once every 7 days, and only when an entry was answered since the last
    pass. True when a new patterns entry was stored. A model failure propagates and
    leaves the week unmarked; an unusable answer still counts as the week's pass."""
    last = store.get_meta(AT)
    if last and now - datetime.fromisoformat(last) < EVERY:
        return False
    done = store.entries(types=entries.TYPES, statuses=("done",))
    if not done:
        return False
    if last:
        since = datetime.fromisoformat(last)
        if not any(e.answered_at and datetime.fromisoformat(e.answered_at) > since
                   for e in done):
            return False
    done = done[-PROMPT_N:]
    system = (paths.REPO_ROOT / "prompts" / "patterns.md").read_text(encoding="utf-8")
    obj = ai.ask_json(cfg, build_prompt(store, done, now), system, run=run)
    items = _clean(obj, {_date(e) for e in done})
    with store.atomic():
        if items:
            store.add_entry("patterns", entries.new_token(), "patterns", status="done",
                            answers=items)
        store.set_meta(AT, now.isoformat())
    return bool(items)
