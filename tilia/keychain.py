"""Secrets in the macOS Keychain: the Telegram token and the candle relay's key and secret.
They never touch disk, and a value never goes into argv (only `security -i`'s stdin)."""
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


_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-.:+/=]{1,8192}$")


def get_secret(service: str, run=subprocess.run) -> str:
    try:
        r = run(["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{service} lookup timed out (Keychain locked?)") from e
    value = (r.stdout or "").strip()
    if r.returncode != 0 or not value:
        raise RuntimeError(f"{service} missing from Keychain")
    return value


def set_secret(service: str, value: str, run=subprocess.run) -> None:
    """Store (or replace) one value, by stdin only. `security -i` reads one command per
    line, split on spaces, so a value must be one plain token: no whitespace or quotes."""
    if not re.fullmatch(r"[a-z-]{1,40}", service or ""):
        raise ValueError("bad Keychain service name")
    if not _SAFE_RE.fullmatch(value or ""):
        raise ValueError("That value can't go into the Keychain safely")
    cmd = f"add-generic-password -U -s {service} -a tilia -w {value}\n"
    try:
        r = run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Saving {service} timed out (Keychain locked?)") from e
    if r.returncode != 0:
        raise RuntimeError(f"Could not save {service} to Keychain")


def delete_token(run=subprocess.run) -> None:
    run(["security", "delete-generic-password", "-s", SERVICE],
        capture_output=True, text=True, timeout=10)
