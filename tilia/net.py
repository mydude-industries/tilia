"""The only network module. Every outbound request goes through here, and only
to api.telegram.org over https. An auditor can grep for network imports and
should find this file alone."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import uuid

ALLOWED_HOST = "api.telegram.org"
_BOT = re.compile(r"bot[^/\s]+")


class NetError(Exception):
    """Network failure. The message never contains the URL or token."""


def redact(text: str) -> str:
    return _BOT.sub("bot<redacted>", text)


def _check(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname != ALLOWED_HOST or u.username or u.password:
        raise ValueError("only https://api.telegram.org is allowed")
    if u.port not in (None, 443):
        raise ValueError("only https://api.telegram.org is allowed")


def _send(req, timeout, opener) -> dict:
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
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
