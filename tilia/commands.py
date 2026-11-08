"""Slash commands and the confirmations they await. handle() returns True if it consumed the text."""

import subprocess
from datetime import datetime, timedelta

from . import assignments, coach, config, outbox, paths, profile

DOC_LIMIT = 3500
CAP_MAX = 4
DEFAULT_PAUSE_H = 24
AWAIT_TTL = timedelta(minutes=30)

HELP = """\
/practice - rehearse a hard conversation (I play the other person); /done ends it
/journal - your next message is saved as a journal entry, and I reflect it back
/profile - show my notes on you
/privacy - what I store, where, and who else sees what
/quieter, /more - fewer or more check-ins
/pause [hours] - no check-ins for a while (default 24)
/export - everything as one markdown file
/forget last | day | all - delete the last exchange, today, or everything (all asks you to confirm)
/help - this list"""

KEEP_TEXTS_Q = "Keep reading your texts from now on? y/n"
FORGOT = "Done. Everything is deleted, including my notes on you."
DISCLOSURE = ("Telegram stores your chat with me on its servers (bot chats are not end-to-end "
              "encrypted), and {who} processes your messages under your own {plan} account.")


def disclosure(cfg) -> str:
    chatgpt = cfg.provider == "chatgpt"
    return DISCLOSURE.format(who="OpenAI" if chatgpt else "Anthropic",
                             plan="ChatGPT" if chatgpt else "Claude")


def current_mode(store) -> str:
    """The mode the loop should pass to coach.respond for an ordinary message."""
    return "rehearsal" if store.get_meta("mode") == "rehearsal" else "chat"


def handle(text, store, cfg, now, tg, chat_id, run=subprocess.run) -> bool:
    raw = (text or "").strip()
    awaiting = store.get_meta("awaiting")
    if awaiting:
        at = store.get_meta("awaiting_at")
        if not at or now - datetime.fromisoformat(at) > AWAIT_TTL:
            _clear_awaiting(store)  # stale: handle the message normally
            awaiting = None
    if awaiting:
        if raw.startswith("/"):
            _clear_awaiting(store)  # a new command replaces whatever was pending
            if awaiting in ("forget_all", "forget_all_texts"):
                tg.send(chat_id, "Okay, nothing deleted.")
        else:
            _clear_awaiting(store)
            return _awaited(awaiting, raw, text, store, cfg, now, tg, chat_id, run)
    if not raw.startswith("/"):
        return False
    parts = raw.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        tg.send(chat_id, "I don't know that one. /help lists what I do.")
        return True
    fn(args, store, cfg, now, tg, chat_id, run)
    return True


def _clear_awaiting(store) -> None:
    store.set_meta("awaiting", None)
    store.set_meta("awaiting_at", None)


def _await(store, kind, now) -> None:
    store.set_meta("awaiting", kind)
    store.set_meta("awaiting_at", now.isoformat())


def _awaited(kind, raw, text, store, cfg, now, tg, chat_id, run) -> bool:
    if kind == "forget_all":
        if raw == "FORGET":
            if cfg.threads:  # else the texts are copied straight back within a minute
                _await(store, "forget_all_texts", now)
                tg.send(chat_id, KEEP_TEXTS_Q)
                return True
            _forget_all(store, now, tg, chat_id, FORGOT)
            return True
        tg.send(chat_id, "Okay, nothing deleted.")
        return False  # the message itself is an ordinary message for the coach
    if kind == "forget_all_texts":
        ans = raw.lower()
        if ans in ("y", "yes"):
            _forget_all(store, now, tg, chat_id, FORGOT + (
                " I'll keep reading your texts, so they'll be copied again from scratch, "
                "as far back as setup said."))
            return True
        if ans in ("n", "no"):
            cfg.threads = []
            config.save(cfg)
            _forget_all(store, now, tg, chat_id, FORGOT + (
                " I've stopped reading your texts. Run ./setup texts on your Mac to turn "
                "them back on."))
            return True
        tg.send(chat_id, "Okay, nothing deleted.")
        return False
    if kind == "practice":
        assignments.start_rehearsal(store, raw)
        # The answer also goes to the coach, so a real crisis in it is screened.
        _coach_reply(store, cfg, now, [text], run, "rehearsal")
        return True
    if kind == "journal":
        store.add_journal(text)
        _coach_reply(store, cfg, now, [text], run, "journal")
        return True
    return False


def _coach_reply(store, cfg, now, incoming, run, mode):
    """A coach reply goes to the outbox in the same transaction as its turns, so a
    Telegram blip (or dying mid-way) never loses it; the loop delivers it."""
    with store.atomic():
        r = coach.respond(store, cfg, now, incoming, run=run, mode=mode)
        outbox.enqueue(store, r.reply, now)
    return r


def _forget_all(store, now, tg, chat_id, done_text) -> None:
    store.forget("all", now)
    profile.write("")
    paths.profile_path().with_name("profile.prev.md").unlink(missing_ok=True)
    paths.crisis_log_path().unlink(missing_ok=True)
    tg.send(chat_id, done_text)


# ---- commands --------------------------------------------------------------

