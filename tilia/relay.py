"""The Mac's side of the candle relay (relay/tilia_relay.py).

All network calls go through net.relay_call. A daemon thread long-polls `GET /v1/pending/<mac_id>` with this Mac's secret, hands each
ask to voice.answer_ask (one at a time, each with its own Store), and posts the sealed
answer, or `{}` for no answer, to `POST /v1/answer/<ask_id>`. Network errors back off 5 s
doubling to 60 s; nothing the handler or the network does kills the thread.

The private key and the secret live only in the Keychain (`tilia-relay-key`,
`tilia-relay-secret`); the config holds the relay's address, this Mac's id and the public
key, which the candle button carries. Logs name outcomes and error types, never a body."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import string
import threading
import time
from datetime import datetime
from typing import Callable

from . import config, keychain, net, paths
from .store import Store, local_now

KEY_SERVICE = "tilia-relay-key"
SECRET_SERVICE = "tilia-relay-secret"
WAIT_S = 25           # the relay's long-poll cap
SOCKET_SLACK_S = 15   # the socket outlives the long-poll
BACKOFF_MIN_S, BACKOFF_MAX_S = 5, 60
MAX_BODY = 256 * 1024  # 20 queued asks of 4 KB each, with room
ASK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAC_ID = re.compile(r"[A-Za-z0-9_-]{6,32}")


def log(msg: str) -> None:
    import sys
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} tilia: relay: {msg}", file=sys.stderr, flush=True)


# ---- values ----------------------------------------------------------------------------

def check_url(url: str) -> str:
    """The relay's base address without a trailing slash; ValueError if it's not https
    (http only for this Mac itself, for development). See net.check_relay_url."""
    return net.check_relay_url(url)


def new_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def new_id() -> str:
    abc = string.ascii_letters + string.digits
    return "".join(secrets.choice(abc) for _ in range(10))


def server_line(mac_id: str, secret: str) -> str:
    """What goes into the server's TILIA_RELAY_MACS: the id and the secret's sha256."""
    return f"{mac_id}:{hashlib.sha256(secret.encode('utf-8')).hexdigest()}"


def encode_pem(pem: str) -> str:
    """A PEM is several lines and `security -i` reads one command per line: one base64url
    line for the Keychain."""
    return base64.urlsafe_b64encode(pem.encode("ascii")).decode("ascii").rstrip("=")


def decode_pem(text: str) -> str:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("ascii")


# ---- the client ------------------------------------------------------------------------

class RelayClient:
    def __init__(self, base_url: str, mac_id: str, secret: str,
                 handle: Callable[[str, str], str | None],
                 urlopen=None, sleep=time.sleep,
                 on_seen: Callable[[], None] | None = None):
        self.base = base_url.rstrip("/")
        self.mac_id, self._secret = mac_id, secret
        self.handle, self.sleep = handle, sleep
        self.urlopen = urlopen  # None: net.py's own (urllib); tests pass a fake
        self.on_seen = on_seen

    def _call(self, method: str, path: str, body: dict | None = None, timeout=SOCKET_SLACK_S):
        kw = {} if self.urlopen is None else {"opener": self.urlopen}
        return net.relay_call(self.base, method, path, self._secret, body, timeout=timeout,
                              max_bytes=MAX_BODY, **kw)

    def _pending(self) -> list[dict]:
        body = self._call("GET", f"/v1/pending/{self.mac_id}?wait={WAIT_S}",
                          timeout=WAIT_S + SOCKET_SLACK_S)
        asks = body.get("asks") if isinstance(body, dict) else None
        if not isinstance(asks, list):
            raise ValueError("pending: no asks list")
        return asks

    def _answer(self, ask_id: str, sealed: str | None) -> None:
        body = {"sealed": sealed} if isinstance(sealed, str) and sealed else {}
        try:
            self._call("POST", f"/v1/answer/{ask_id}", body)
        except net.NetError as e:  # 404: the ask expired on the relay
            log(f"answer not taken ({e.code or 'no answer'})")
        except Exception as e:
            log(f"answer not sent ({type(e).__name__})")

    def _one(self, ask) -> None:
        if not (isinstance(ask, dict) and isinstance(ask.get("ask_id"), str)
                and ASK_ID.fullmatch(ask["ask_id"]) and isinstance(ask.get("entry"), str)
                and isinstance(ask.get("sealed"), str)):
            log("skipped a malformed ask")
            return
        try:
            sealed = self.handle(ask["entry"], ask["sealed"])
        except Exception as e:  # never the message: it may hold what the ask said
            log(f"handler failed ({type(e).__name__}); answering empty")
            sealed = None
        self._answer(ask["ask_id"], sealed)

    def poll_once(self) -> None:
        """One long-poll and the answers to what it brought. Raises on a failed poll."""
        asks = self._pending()
        if self.on_seen is not None:
            try:
                self.on_seen()
            except Exception as e:
                log(f"could not note the poll ({type(e).__name__})")
        for ask in asks:
            self._one(ask)

    def run_forever(self) -> None:
        delay = BACKOFF_MIN_S
        while True:
            try:
                self.poll_once()
            except Exception as e:
                code = getattr(e, "code", None)
                log(f"poll failed ({type(e).__name__}{f' {code}' if code else ''}); "
                    f"retrying in {delay} s")
                self.sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)
                continue
            delay = BACKOFF_MIN_S


# ---- wiring ----------------------------------------------------------------------------

def make_handler(private_pem: str) -> Callable[[str, str], str | None]:
    """answer_ask with a Store of its own (WAL; never the loop's connection), closed after."""
    def handle(entry: str, sealed: str) -> str | None:
        from . import voice
        store = Store(paths.db_path())
        try:
            return voice.answer_ask(store, config.load(), local_now(), private_pem, entry,
                                    sealed)
        finally:
            store.close()
    return handle


SEEN_EVERY_S = 300


def mark_seen() -> None:
    store = Store(paths.db_path())
    try:
        store.set_meta("relay_seen", local_now().isoformat(timespec="seconds"))
    finally:
        store.close()


def _seen_throttled(clock=time.monotonic) -> Callable[[], None]:
    last = [None]

    def seen():
        t = clock()
        if last[0] is None or t - last[0] >= SEEN_EVERY_S:
            mark_seen()
            last[0] = t
    return seen


def start(cfg, get_secret=keychain.get_secret, thread=threading.Thread):
    """Start the relay thread if this Mac is set up for it; None (logged) if not."""
    if not (cfg.relay_url and cfg.relay_id):
        return None
    try:
        url = check_url(cfg.relay_url)
        if not MAC_ID.fullmatch(cfg.relay_id):
            raise ValueError("bad relay_id")
        pem = decode_pem(get_secret(KEY_SERVICE))
        secret = get_secret(SECRET_SERVICE)
    except (RuntimeError, ValueError) as e:
        log(f"not started ({type(e).__name__}). Run ./setup relay.")
        return None
    client = RelayClient(url, cfg.relay_id, secret, make_handler(pem),
                         on_seen=_seen_throttled())
    t = thread(target=client.run_forever, name="tilia-relay", daemon=True)
    t.start()
    log("listening")
    return t
