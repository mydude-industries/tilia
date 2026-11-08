#!/usr/bin/env python3
"""Refuse files that look like personal data. Stdlib only.

Flags: database files (and their -wal/-shm/-journal sidecars), Telegram bot tokens, real-looking phone numbers
(anything E.164 that is not +1555010xxxx), and files over 200 KB.
"""
import re
import sys
from pathlib import Path

MAX_BYTES = 200 * 1024
DB_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal",
               ".sqlite-wal", ".sqlite-shm")
TOKEN_RE = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")
PHONE_RE = re.compile(r"\+\d{11,15}")
FAKE_PHONE_RE = re.compile(r"\+1555010\d{4}")


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    if path.suffix.lower() in DB_SUFFIXES:
        problems.append("database file")
    try:
        size = path.stat().st_size
    except OSError:
        return problems
    if size > MAX_BYTES:
        problems.append(f"file over 200 KB ({size} bytes)")
        return problems
    text = path.read_bytes().decode("utf-8", errors="ignore")
    if TOKEN_RE.search(text):
        problems.append("looks like a Telegram bot token")
    for m in PHONE_RE.finditer(text):
        if not FAKE_PHONE_RE.fullmatch(m.group()):
            problems.append("real-looking phone number (only +1555010xxxx allowed)")
            break
    return problems


def main(paths: list[str]) -> int:
    bad = 0
    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue
        for problem in check_file(path):
            print(f"{p}: {problem}", file=sys.stderr)
            bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
