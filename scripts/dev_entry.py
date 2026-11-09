"""Dev-only (never run by Tilia, not part of the `tilia` package): worksheets, cards and
blots end to end, without Telegram (except `button`).

    export TILIA_HOME=$(mktemp -d)
    uv run python scripts/dev_entry.py offer check_story --focus "Riley went quiet" --provider claude
    uv run python scripts/dev_entry.py answer <TOKEN> --answers '[...]' --provider claude
    uv run python scripts/dev_entry.py ask "can you do a tarot reading for me?" --provider chatgpt
    uv run python scripts/dev_entry.py card-after-crisis --provider claude
    uv run python scripts/dev_entry.py button "<url>"       # Task 14: real bot, real chat

Everything but `button` refuses to run unless TILIA_HOME is a throwaway folder (not the
real data folder or anything inside it), and seeds a made-up profile (Sam, partner Riley).
`--provider claude` uses your own logged-in `claude`. `--provider chatgpt` runs the `codex`
on your PATH with a dev-only Codex login of its own, in ~/.tilia-dev/codex-home (outside the
data folder and the repo); log in there once with
    CODEX_HOME=~/.tilia-dev/codex-home codex login
Nothing here reads your own ~/.codex.
`button` reads only the Keychain token and config.chat_id, and sends one keyboard button.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from tilia import checkins, coach, config, entries, paths, profile, webapp, worksheets
from tilia.store import Store, local_now

DEV_CODEX_HOME = Path.home() / ".tilia-dev" / "codex-home"
LOGIN_HINT = "CODEX_HOME=~/.tilia-dev/codex-home codex login"

PROFILE = """# Sam
- Sam, 34, lives with his partner Riley (together 6 years).
- Goal: stay calm and curious when Riley goes quiet, instead of assuming the worst.
- Pattern: when Riley is quiet, Sam reads it as anger at him and over-apologizes.
- Likes plain talk; dislikes being lectured.
- Works in logistics; Sundays are dinner with Riley's family.
"""


def _throwaway_home() -> None:
    raw = os.environ.get("TILIA_HOME")
    if not raw:
        raise SystemExit("Set TILIA_HOME to a throwaway folder first: export TILIA_HOME=$(mktemp -d)")
    real = Path(paths._DEFAULT).expanduser().resolve()
    p = Path(raw).expanduser().resolve()
    if p == real or real in p.parents:
        raise SystemExit("TILIA_HOME is the real data folder or inside it; use a throwaway one.")


def _setup(provider: str):
    """The throwaway folder's config and made-up profile; the ChatGPT login copy if asked."""
    _throwaway_home()
    os.umask(0o077)
    cfg = config.load()
    cfg.provider = provider
    cfg.goal = cfg.goal or "stay calm and curious when Riley goes quiet"
    config.save(cfg)
    if not profile.read().strip():
        profile.write(PROFILE)
    if provider == "chatgpt":
        _dev_codex()
    return cfg, Store(paths.db_path())


def _dev_codex() -> None:
    """Point the throwaway folder's bin/codex at the codex on PATH, and Tilia's codex home
    at the dev-only login in DEV_CODEX_HOME. Real installs use ./setup ai instead."""
    if not os.path.lexists(paths.codex_bin()):
        found = shutil.which("codex")
        if not found:
            raise SystemExit("No codex on PATH.")
        paths.codex_bin().symlink_to(found)
    DEV_CODEX_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DEV_CODEX_HOME, 0o700)
    if not (DEV_CODEX_HOME / "auth.json").is_file():
        print(LOGIN_HINT)
        raise SystemExit(1)
    paths.codex_home = lambda: DEV_CODEX_HOME  # this process only


def _show_offer(store, offer) -> None:
    e = store.entry_by_token(offer.token)
    print(f"[entry] type={e.type} token={e.token} card={e.card} seed={e.seed}")
    print("[wording]", json.dumps(e.wording, ensure_ascii=False) if e.wording else "(generic)")
    if e.wording:
        for k, v in (e.wording.get("prompts") or {}).items():
            print(f"  {k} ({len(v)} chars): {v}")
        for k, opts in (e.wording.get("options") or {}).items():
            print(f"  taps {k}: " + " | ".join(f"{o} ({len(o)})" for o in opts))
    print("[url]", offer.url)
    print("[sealed read]", e.sealed_read if e.sealed_read is not None else "(none)")


