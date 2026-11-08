"""Hardened `claude -p` runner and the coach reply contract."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from . import paths

TIMEOUT_S = 120

_LIMIT_RE = re.compile(r"usage limit|rate[ _]limit|limit reached", re.I)
_AUTH_RE = re.compile(r"login|not authenticated|/login|invalid api key", re.I)
_RESET_RE = re.compile(
    r"(?:resets?(?:\s+at)?|try again(?:\s+at)?)\s+([0-9][^.\n]{0,40}?(?:[ap]m|\d{1,2}:\d{2})[^.\n]{0,20}?)(?=[.\n]|$)",
    re.I)


class LimitError(Exception):
    def __init__(self, message: str = "", reset_hint: str = ""):
        super().__init__(message)
        self.reset_hint = reset_hint or _parse_reset(message)


class NotLoggedIn(Exception):
    pass


class MissingCLI(Exception):
    pass


class UnsupportedCLI(Exception):
    """The AI command is a version Tilia has not checked (codex only, for now)."""


def _parse_reset(text: str) -> str:
    m = _RESET_RE.search(text or "")
    return m.group(0).strip() if m else ""


def _attempt(prompt: str, model: str, system: str, run, timeout: int, cwd: str) -> str | None:
    """One claude call. Returns the raw result text, or None if the envelope is malformed."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
           "--safe-mode", "--strict-mcp-config", "--no-session-persistence",
           # cwd is an empty private folder, so this loads no settings at all: never
           # ~/.claude/settings.json (its env, apiKeyHelper or hooks could redirect or watch
           # every call), and never a .claude/ someone planted in a shared /tmp
           "--setting-sources", "project",
           "--system-prompt", system]
    try:
        proc = run(cmd, input=prompt, capture_output=True, text=True,
                   timeout=timeout, cwd=cwd)
    except FileNotFoundError as e:
        raise MissingCLI("the `claude` command was not found") from e
    out, err = proc.stdout or "", proc.stderr or ""
    try:
        outer = json.loads(out)
    except ValueError:
        outer = None
    is_err = proc.returncode != 0 or (isinstance(outer, dict) and outer.get("is_error"))
    if is_err:
        blob = err + " " + (str(outer.get("result", "")) if isinstance(outer, dict) else out)
        if isinstance(outer, dict) and outer.get("api_error_status") == 429:
            raise LimitError(blob.strip()[:300])
        if _LIMIT_RE.search(blob):
            raise LimitError(blob.strip()[:300])
        if _AUTH_RE.search(blob):
            raise NotLoggedIn(blob.strip()[:300])
        raise RuntimeError(f"claude failed (rc={proc.returncode}): {blob.strip()[:300]}")
    if not isinstance(outer, dict) or not isinstance(outer.get("result"), str):
        return None
    return outer["result"]


def _call_with_retry(prompt: str, model: str, system: str, run, timeout: int,
                     cwd: str) -> str | None:
    """Retry once on timeout/failure; limit, auth and missing-CLI errors propagate at once."""
    for attempt in range(2):
        try:
            return _attempt(prompt, model, system, run, timeout, cwd)
        except (LimitError, NotLoggedIn, MissingCLI):
            raise
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
            if attempt == 1:
                raise
    return None  # unreachable


def run_claude(prompt: str, model: str, system: str, run=subprocess.run,
               timeout: int = TIMEOUT_S) -> str:
    """Raw result text. Raises LimitError, NotLoggedIn, MissingCLI, or RuntimeError."""
    cwd = str(paths.claude_cwd())  # raises RuntimeError (no call made) if it isn't private
    try:
        text = _call_with_retry(prompt, model, system, run, timeout, cwd)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"claude failed: {type(e).__name__}") from e
    if text is None:
        raise RuntimeError("claude returned an unparseable envelope")
    return text


def _extract_obj(text: str) -> dict | None:
    """First JSON object in text, tolerating code fences and leading prose."""
    if not isinstance(text, str):
        return None
    text = re.sub(r"```(?:json)?", "", text, flags=re.I)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


extract_obj = _extract_obj  # shared with ai.py, so both providers are read the same way


def ask_json(prompt: str, model: str, system: str, run=subprocess.run) -> dict | None:
    text = run_claude(prompt, model, system, run=run)
    return _extract_obj(text)


@dataclass
class CoachReply:
    reply: str
    state: str = "ok"
    new_assignment: dict | None = None
    close_assignment: int | None = None
    remember: str | None = None


def parse_coach(text: str) -> CoachReply | None:
    obj = _extract_obj(text)
    if obj is None:
        return None
    reply = obj.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    state = obj.get("state")
    if state not in ("ok", "struggling", "crisis"):
        state = "ok"
    na = obj.get("new_assignment")
    if not (isinstance(na, dict) and na.get("kind") in ("rehearsal", "challenge")
            and isinstance(na.get("text"), str) and na["text"].strip()):
        na = None
    ca = obj.get("close_assignment")
    if not isinstance(ca, int) or isinstance(ca, bool):
        ca = None
    rem = obj.get("remember")
    if not (isinstance(rem, str) and rem.strip()):
        rem = None
    return CoachReply(reply.strip(), state, na, ca, rem)
