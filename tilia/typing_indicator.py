"""Telegram's "typing…" indicator while Tilia writes a reply.

The loop's own tick shows it from the moment a message is queued (see Loop._keep_typing);
Typing covers the blocking coach call, when no tick runs.

Telegram shows the indicator for about 5 s, so Typing sends it once on entry and again
every 4 s from a daemon thread until the block ends (normally or by an exception). The
thread only ever calls tg.typing: no Store access, so no SQLite across threads. A
failed indicator is cosmetic: it is logged by type name and never breaks a reply."""
from __future__ import annotations

import sys
import threading

INTERVAL = 4          # seconds between indicators while the coach is writing
JOIN_S = 10           # tg.typing has its own network timeout well under this
PART_PAUSE = 1.5      # seconds before the next part of a split reply...
PART_PAUSE_MAX = 3.0  # ...a little longer for a longer part, never more than this
PART_SCALE = 3500     # characters that add one second (a full part adds ~1 s)


def pause_for(next_part: str) -> float:
    return min(PART_PAUSE_MAX, PART_PAUSE + len(next_part) / PART_SCALE)


def _event_wait(ev: threading.Event, seconds: float) -> bool:
    return ev.wait(seconds)


def ping(tg, chat_id) -> None:
    """One indicator; never raises."""
    try:
        tg.typing(chat_id)
    except Exception as e:
        print(f"tilia: typing indicator failed: {type(e).__name__}", file=sys.stderr)


class Typing:
    """`with Typing(tg, chat_id): reply = coach.respond(...)`.

    wait(stop_event, seconds) -> True once stopped; injectable so tests don't sleep.
    first_after: seconds until the first indicator. 0 sends one on entry; the loop passes
    the rest of the interval when it showed "typing…" moments ago, so there's no burst."""

    def __init__(self, tg, chat_id, interval: float = INTERVAL, wait=_event_wait,
                 thread=threading.Thread, first_after: float = 0):
        self.tg, self.chat_id, self.interval = tg, chat_id, interval
        self.first_after = max(0.0, float(first_after))
        self._wait, self._thread_cls = wait, thread
        self._stop = threading.Event()
        self.thread = None

    def __enter__(self):
        if self.first_after <= 0:
            ping(self.tg, self.chat_id)
        self.thread = self._thread_cls(target=self._run, name="tilia-typing", daemon=True)
        self.thread.start()
        return self

    def _run(self) -> None:
        if self.first_after > 0:
            if self._wait(self._stop, self.first_after):
                return
            ping(self.tg, self.chat_id)
        while not self._wait(self._stop, self.interval):
            ping(self.tg, self.chat_id)

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(JOIN_S)
        return False  # never swallow the coach's exception
