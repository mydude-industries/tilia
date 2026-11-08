"""Telegram token storage in the macOS Keychain. The token never touches disk."""
from __future__ import annotations

import re
import subprocess

SERVICE = "tilia-telegram"


def get_token(run=subprocess.run) -> str:
    try:
        r = run(["security", "find-generic-password", "-s", SERVICE, "-w"],
                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Telegram token lookup timed out (Keychain locked?)") from e
    token = (r.stdout or "").strip()
    if r.returncode != 0 or not token:
        raise RuntimeError("Telegram token missing from Keychain")
    return token


_TOKEN_RE = re.compile(r"^[0-9]{5,12}:[A-Za-z0-9_-]{20,64}$")


def set_token(token: str, run=subprocess.run) -> None:
    """Store the token. It goes to `security -i` on stdin, never into argv
    (argv is visible to other local processes via ps)."""
    if not _TOKEN_RE.fullmatch(token or ""):
        raise ValueError("That does not look like a Telegram bot token")
    cmd = f"add-generic-password -U -s {SERVICE} -a tilia -w {token}\n"
    try:
        r = run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Saving the Telegram token timed out (Keychain locked?)") from e
    if r.returncode != 0:
        raise RuntimeError("Could not save the Telegram token to Keychain")


def delete_token(run=subprocess.run) -> None:
    run(["security", "delete-generic-password", "-s", SERVICE],
        capture_output=True, text=True, timeout=10)
