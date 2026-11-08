"""Dev-only: send one message to the coach and print the reply. No Telegram.

    TILIA_HOME=$(mktemp -d) uv run python -m tilia.dev_say "had a rough day" \
        --profile /path/outside/repo/fake.md --referrals never --tone direct_soft

Uses the real `claude` CLI under your login and the data folder TILIA_HOME points at.
"""

import argparse
from datetime import datetime

from . import coach, config, paths, profile
from .store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tilia.dev_say")
    ap.add_argument("message", nargs="+", help="one or more messages, sent as one batch")
    ap.add_argument("--profile", help="seed profile.md from this file first")
    ap.add_argument("--goal")
    ap.add_argument("--tone", choices=["very_direct", "direct_soft", "gentle"])
    ap.add_argument("--referrals", choices=["never", "gently"])
    ap.add_argument("--model")
    ap.add_argument("--mode", default="chat", choices=["chat", "journal", "rehearsal"])
    a = ap.parse_args(argv)

    cfg = config.load()
    for k in ("goal", "tone", "referrals", "model"):
        if getattr(a, k) is not None:
            setattr(cfg, k, getattr(a, k))
    if a.profile:
        profile.import_file(a.profile)
    store = Store(paths.db_path())
    r = coach.respond(store, cfg, datetime.now().astimezone(), a.message, mode=a.mode)
    print(f"[state={r.state}]")
    print(r.reply)
    if r.new_assignment:
        print(f"[new_assignment={r.new_assignment}]")
    if r.remember:
        print(f"[remember={r.remember}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
