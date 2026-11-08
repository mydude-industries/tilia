"""The 2-second loop: Telegram in, the coach out, texts and check-ins in the background.

Rules that matter more than anything else here:
- only the paired chat is ever answered; every other chat is dropped and counted;
- the person's words are never dropped: they sit in `pending` until a reply succeeds;
- one batch, one answer; and no exception ever escapes tick().
Logs carry error type names only, never message content."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

from . import checkins, coach, commands, outbox, signals, texts
from .claude import LimitError, MissingCLI, NotLoggedIn, UnsupportedCLI
from .store import local_now
from .telegram import split_reply
from .typing_indicator import INTERVAL as TYPING_EVERY_S, Typing, pause_for, ping

TICK_S = 2
QUIET = timedelta(seconds=8)             # batch window: answer after this much quiet
TYPING_EVERY = timedelta(seconds=TYPING_EVERY_S)  # "typing…" lasts ~5 s on Telegram
INGEST_EVERY = timedelta(seconds=60)
SLOW_EVERY = timedelta(minutes=15)       # daily signals pass + check-ins
IDLE_SUMMARY = timedelta(minutes=30)
CLAUDE_RETRY = timedelta(minutes=10)     # after a usage limit or the AI being unreachable
ERROR_RETRY = timedelta(seconds=60)      # after any other failed reply
BG_RETRY = timedelta(minutes=10)         # after any failed background Claude call
POWER_EVERY = timedelta(seconds=60)
OUTBOX_EVERY = timedelta(seconds=30)
OUTBOX_GIVE_UP = timedelta(hours=6)
TROUBLE_AFTER = 3                        # failed replies in a row before saying so

UNSUPPORTED = "I can only read text for now, but I'm here."
DOWN = "I can't reach Claude. Open Terminal and run `claude` to log in."
DOWN_CHATGPT = ("I can't reach ChatGPT. Open Terminal in the Tilia folder and run ./setup ai "
                "to log in again.")
TROUBLE = "Something's off on my side. I have your messages and I'll keep trying."
TEXTS_OFF = ("I can't open Messages right now, so I'm not reading your texts. "
             "Run ./setup texts on your Mac to fix it.")
UNCHECKED = ("Codex on this Mac is a version I haven't checked, so I'm not using it. Your "
             "messages are saved and I'll answer them once it's fixed: run ./update in the Tilia "
             "folder for a Tilia that supports it, or ./setup ai.")
CLAUDE_ERRORS = (LimitError, NotLoggedIn, MissingCLI, UnsupportedCLI)


def down_notice(cfg) -> str:
    return DOWN_CHATGPT if getattr(cfg, "provider", "claude") == "chatgpt" else DOWN


def limit_notice(hint: str) -> str:
    # reset_hint carries its own verb ("resets 3pm"), so it reads best in parentheses.
    hint = (hint or "").strip()
    head = f"I'm at my usage limit ({hint})." if hint else "I'm at my usage limit."
    return head + " I'll read everything when I'm back."


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} tilia: {msg}", file=sys.stderr, flush=True)


class Loop:
    def __init__(self, cfg, store, tg, clock=local_now, run=subprocess.run,
                 chatdb_opener=lambda: None, power=None, ac=None, sleep=time.sleep,
                 typing=Typing):
        """chatdb_opener() returns an open chat.db connection, or None to let
        texts.ingest open the real one itself. power/ac: a power.Power and a
        `() -> bool` on-AC probe; both optional (tests leave them out).
        sleep: the pause between parts of a split reply. typing(tg, chat_id, first_after=s):
        the context manager that shows "typing…" while the coach writes."""
        self.cfg, self.store, self.tg, self.clock, self.run = cfg, store, tg, clock, run
        self.sleep, self.typing = sleep, typing
        self.chatdb_opener = chatdb_opener
        self.power, self.ac = power, ac
        now = clock()
        # Pending text from before a restart is answered after one quiet window.
        self.last_msg_at = now if self._pending() else None
        self.reply_at = None          # earliest retry of a failed reply
        self.bg_at = None             # earliest background Claude work after a failure
        self.next_ingest = self.next_slow = self.next_power = self.next_outbox = None
        self.failures = 0
        self.typed_at = None          # when "typing…" was last shown by the tick

    # ---- the tick --------------------------------------------------------------

    def tick(self) -> None:
        try:
            now = self.clock()
            self._safe("outbox", self._flush_outbox, now)
            self._safe("updates", self._poll, now)
            self._safe("typing", self._keep_typing, now)
            self._safe("reply", self._answer, now)
            if self.next_ingest is None or now >= self.next_ingest:
                self.next_ingest = now + INGEST_EVERY
                self._safe("texts", self._texts, now)
            if self.bg_at is None or now >= self.bg_at:
                if self.next_slow is None or now >= self.next_slow:
                    self.next_slow = now + SLOW_EVERY
                    self._background("checkins", self._slow, now)
                self._background("summary", self._summary, now)
            if self.power is not None and (self.next_power is None or now >= self.next_power):
                self.next_power = now + POWER_EVERY
                self._safe("power", lambda _: self.power.update(self.ac()), now)
        except Exception as e:  # belt and braces: tick never raises
            log(f"tick failed: {type(e).__name__}")

    def run_forever(self, sleep=time.sleep) -> None:
        log("running")
        try:
            while True:
                self.tick()
                sleep(TICK_S)
        finally:
            if self.power is not None:
                self.power.release()

    def _safe(self, name, fn, now) -> None:
        try:
            fn(now)
        except Exception as e:
            log(f"{name} failed: {type(e).__name__}")

    def _background(self, name, fn, now) -> None:
        """Background Claude work. Any failure (limit, login, timeout) waits BG_RETRY;
        the person isn't waiting on it, so no notice."""
        try:
            fn(now)
        except Exception as e:
            self.bg_at = now + BG_RETRY
            log(f"{name} failed: {type(e).__name__}")

    # ---- Telegram in -------------------------------------------------------------

    def _poll(self, now) -> None:
        raw = self.store.get_meta("tg_offset")
        offset = int(raw) if raw else None
        for u in self.tg.updates(offset):
            if offset is not None and u.update_id < offset:
                continue
            try:
                self._update(u, now)
            except Exception as e:
                log(f"update failed: {type(e).__name__}")
            # Advance past it either way: a poison update must not replay forever.
            offset = u.update_id + 1
            self.store.set_meta("tg_offset", str(offset))

    def _update(self, u, now) -> None:
        if self.cfg.chat_id is None or u.chat_id != int(self.cfg.chat_id):
            n = int(self.store.get_meta("ignored_chats") or 0) + 1
            self.store.set_meta("ignored_chats", str(n))
            return
        self.store.mark_last_checkin_replied()
        self._touch(now)
        if u.kind != "text" or u.text is None:
            self.tg.send(self.cfg.chat_id, UNSUPPORTED)
            return
        is_command = u.text.strip().startswith("/")
        try:
            consumed = commands.handle(u.text, self.store, self.cfg, now, self.tg,
                                       self.cfg.chat_id, run=self.run)
        except CLAUDE_ERRORS as e:
            # handle() can call the coach (practice, journal, /done). Keep the words.
            self._claude_down(e, now)
            consumed = is_command
        except Exception as e:
            log(f"command failed: {type(e).__name__}")
            consumed = is_command
        if not consumed:
            self.store.add_pending(u.text)
            self.last_msg_at = now
        elif self.store.get_meta("outbox"):
            self._flush_now(now)  # a command's coach reply (practice, journal, /done)

    # ---- the coach out -----------------------------------------------------------

    def _pending(self) -> int:
        return self.store.stats()["pending"]

    def _keep_typing(self, now) -> None:
        """Show "typing…" from the moment her words are queued until the reply is out:
        every TYPING_EVERY while they wait in pending. Not while a failed batch waits to
        be retried (she'd see typing for minutes), and only ever to the paired chat."""
        if self.cfg.chat_id is None or not self._pending():
            self.typed_at = None
            return
        if self.reply_at is not None and now < self.reply_at:
            return
        if self.typed_at is not None and now - self.typed_at < TYPING_EVERY:
            return
        self.typed_at = now
        ping(self.tg, self.cfg.chat_id)

    def _typing_first_after(self, now) -> float:
        """Seconds until Typing should show its first indicator: the rest of the interval
        if the tick just showed one, so generation carries on without a double ping."""
        if self.typed_at is None:
            return 0
        left = (TYPING_EVERY - (now - self.typed_at)).total_seconds()
        return left if left > 0 else 0

    def _answer(self, now) -> None:
        if not self._pending():
            return
        if self.last_msg_at is None:
            self.last_msg_at = now
        if now - self.last_msg_at < QUIET:
            return
        if self.reply_at is not None and now < self.reply_at:
            return
        rows = self.store.peek_pending()
        if not rows:
            return
        batch = [t for _, t in rows]
        try:
            # One transaction: the turns respond() stores, removing the answered rows
            # from pending, and queueing the reply in the outbox. If the process dies
            # anywhere in here (SIGTERM at logout, a kill, power loss) it all rolls back
            # and the words are still in pending; once committed, the reply is in the
            # outbox and a restart delivers it.
            with self.store.atomic():
                with self.typing(self.tg, self.cfg.chat_id,
                                 first_after=self._typing_first_after(now)):
                    r = coach.respond(self.store, self.cfg, now, batch, run=self.run,
                                      mode=commands.current_mode(self.store))
                self.store.delete_pending(rows[-1][0])
                self._enqueue(r.reply, now)
        except CLAUDE_ERRORS as e:
            self._claude_down(e, now)
            return
        except Exception as e:
            self.failures += 1
            self.reply_at = now + ERROR_RETRY
            log(f"reply failed: {type(e).__name__}")
            if self.failures >= TROUBLE_AFTER:
                self._notice("trouble", TROUBLE)
            return
        self.failures = 0
        self.reply_at = None
        self.typed_at = None
        self.store.set_meta("notice", None)  # the episode is over
        self._touch(now)
        self._flush_now(now)

    def _claude_down(self, e: Exception, now) -> None:
        self.reply_at = now + CLAUDE_RETRY
        log(f"claude unavailable: {type(e).__name__}")
        if isinstance(e, LimitError):
            self._notice("limit", limit_notice(e.reset_hint))
        elif isinstance(e, UnsupportedCLI):
            self._notice("unchecked", UNCHECKED)
        else:
            self._notice("down", down_notice(self.cfg))

    def _notice(self, kind: str, text: str) -> None:
        """One notice per kind per episode; cleared by the next successful reply."""
        if self.store.get_meta("notice") == kind:
            return
        self.store.set_meta("notice", kind)
        self.tg.send(self.cfg.chat_id, text)

    def _enqueue(self, text: str, now) -> None:
        outbox.enqueue(self.store, text, now)

    def _flush_now(self, now) -> None:
        self.next_outbox = None
        self._flush_outbox(now)

    def _flush_outbox(self, now) -> None:
        raw = self.store.get_meta("outbox")
        if not raw or (self.next_outbox is not None and now < self.next_outbox):
            return
        self.next_outbox = now + OUTBOX_EVERY
        box = json.loads(raw)
        while box:  # oldest first; stop at the first failure so order is kept
            item = box[0]
            if now - datetime.fromisoformat(item["since"]) > OUTBOX_GIVE_UP:
                log("outbox: gave up on one reply")
            elif not self._send_parts(box, item):
                return
            box.pop(0)
            # saved after each send, so dying mid-flush never sends a reply twice
            self.store.set_meta("outbox", json.dumps(box) if box else None)

    def _send_parts(self, box, item) -> bool:
        """Send one outbox item part by part. What is left is saved after each part, so
        a blip or a restart resumes at the next part and no part goes twice. Between
        parts: "typing…" and a short pause, so they don't land all at once."""
        parts = item.get("parts") or split_reply(item["text"])
        while parts:
            if not self.tg.send(self.cfg.chat_id, parts[0]):
                return False
            parts.pop(0)
            if parts:
                item["parts"] = parts
                self.store.set_meta("outbox", json.dumps(box))
                ping(self.tg, self.cfg.chat_id)
                try:
                    self.sleep(pause_for(parts[0]))
                except Exception as e:
                    log(f"pause failed: {type(e).__name__}")
        return True

    def _touch(self, now) -> None:
        self.store.set_meta("last_activity", now.isoformat())

    # ---- background ----------------------------------------------------------------

    def _texts(self, now) -> None:
        conn = self.chatdb_opener()
        try:
            texts.ingest(self.store, self.cfg, conn, now)
            self._texts_notice(now)
            texts.rule_signals(self.store, self.cfg, now)
        finally:
            if conn is not None:
                conn.close()

    def _texts_notice(self, now) -> None:
        """Say once when texts stop being readable; forget that we said it once they are."""
        off = self.store.get_meta("texts_unavailable")
        told = self.store.get_meta("texts_notice") == "1"
        if off == "1" and not told:
            self.store.set_meta("texts_notice", "1")
            self._enqueue(TEXTS_OFF, now)
            self._flush_now(now)
        elif off == "0" and told:
            self.store.set_meta("texts_notice", None)

    def _slow(self, now) -> None:
        signals.daily_pass(self.store, self.cfg, now, run=self.run)
        if checkins.maybe_send(self.store, self.cfg, now, run=self.run):
            self._flush_now(now)

    def _summary(self, now) -> None:
        """Once per idle period: 30 min after the last activity, with nothing pending."""
        last = self.store.get_meta("last_activity")
        if not last or self._pending():
            return
        if now - datetime.fromisoformat(last) < IDLE_SUMMARY:
            return
        if self.store.get_meta("summarized_activity") == last:
            return
        coach.summarize(self.store, self.cfg, now, run=self.run)
        self.store.set_meta("summarized_activity", last)
