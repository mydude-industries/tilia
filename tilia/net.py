"""The only network module. Every outbound request goes through here: to
api.telegram.org over https, and to the candle relay the owner set up with ./setup relay
(https; http only to this Mac itself, for development). An auditor can grep for network
imports and should find this file alone."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

ALLOWED_HOST = "api.telegram.org"
_BOT = re.compile(r"bot[^/\s]+")


class NetError(Exception):
    """Network failure. The message never contains the URL or token. `code` is the HTTP
    status when the server answered with an error, else None."""

    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


def redact(text: str) -> str:
    return _BOT.sub("bot<redacted>", text)


def _check(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname != ALLOWED_HOST or u.username or u.password:
        raise ValueError("only https://api.telegram.org is allowed")
    if u.port not in (None, 443):
        raise ValueError("only https://api.telegram.org is allowed")


def _send(req, timeout, opener) -> dict:
    """The JSON answer. An HTTP error status whose body is a JSON object is an answer too
    (Telegram refuses with 400/403/429 and {"ok": false, ...}), so the caller can tell a
    refusal from no answer at all; anything else raises NetError."""
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"")
        except Exception:
            body = None
        if isinstance(body, dict):
            return body
        raise NetError(f"request failed: HTTPError {e.code}") from None
    except Exception as e:
        # Never chain: the original exception text can embed the URL (token).
        raise NetError(f"request failed: {type(e).__name__}") from None


def post(url: str, params: dict, timeout=10, opener=urllib.request.urlopen) -> dict:
    _check(url)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode())
    return _send(req, timeout, opener)


def post_multipart(url: str, fields: dict, files: dict, timeout=30,
                   opener=urllib.request.urlopen) -> dict:
    _check(url)
    boundary = uuid.uuid4().hex
    b = boundary.encode()
    body = b""
    for k, v in fields.items():
        body += (b"--" + b + b'\r\nContent-Disposition: form-data; name="'
                 + str(k).encode() + b'"\r\n\r\n' + str(v).encode() + b"\r\n")
    for k, (filename, data, ctype) in files.items():
        body += (b"--" + b + b'\r\nContent-Disposition: form-data; name="' + str(k).encode()
                 + b'"; filename="' + filename.replace('"', "").encode()
                 + b'"\r\nContent-Type: ' + ctype.encode() + b"\r\n\r\n" + data + b"\r\n")
    body += b"--" + b + b"--\r\n"
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return _send(req, timeout, opener)


# ---- the candle relay (tilia/relay.py) --------------------------------------------------

_LOCAL = ("127.0.0.1", "localhost", "::1")


def check_relay_url(url: str) -> str:
    """The relay's base address without a trailing slash. https only (http just for this
    Mac itself); no query, fragment, login or whitespace."""
    raw = (url or "").strip()
    if not raw or any(c.isspace() for c in raw) or "?" in raw or "#" in raw:
        raise ValueError("not a relay address")
    u = urllib.parse.urlsplit(raw)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password:
        raise ValueError("not a relay address")
    if u.scheme == "http" and u.hostname not in _LOCAL:
        raise ValueError("the relay must be https")
    return raw.rstrip("/")


def relay_call(base: str, method: str, path: str, secret: str, body: dict | None = None,
               timeout=15, opener=urllib.request.urlopen, max_bytes=256 * 1024):
    """One request to the relay with this Mac's bearer secret. Returns the JSON answer
    (None for an empty body, e.g. 204). Any failure is NetError (with .code for an HTTP
    error status); the message never holds the URL, the secret or a body."""
    base = check_relay_url(base)
    if not path.startswith("/v1/"):
        raise ValueError("not a relay route")
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {secret}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise NetError(f"relay: HTTPError {e.code}", code=e.code) from None
    except Exception as e:
        raise NetError(f"relay: {type(e).__name__}") from None
    if len(raw) > max_bytes:
        raise NetError("relay: answer too big")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise NetError("relay: not JSON") from None