def offer(type_: str, focus, provider: str, run=subprocess.run) -> int:
    cfg, store = _setup(provider)
    o = worksheets.prepare(store, cfg, local_now(), type_, focus=focus, source="ask", run=run)
    if o is None:
        print("no offer: that sheet can't be read")
        return 1
    _show_offer(store, o)
    return 0


def answer(token: str, answers: str, provider: str, run=subprocess.run) -> int:
    cfg, store = _setup(provider)
    e = store.entry_by_token(token)
    if e is None:
        print("no entry with that token")
        return 1
    now = local_now()
    payload = json.dumps({"v": 1, "type": e.type, "id": token, "answers": json.loads(answers)})
    status = worksheets.accept(store, now, payload)
    print(f"[accept] {status}")
    if status != "ok":
        return 1
    with store.atomic():
        r = worksheets.answer(store, cfg, now, store.get_entry(e.id), run=run)
    print(f"[state={r.state}] [themes={r.themes}]")
    print(r.reply)
    return 0


def ask(text: str, provider: str, run=subprocess.run) -> int:
    """One plain chat message through the normal coach turn: does it offer a worksheet?"""
    cfg, store = _setup(provider)
    r = coach.respond(store, cfg, local_now(), [text], run=run)
    print(f"[state={r.state}]")
    print(r.reply)
    if r.offer is None:
        print("[offer] none")
        return 0
    _show_offer(store, r.offer)
    return 0


def card_after_crisis(provider: str, run=subprocess.run) -> int:
    """An earlier chat, a crisis episode that ended 2 days ago, then maybe_send: no card
    may go out, and no AI call may be made."""
    cfg, store = _setup(provider)
    now = local_now()
    cfg.chat_id = cfg.chat_id or 1555010000  # paired, in memory only
    cfg.quiet_start = cfg.quiet_end = 0      # never quiet hours for this check
    if not store.recent_turns(1):
        earlier = Store(paths.db_path(), clock=lambda: now - timedelta(hours=2))
        earlier.add_turn("user", "Back from a walk, feeling okay.")
        earlier.add_turn("coach", "Good. What did the walk do for you?", "ok")
    before = [c.reason for c in checkins.candidates(store, cfg, now)]
    print(f"[candidates before the crisis mark] {before}")
    store.set_meta(coach._CRISIS_LAST, (now - timedelta(days=2)).isoformat())
    print(f"[candidates after] {[c.reason for c in checkins.candidates(store, cfg, now)]}")
    calls = []

    def counted(cmd, **kw):
        calls.append(cmd[0])
        return run(cmd, **kw)
    sent = checkins.maybe_send(store, cfg, now, run=counted)
    cards = store.entries(types=entries.CARD_TYPES)
    print(f"[card sent] {sent and bool(cards)}  [check-in sent] {sent}  [ai calls] {len(calls)}")
    return 0


def button(url: str) -> int:
    from tilia import keychain
    from tilia.telegram import Telegram
    cfg = config.load()
    if cfg.chat_id is None:
        print("Not paired: no chat_id in config.toml.")
        return 1
    ok = Telegram(keychain.get_token()).send_webapp_button(
        cfg.chat_id, "Dev check: tap Open.", webapp.LABEL, url)
    print("sent" if ok else "not sent")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/dev_entry.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_provider(p):
        p.add_argument("--provider", choices=["claude", "chatgpt"], default="claude")
        return p
    o = with_provider(sub.add_parser("offer"))
    o.add_argument("type", choices=entries.TYPES)
    o.add_argument("--focus")
    a = with_provider(sub.add_parser("answer"))
    a.add_argument("token")
    a.add_argument("--answers", required=True, help="a JSON list, one per answer card")
    k = with_provider(sub.add_parser("ask"))
    k.add_argument("text")
    with_provider(sub.add_parser("card-after-crisis"))
    b = sub.add_parser("button")
    b.add_argument("url")
    x = ap.parse_args(argv)
    if x.cmd == "offer":
        return offer(x.type, x.focus, x.provider)
    if x.cmd == "answer":
        return answer(x.token, x.answers, x.provider)
    if x.cmd == "ask":
        return ask(x.text, x.provider)
    if x.cmd == "card-after-crisis":
        return card_after_crisis(x.provider)
    return button(x.url)


if __name__ == "__main__":
    raise SystemExit(main())
