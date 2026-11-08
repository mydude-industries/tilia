"""python -m tilia run | import-profile <file> | status | probe-texts --out <file> [--name-file <f>]"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import config, paths, profile


def _run() -> int:
    from . import keychain, power
    from .loop import Loop, log
    from .store import Store
    from .telegram import Telegram

    cfg = config.load()
    store = Store(paths.db_path())
    try:
        token = keychain.get_token()
    except RuntimeError as e:
        log(f"no Telegram token: {type(e).__name__}. Run ./setup.")
        time.sleep(60)  # launchd restarts us; don't spin
        return 1
    if cfg.chat_id is None:
        log("not paired yet: every chat is ignored until ./setup pairs your phone")
    pw = power.Power(cfg.battery_awake)
    Loop(cfg, store, Telegram(token), power=pw, ac=power.on_ac).run_forever()
    return 0


def _import_profile(path: str) -> int:
    try:
        profile.import_file(path)
    except ValueError as e:
        print(f"Not imported: {e}", file=sys.stderr)
        return 1
    print(f"Imported into {paths.profile_path()}")
    return 0


def _status() -> int:
    from .store import Store

    cfg = config.load()
    store = Store(paths.db_path())
    last = store.recent_turns(1)
    print(f"data folder: {paths.data_dir()}")
    print(f"paired: {'yes' if cfg.chat_id is not None else 'no'}")
    print(f"provider: {config.provider_name(cfg)}")
    print(f"model: {config.active_model(cfg)}")
    print(f"threads: {len(cfg.threads)}")
    print(f"pending: {store.stats()['pending']}")
    print(f"last turn: {last[0].at if last else 'never'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tilia")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the loop (launchd does this)")
    imp = sub.add_parser("import-profile", help="seed profile.md from a file outside the repo")
    imp.add_argument("file")
    sub.add_parser("status", help="show what's set up, without any message content")
    pr = sub.add_parser("probe-texts", help="(setup uses this) can Tilia read Messages? "
                        "writes a count and matching chat names only, never message text")
    pr.add_argument("--out", required=True)
    pr.add_argument("--name-file", help="a file holding the contact name to look for")
    a = ap.parse_args(argv)
    # Every file this process makes (db sidecars, crisis.log, codex files) is 0600. agent.log
    # is opened by launchd, not by us: the plist's Umask covers it (install.sh fixes old ones).
    os.umask(0o077)
    paths.tmp_dir()  # the plist points TMPDIR here, so it must exist (0700)
    if a.cmd == "probe-texts":
        from .setup_wizard import probe_texts
        name = None
        if a.name_file:
            from pathlib import Path
            name = Path(a.name_file).read_text(encoding="utf-8").strip() or None
        probe_texts(a.out, name)
        return 0
    if a.cmd == "run":
        return _run()
    if a.cmd == "import-profile":
        return _import_profile(a.file)
    return _status()


if __name__ == "__main__":
    raise SystemExit(main())
