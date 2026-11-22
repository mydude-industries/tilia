#!/usr/bin/env python3
"""A small in-memory relay for the candle's voice. Standard library only.

The candle page hands over a sealed (already encrypted) blob; the owner's Mac picks it
up, answers with another sealed blob, and the page collects it. The relay never opens
anything: `sealed` is an opaque string. Everything lives in memory and expires after
120 s. Logs carry method, route name, status and duration, never a body or an id.
"""
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGES_ORIGIN = "https://mydude-industries.github.io"
TTL = 120.0
BODY_CAP = 4096
MAX_WAIT = 25.0
MAC_ID = re.compile(r"[A-Za-z0-9_-]{6,32}")
ENTRY = re.compile(r"[A-Za-z0-9_-]{12}")

ROUTES = ("ask", "answer", "pending")  # the only names that reach a log line

log = logging.getLogger("tilia.relay")


class State:
    """All mutable relay state, behind one lock with one Condition for waiters."""

    def __init__(self, macs, clock, rate_per_min, queue_cap):
        self.macs = macs
        self.clock = clock
        self.rate_per_min = rate_per_min
        self.queue_cap = queue_cap
        self.cond = threading.Condition()
        self.asks = {}    # ask_id -> dict(mac, entry, sealed, born, handed, answer, answered)
        self.hits = {}    # ip -> times of POST /v1/ask in the last minute

    def sweep(self, now):  # call with the lock held
        for ask_id in [i for i, a in self.asks.items()
                       if now - a["born"] > TTL or (a["answered"] and now - a["answered"] > TTL)]:
            del self.asks[ask_id]
        for ip in list(self.hits):
            self.hits[ip] = [t for t in self.hits[ip] if now - t < 60]
            if not self.hits[ip]:
                del self.hits[ip]

    def allow(self, ip, now):  # lock held; counts this attempt
        hits = self.hits.setdefault(ip, [])
        if len(hits) >= self.rate_per_min:
            return False
        hits.append(now)
        return True

    def queued(self, mac):  # lock held
        return sum(1 for a in self.asks.values() if a["mac"] == mac and not a["handed"])


