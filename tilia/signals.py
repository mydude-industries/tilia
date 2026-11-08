"""The daily model pass over chosen text threads: conflict, repair, bid and pattern_hit.
Raw texts go into the prompt only; what is stored is the model's one-line description."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta

from . import ai, paths, profile, texts
from .coach import DATA_NOTE, _tag

PASS_HOUR = 19  # run in the evening, so the pass sees most of the day
MAX_WINDOW = timedelta(hours=36)
KINDS = ("conflict", "repair", "bid", "pattern_hit")
MAX_PER_PASS = 5
DETAIL_MAX = 300


def build_prompt(name: str, msgs: list[dict], now) -> str:
    lines = "\n".join(texts._line(m, name) for m in msgs)
    return "\n\n".join([
        f"Current local time: {now.astimezone().strftime('%Y-%m-%d %H:%M (%A)')}\n{DATA_NOTE}",
        "PROFILE (the coach's notes on the person)\n"
        + _tag("profile", profile.read(), "(empty)"),
        f"MESSAGES (one thread with {name}; \"me\" is the person being coached; oldest first)\n"
        + _tag("texts", lines),
        "Reply with the JSON object only.",
    ])


def daily_pass(store, cfg, now, run=subprocess.run) -> int:
    """Once per chosen thread per local day (meta signals_day:<chat_id>), at or after
    PASS_HOUR: one ask_json over the messages since that thread's previous pass (36 h at
    most). Returns the number of signals added. LimitError / NotLoggedIn / MissingCLI
    propagate and leave the day unmarked, so the next tick retries."""
    if now.astimezone().hour < PASS_HOUR:
        return 0
    today = now.astimezone().date().isoformat()
    system = (paths.REPO_ROOT / "prompts" / "signals.md").read_text(encoding="utf-8")
    added = 0
    for t in cfg.threads:
        cid = t["chat_id"]
        if store.get_meta(f"signals_day:{cid}") == today:
            continue
        since = now - MAX_WINDOW
        last = store.get_meta(f"signals_at:{cid}")
        if last:
            since = max(since, datetime.fromisoformat(last))
        msgs = [m for m in store.text_msgs(cid, since=since)
                if datetime.fromisoformat(m["at"]) <= now]
        if msgs:  # a quiet thread costs no call
            obj = ai.ask_json(cfg, build_prompt(t["name"], msgs, now), system, run=run)
            added += _store(store, cid, obj, now)
        store.set_meta(f"signals_day:{cid}", today)
        store.set_meta(f"signals_at:{cid}", now.isoformat())
    return added


def _store(store, cid, obj, now) -> int:
    items = obj.get("signals") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return 0
    n = 0
    for s in items:
        if n >= MAX_PER_PASS:
            break
        if not isinstance(s, dict) or s.get("kind") not in KINDS:
            continue
        detail = s.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            continue
        store.add_signal(cid, s["kind"], " ".join(detail.split())[:DETAIL_MAX], now)
        n += 1
    return n
