"""Tilia's two lines for a lit candle, answered from this Mac.

The candle page seals what the candle is for (tilia/seal.py); the relay carries the sealed
ask here. answer_ask checks that the ask belongs to an open candle lit under CANDLE_REOPEN
ago, opens the seal, asks the model for two short lines (`now` and `later`), saves them on
the entry and seals them back under the page's one-time key. A repeat ask for a candle that
already has its lines gets those lines again, with no model call.

Every failure is None, so the relay answers `{}` and the page stops waiting: there is never
a stand-in line. Logs name outcomes only, never the intention or the lines."""
from __future__ import annotations

import html
import subprocess
import sys
import time
from datetime import datetime

from . import ai, claude, coach, paths, seal, worksheets

VOICE_TIMEOUT_S = 25  # the page gives up after 30 s
RETRY_BEFORE_S = 12  # an unusable reply is asked again only while there's time for it
_TOO_LONG = ("YOUR LAST REPLY was not usable: each line must be one sentence of at most 9 "
             "words, never over 60 characters, in the JSON object. Reply again, shorter.")
LINE_MAX = 60
TURNS = 12
PAST_LINES = 3  # earlier candles' lines, shown so the new ones don't repeat them
_SOURCES = {"ask": "they asked you for it",
            "checkin": "you offered it in a check-in",
            "assignment": "it was part of something you assigned them"}


def _log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} tilia: voice: {msg}", file=sys.stderr, flush=True)


def _system() -> str:
    return (paths.REPO_ROOT / "prompts" / "candle.md").read_text(encoding="utf-8")


def _open_candle(store, now, token):
    e = store.entry_by_token(token) if isinstance(token, str) else None
    if e is None or e.type != "candle" or e.status != "open":
        return None
    try:
        age = now - datetime.fromisoformat(e.created)
    except (TypeError, ValueError):
        return None
    return e if age < worksheets.CANDLE_REOPEN else None


def _line(x) -> str | None:
    """One tidy line of 1-60 code points, or None. The prompt html-escapes what it quotes
    (an intention with `&` reads `&amp;` there), so a line that echoes it is unescaped."""
    if not isinstance(x, str):
        return None
    x = " ".join(html.unescape(x).split())
    return x if 1 <= len(x) <= LINE_MAX else None


def _past_lines(store, entry) -> str:
    rows = [f"now: {e.voice_now} | later: {e.voice_later}"
            for e in store.entries(types=("candle",))
            if e.id != entry.id and e.voice_now and e.voice_later][-PAST_LINES:]
    return ("PAST LINES (your lines for their last few candles, oldest first)\n"
            + coach._tag("past_lines", "\n".join(rows)))


def _prompt(store, entry, intention: str) -> str:
    why = _SOURCES.get(entry.source, "it was offered")
    rows = []
    for t in store.recent_turns(TURNS):
        who = "person" if t.role == "user" else "tilia"
        rows.append(f"[{coach._hm(t.at)}] {who}: {t.text}")
    return "\n\n".join([
        coach.DATA_NOTE,
        "INTENTION (what they typed the candle is for)\n"
        + coach._tag("intention", intention, "(they didn't say)"),
        f"CANDLE\nOffered {coach._hm(entry.created)}: {why}.",
        _past_lines(store, entry),
        coach._entries_digest(store),
        f"CONVERSATION (last {TURNS} turns, oldest first)\n"
        + coach._tag("conversation", "\n".join(rows), "(none yet)"),
    ])


def answer_ask(store, cfg, now, private_pem: str, entry_token: str, sealed: str,
               run=subprocess.run) -> str | None:
    """The sealed answer for one ask, or None to answer `{}`."""
    entry = _open_candle(store, now, entry_token)
    if entry is None:
        _log("refused (no open candle for that token)")
        return None
    try:
        intention, key = seal.open_ask(private_pem, sealed)
    except seal.SealError:
        _log("refused (the ask did not open)")
        return None
    if coach.in_episode(store, now):
        _log("silent (crisis episode)")  # R4
        return None
    if entry.voice_now and entry.voice_later:
        _log("resent saved lines")
        return seal.seal_answer(key, entry.voice_now, entry.voice_later)
    started = time.monotonic()
    prompt = _prompt(store, entry, intention)
    for attempt in (1, 2):
        left = VOICE_TIMEOUT_S - (time.monotonic() - started)
        try:  # never the runner's own retry: the page waits 30 s
            text = ai.run_model(cfg, prompt, _system(), run=run, timeout=left, retry=False)
        except (claude.LimitError, claude.NotLoggedIn, claude.MissingCLI, claude.UnsupportedCLI,
                RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            _log(f"no lines (model unavailable: {type(e).__name__})")
            return None
        obj = claude.extract_obj(text)
        now_line, later_line = (_line(obj.get(k)) if obj else None for k in ("now", "later"))
        if now_line is not None and later_line is not None:
            break
        if attempt == 2 or time.monotonic() - started >= RETRY_BEFORE_S:
            _log("no lines (model output unusable)")
            return None
        _log("asked again (model output unusable)")
        prompt += "\n\n" + _TOO_LONG
    store.set_entry_voice(entry.id, now_line, later_line)
    _log(f"answered in {time.monotonic() - started:.0f} s")  # how long only, never words
    return seal.seal_answer(key, now_line, later_line)