def _help(args, store, cfg, now, tg, chat_id, run):
    tg.send(chat_id, HELP)


def _privacy(args, store, cfg, now, tg, chat_id, run):
    counts = ", ".join(f"{k}: {v}" for k, v in store.stats().items())
    names = ", ".join(t["name"] for t in cfg.threads) or "none"
    if cfg.threads and store.get_meta("texts_unavailable") == "1":
        names += " (not readable right now)"
    tg.send(chat_id, (
        f"Everything lives on this Mac, in {paths.shown_data_dir()}\n\n"
        f"Stored now: {counts}\n"
        f"Text threads I read: {names}\n"
        f"AI: {config.provider_name(cfg)} ({config.active_model(cfg)})\n\n{disclosure(cfg)}\n\n"
        "/forget last, /forget day or /forget all deletes it."))


def _profile(args, store, cfg, now, tg, chat_id, run):
    body = profile.read().strip()
    if not body:
        tg.send(chat_id, "Nothing in my notes yet. They fill in as we talk.")
    elif len(body) > DOC_LIMIT:
        tg.send_document(chat_id, "profile.md", body.encode("utf-8"))
    else:
        tg.send(chat_id, body)


def _practice(args, store, cfg, now, tg, chat_id, run):
    _await(store, "practice", now)
    tg.send(chat_id, "who, and what's the situation?")


def _done(args, store, cfg, now, tg, chat_id, run):
    if current_mode(store) != "rehearsal":
        tg.send(chat_id, "There's no practice running. /practice starts one.")
        return
    # Debrief first: if the call fails, the rehearsal stays open and /done can be retried.
    with store.atomic():
        _coach_reply(store, cfg, now, [
            "/done. The practice is over, so you're the coach again. Give me an honest "
            "debrief: what worked, what didn't, and one sentence to try in the real "
            "conversation."], run, "chat")
        assignments.finish_rehearsal(store, now)


def _journal(args, store, cfg, now, tg, chat_id, run):
    _await(store, "journal", now)
    tg.send(chat_id, "Go ahead. Your next message goes in your journal.")


def _quieter(args, store, cfg, now, tg, chat_id, run):
    cfg.checkin_cap = max(0, cfg.checkin_cap - 1)
    config.save(cfg)
    tg.send(chat_id, f"Okay. Up to {cfg.checkin_cap} check-ins a day.")


def _more(args, store, cfg, now, tg, chat_id, run):
    cfg.checkin_cap = min(CAP_MAX, cfg.checkin_cap + 1)
    config.save(cfg)
    tg.send(chat_id, f"Okay. Up to {cfg.checkin_cap} check-ins a day.")


def _pause(args, store, cfg, now, tg, chat_id, run):
    try:
        hours = float(args[0]) if args else DEFAULT_PAUSE_H
        if not 0 < hours <= 24 * 365:
            raise ValueError
    except ValueError:
        tg.send(chat_id, "Use /pause or /pause 6 (hours).")
        return
    until = now + timedelta(hours=hours)
    cfg.paused_until = until.isoformat()
    config.save(cfg)
    tg.send(chat_id, f"Paused. No check-ins until {until.astimezone():%a %H:%M}. "
                     "You can still text me any time.")


def _export(args, store, cfg, now, tg, chat_id, run):
    out = [f"# Tilia export, {now.astimezone():%Y-%m-%d %H:%M}", "", "## Conversation", ""]
    out += [f"- {t.at} {'you' if t.role == 'user' else 'tilia'}: {t.text}"
            for t in store.recent_turns(10**9)] or ["(none)"]
    out += ["", "## Journal", ""]
    out += [f"- {at}: {t}" for at, t in store.journal_entries()] or ["(none)"]
    out += ["", "## Assignments", ""]
    out += [f"- #{a.id} [{a.kind}] {a.text} (created {a.created}, due {a.due or 'no date'}, "
            f"{a.status})" for a in store.all_assignments()] or ["(none)"]
    out += ["", "## Profile", "", profile.read().strip() or "(empty)", ""]
    tg.send_document(chat_id, f"tilia-export-{now.astimezone():%Y-%m-%d}.md",
                     "\n".join(out).encode("utf-8"))


def _forget(args, store, cfg, now, tg, chat_id, run):
    scope = args[0].lower() if args else ""
    if scope == "all":
        _await(store, "forget_all", now)
        tg.send(chat_id, "This deletes everything: our conversation, journal, assignments, "
                         "text copies, my notes on you and the crisis log. If I'm reading "
                         "your texts, I'll then ask whether to keep reading them; if I do, "
                         "they're copied again from scratch. type FORGET to confirm.")
    elif scope in ("last", "day"):
        store.forget(scope, now)
        tg.send(chat_id, "Deleted " + ("the last exchange." if scope == "last" else "today."))
    else:
        tg.send(chat_id, "Use /forget last, /forget day or /forget all.")


_COMMANDS = {"/help": _help, "/start": _help, "/privacy": _privacy, "/profile": _profile,
             "/practice": _practice, "/done": _done, "/journal": _journal,
             "/quieter": _quieter, "/more": _more, "/pause": _pause, "/export": _export,
             "/forget": _forget}
