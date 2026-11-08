"""Telegram Bot API client. All network access goes through tilia.net."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from tilia import net

LIMIT = 3500


@dataclass(frozen=True)
class Update:
    update_id: int
    chat_id: int
    text: str | None
    kind: str  # "text" | "unsupported"
    chat_type: str | None = None  # "private" | "group" | "supergroup" | "channel"
    sender_name: str = ""  # Telegram first name of the sender, if given
    sender_username: str = ""  # without the @


def split_reply(text: str, limit: int = LIMIT) -> list[str]:
    """Split on sentence boundaries into parts of at most `limit` characters.
    A single sentence longer than the limit is hard-cut."""
    if len(text) <= limit:
        return [text]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
    parts: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(s[:limit])
            s = s[limit:]
        if not s:
            continue
        if not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= limit:
            cur += " " + s
        else:
            parts.append(cur)
            cur = s
    if cur:
        parts.append(cur)
    return parts


def _log(what: str, e: Exception) -> None:
    # Type name only: never the message, which could carry the token.
    print(f"tilia: telegram {what} failed: {type(e).__name__}", file=sys.stderr)


class Telegram:
    def __init__(self, token: str, post=net.post):
        self._token = token
        self._post = post

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _call(self, method: str, params: dict, timeout: int = 10) -> dict:
        return self._post(self._url(method), params, timeout=timeout)

    def get_me(self) -> dict | None:
        try:
            body = self._call("getMe", {})
            return body.get("result") if body.get("ok") else None
        except Exception as e:
            _log("get_me", e)
            return None

    def updates(self, offset: int | None) -> list[Update]:
        """New updates; never raises ([] on failure). No long-poll: the loop ticks."""
        try:
            params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
            if offset is not None:
                params["offset"] = offset
            body = self._call("getUpdates", params)
            if not body.get("ok"):
                return []
            out = []
            for u in body.get("result", []):
                try:
                    m = u.get("message")
                    if not m:
                        continue
                    text = m.get("text")
                    sender = m.get("from") or {}
                    out.append(Update(int(u["update_id"]), int(m["chat"]["id"]),
                                      text if text is not None else None,
                                      "text" if text is not None else "unsupported",
                                      chat_type=m["chat"].get("type"),
                                      sender_name=str(sender.get("first_name") or ""),
                                      sender_username=str(sender.get("username") or "")))
                except (KeyError, TypeError, ValueError, AttributeError) as e:
                    _log("updates (one malformed update skipped)", e)
            return out
        except Exception as e:
            _log("updates", e)
            return []

    def send(self, chat_id, text: str) -> bool:
        try:
            for part in split_reply(text):
                body = self._call("sendMessage", {"chat_id": chat_id, "text": part})
                if not body.get("ok", True):
                    return False
            return True
        except Exception as e:
            _log("send", e)
            return False

    def typing(self, chat_id) -> bool:
        """Show "typing…" in the chat for ~5 s. Never raises."""
        try:
            body = self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"},
                              timeout=5)
            return bool(body.get("ok", True))
        except Exception as e:
            _log("typing", e)
            return False

    def send_document(self, chat_id, filename: str, data: bytes) -> bool:
        try:
            body = net.post_multipart(
                self._url("sendDocument"), {"chat_id": chat_id},
                {"document": (filename, data, "application/octet-stream")})
            return bool(body.get("ok", True))
        except Exception as e:
            _log("send_document", e)
            return False