def _wait_param(query):
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key == "wait":
            try:
                seconds = float(value)
            except ValueError:
                return 0.0
            if not math.isfinite(seconds):
                return 0.0
            return min(max(seconds, 0.0), MAX_WAIT)
    return 0.0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "tilia-relay"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------
    def setup(self):
        self.timeout = self.server.sock_timeout  # long-polls wait on the Condition, not the socket
        super().setup()

    def log_message(self, *args):  # the stock access log prints full paths; ours does not
        pass

    @property
    def st(self):
        return self.server.state

    def _client_ip(self):
        if self.server.trust_proxy:
            # Behind a trusted proxy: Cloudflare's own header, else the hop the proxy
            # appended (the last one; earlier hops are client-controlled).
            cf = self.headers.get("CF-Connecting-IP", "").strip()
            if cf:
                return cf
            last = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            if last:
                return last
        return self.client_address[0]

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and origin in self.server.origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, status, payload=None, cors=False, extra=()):
        self._status = status
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        if status != 204:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self._cors()
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if status != 204:
            self.wfile.write(body)

    def _read_json(self, cors=False):
        """The parsed JSON object body, or None after sending the error."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._send(400, {"error": "bad length"}, cors)
            return None
        if length > BODY_CAP:
            self.close_connection = True  # the oversized body is never read
            self._send(413, {"error": "too large"}, cors)
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send(400, {"error": "bad body"}, cors)
            return None
        return data

    def _mac_for_bearer(self):
        """The mac_id whose secret the Authorization header carries, else None."""
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) <= 7:
            return None
        digest = hashlib.sha256(header[7:].encode()).hexdigest()
        found = None
        for mac_id, expected in self.st.macs.items():  # no early exit
            if hmac.compare_digest(digest, expected) and found is None:
                found = mac_id
        return found

    def _dispatch(self, method):
        start = time.monotonic()
        self._status = 0
        route = "?"
        try:
            path, _, query = self.path.partition("?")
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "v1":
                if parts[1] in ROUTES:
                    route = f"/v1/{parts[1]}"
                    handler = getattr(self, f"_{method.lower()}_{parts[1]}", None)
                    if handler:
                        return handler(parts[2], query)
            self._send(404, {"error": "not found"})
        except (TimeoutError, socket.timeout):  # a client that went quiet mid-request
            self.close_connection = True
        except Exception:  # never let one request take the thread down
            log.exception("relay error")
            if not self._status:
                self._send(500, {"error": "server error"})
        finally:
            log.info("%s %s %s %.0fms", method, route, self._status,
                     (time.monotonic() - start) * 1000)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    # -- preflight for the page routes -----------------------------------
    def _options_ask(self, _key, _query):
        self._preflight()

    def _options_answer(self, _key, _query):
        self._preflight()

    def _preflight(self):
        self._send(204, extra=(("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                               ("Access-Control-Allow-Headers", "Content-Type"),
                               ("Access-Control-Max-Age", "600")), cors=True)

    # -- the page: POST /v1/ask/<mac>, GET /v1/answer/<ask_id> -----------
    def _post_ask(self, mac_id, _query):
        now = self.st.clock()
        with self.st.cond:
            self.st.sweep(now)
            if not self.st.allow(self._client_ip(), now):
                self.close_connection = True
                return self._send(429, {"error": "slow down"}, cors=True)
        data = self._read_json(cors=True)
        if data is None:
            return
        if not MAC_ID.fullmatch(mac_id) or mac_id not in self.st.macs:
            return self._send(404, {"error": "unknown"}, cors=True)
        entry, sealed = data.get("entry"), data.get("sealed")
        if (not isinstance(entry, str) or not ENTRY.fullmatch(entry)
                or not isinstance(sealed, str) or not sealed):
            return self._send(400, {"error": "bad ask"}, cors=True)
        with self.st.cond:
            if self.st.queued(mac_id) >= self.st.queue_cap:
                return self._send(429, {"error": "busy"}, cors=True)
            ask_id = secrets.token_urlsafe(16)
            self.st.asks[ask_id] = {"mac": mac_id, "entry": entry, "sealed": sealed,
                                    "born": now, "handed": False, "answer": None,
                                    "answered": None}
            self.st.cond.notify_all()
        self._send(200, {"ask_id": ask_id}, cors=True)

    def _get_answer(self, ask_id, query):
        st = self.st
        deadline = time.monotonic() + _wait_param(query)
        with st.cond:
            while True:
                st.sweep(st.clock())
                ask = st.asks.get(ask_id)
                if ask is None:
                    return self._send(404, {"error": "gone"}, cors=True)
                if ask["answer"] is not None:
                    return self._send(200, ask["answer"], cors=True)
                left = deadline - time.monotonic()
                if left <= 0:
                    return self._send(204, cors=True)
                st.cond.wait(left)

    # -- the Mac: GET /v1/pending/<mac>, POST /v1/answer/<ask_id> --------
    def _get_pending(self, mac_id, query):
        st = self.st
        if not MAC_ID.fullmatch(mac_id) or mac_id not in st.macs:
            return self._send(404, {"error": "unknown"})
        if self._mac_for_bearer() != mac_id:
            return self._send(401, {"error": "unauthorized"})
        deadline = time.monotonic() + _wait_param(query)
        with st.cond:
            while True:
                st.sweep(st.clock())
                fresh = [(i, a) for i, a in st.asks.items()
                         if a["mac"] == mac_id and not a["handed"]]
                if fresh:
                    for _, a in fresh:
                        a["handed"] = True
                    asks = [{"ask_id": i, "entry": a["entry"], "sealed": a["sealed"]}
                            for i, a in fresh]
                    try:
                        return self._send(200, {"asks": asks})
                    except OSError:  # the Mac went away mid-send: the next poll gets them
                        for _, a in fresh:
                            a["handed"] = False
                        self.close_connection = True
                        return None
                left = deadline - time.monotonic()
                if left <= 0:
                    return self._send(200, {"asks": []})
                st.cond.wait(left)

    def _post_answer(self, ask_id, _query):
        st = self.st
        mac_id = self._mac_for_bearer()
        if mac_id is None:
            return self._send(401, {"error": "unauthorized"})
        data = self._read_json()
        if data is None:
            return
        if "sealed" in data and not isinstance(data["sealed"], str):
            return self._send(400, {"error": "bad answer"})
        answer = {"sealed": data["sealed"]} if "sealed" in data else {}
        with st.cond:
            now = st.clock()
            st.sweep(now)
            ask = st.asks.get(ask_id)
            if ask is None:
                return self._send(404, {"error": "gone"})
            if ask["mac"] != mac_id:
                return self._send(403, {"error": "not yours"})
            ask["answer"], ask["answered"] = answer, now
            st.cond.notify_all()
        self._send(204)


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(host, port, macs, origins, clock=time.monotonic, *,
                rate_per_min=10, queue_cap=20, trust_proxy=False, sock_timeout=30):
    """`macs` maps mac_id -> sha256 hex of that Mac's secret. `origins` are the page
    origins CORS allows. `trust_proxy` reads CF-Connecting-IP, else the last
    X-Forwarded-For hop. `sock_timeout` drops a client that goes quiet mid-request."""
    server = RelayServer((host, port), Handler)
    server.state = State(dict(macs), clock, rate_per_min, queue_cap)
    server.origins = frozenset(origins)
    server.trust_proxy = trust_proxy
    server.sock_timeout = sock_timeout
    return server


def _parse_macs(text):
    macs = {}
    for item in filter(None, (p.strip() for p in text.split(","))):
        mac_id, _, digest = item.partition(":")
        if not MAC_ID.fullmatch(mac_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SystemExit(f"TILIA_RELAY_MACS: bad entry {mac_id!r}; want id:sha256hex")
        macs[mac_id] = digest
    return macs


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    macs = _parse_macs(os.environ.get("TILIA_RELAY_MACS", ""))
    if not macs:
        raise SystemExit("TILIA_RELAY_MACS is empty; set id:sha256hex[,id:sha256hex]")
    extra = [o.strip() for o in os.environ.get("TILIA_RELAY_ORIGINS", "").split(",") if o.strip()]
    port = int(os.environ.get("PORT", "8787"))
    trust = os.environ.get("TILIA_RELAY_TRUST_PROXY", "") == "1"
    server = make_server("127.0.0.1", port, macs, (PAGES_ORIGIN, *extra), trust_proxy=trust)
    log.info("relay listening on 127.0.0.1:%d with %d mac(s)", port, len(macs))
    server.serve_forever()


if __name__ == "__main__":
    main()
