"""The candle relay: a memory-only mailbox between the page and the owner's Mac."""
import hashlib
import importlib.util
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "tilia_relay", Path(__file__).resolve().parent.parent / "relay" / "tilia_relay.py")
relay = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(relay)

PAGES = "https://mydude-industries.github.io"
SECRET = "sam-mac-secret"
MAC = "sam-mac-1"
ENTRY = "abcDEF123_-x"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Relay:
    def __init__(self, **kw):
        self.clock = Clock()
        macs = {MAC: hashlib.sha256(SECRET.encode()).hexdigest()}
        self.server = relay.make_server("127.0.0.1", 0, macs, (PAGES, "http://localhost:8000"),
                                        clock=self.clock, **kw)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01},
                         daemon=True).start()

    def call(self, method, path, body=None, secret=None, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if secret:
            req.add_header("Authorization", f"Bearer {secret}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw_body = r.read()
                return r.status, (json.loads(raw_body) if raw_body else None), r.headers
        except urllib.error.HTTPError as e:
            raw_body = e.read()
            try:
                parsed = json.loads(raw_body) if raw_body else None
            except ValueError:
                parsed = None
            return e.code, parsed, e.headers

    def ask(self, sealed="sealed-one", entry=ENTRY, mac=MAC, **kw):
        return self.call("POST", f"/v1/ask/{mac}", {"entry": entry, "sealed": sealed}, **kw)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def rl():
    r = Relay()
    yield r
    r.close()


def test_ask_then_mac_pending_then_answer_roundtrip(rl):
    status, body, _ = rl.ask("sealed-question")
    assert status == 200
    ask_id = body["ask_id"]
    assert len(ask_id) >= 22

    status, body, _ = rl.call("GET", f"/v1/pending/{MAC}?wait=1", secret=SECRET)
    assert status == 200
    assert body == {"asks": [{"ask_id": ask_id, "entry": ENTRY, "sealed": "sealed-question"}]}

    # each ask is handed out once
    status, body, _ = rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)
    assert (status, body) == (200, {"asks": []})

    status, _, _ = rl.call("POST", f"/v1/answer/{ask_id}", {"sealed": "sealed-answer"}, secret=SECRET)
    assert status == 204
    status, body, _ = rl.call("GET", f"/v1/answer/{ask_id}?wait=1")
    assert (status, body) == (200, {"sealed": "sealed-answer"})


def test_empty_answer_is_returned_unchanged(rl):
    ask_id = rl.ask()[1]["ask_id"]
    assert rl.call("POST", f"/v1/answer/{ask_id}", {}, secret=SECRET)[0] == 204
    assert rl.call("GET", f"/v1/answer/{ask_id}?wait=0")[:2] == (200, {})


def test_pending_wakes_when_an_ask_arrives(rl):
    out = {}
    t = threading.Thread(target=lambda: out.update(r=rl.call("GET", f"/v1/pending/{MAC}?wait=10", secret=SECRET)))
    t.start()
    rl.ask("late")
    t.join(5)
    assert out["r"][1]["asks"][0]["sealed"] == "late"


def test_pending_requires_right_secret(rl):
    assert rl.call("GET", f"/v1/pending/{MAC}?wait=0")[0] == 401
    assert rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret="wrong")[0] == 401
    assert rl.call("GET", "/v1/pending/nobody-here?wait=0", secret=SECRET)[0] == 404
    ask_id = rl.ask()[1]["ask_id"]
    assert rl.call("POST", f"/v1/answer/{ask_id}", {}, secret="wrong")[0] == 401
    assert rl.call("POST", f"/v1/answer/{ask_id}", {})[0] == 401


def test_ask_validation(rl):
    assert rl.ask(entry="short")[0] == 400
    assert rl.ask(entry="has space!!!")[0] == 400
    assert rl.call("POST", f"/v1/ask/{MAC}", {"entry": ENTRY, "sealed": 5})[0] == 400
    assert rl.call("POST", f"/v1/ask/{MAC}", raw=b"not json")[0] == 400
    assert rl.ask(mac="unknown-mac")[0] == 404


def test_answer_poll_204_then_200(rl):
    ask_id = rl.ask()[1]["ask_id"]
    assert rl.call("GET", f"/v1/answer/{ask_id}?wait=1")[0] == 204
    assert rl.call("GET", "/v1/answer/never-asked-this-one-xyz?wait=0")[0] == 404
    rl.call("POST", f"/v1/answer/{ask_id}", {"sealed": "ans"}, secret=SECRET)
    assert rl.call("GET", f"/v1/answer/{ask_id}?wait=1")[:2] == (200, {"sealed": "ans"})


def test_answer_poll_wakes_when_answered(rl):
    ask_id = rl.ask()[1]["ask_id"]
    out = {}
    t = threading.Thread(target=lambda: out.update(r=rl.call("GET", f"/v1/answer/{ask_id}?wait=10")))
    t.start()
    rl.call("POST", f"/v1/answer/{ask_id}", {"sealed": "now"}, secret=SECRET)
    t.join(5)
    assert out["r"][:2] == (200, {"sealed": "now"})


def test_expired_after_120s(rl):
    ask_id = rl.ask("a")[1]["ask_id"]
    other = rl.ask("b", entry="zzzzzzzzzzzz")[1]["ask_id"]
    rl.call("POST", f"/v1/answer/{other}", {"sealed": "x"}, secret=SECRET)
    rl.clock.t += 119
    assert rl.call("GET", f"/v1/answer/{other}?wait=0")[0] == 200
    rl.clock.t += 2
    assert rl.call("GET", f"/v1/answer/{ask_id}?wait=0")[0] == 404
    assert rl.call("GET", f"/v1/answer/{other}?wait=0")[0] == 404
    assert rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)[1] == {"asks": []}
    assert rl.call("POST", f"/v1/answer/{ask_id}", {}, secret=SECRET)[0] == 404


def test_body_cap_413(rl):
    def body(n):
        pad = n - len('{"entry":"%s","sealed":""}' % ENTRY)
        return ('{"entry":"%s","sealed":"%s"}' % (ENTRY, "s" * pad)).encode()
    raw, ok = body(4097), body(4096)
    assert (len(raw), len(ok)) == (4097, 4096)
    assert rl.call("POST", f"/v1/ask/{MAC}", raw=raw)[0] == 413
    assert rl.call("POST", f"/v1/ask/{MAC}", raw=ok)[0] == 200


def test_rate_limit_429_after_10_per_minute(rl):
    for _ in range(10):
        assert rl.ask()[0] == 200
        rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)  # keep the queue short
    assert rl.ask()[0] == 429
    # polling and the Mac's calls are never rate limited
    assert rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)[0] == 200
    rl.clock.t += 61
    assert rl.ask()[0] == 200


def test_rate_limit_ignores_forwarded_for_by_default(rl):
    for i in range(10):
        rl.ask(headers={"X-Forwarded-For": f"9.9.9.{i}"})
    assert rl.ask(headers={"X-Forwarded-For": "8.8.8.8"})[0] == 429


def test_rate_limit_uses_last_forwarded_hop_when_trusted():
    r = Relay(trust_proxy=True, queue_cap=100)
    try:
        for i in range(10):  # a client-supplied first hop must not dodge the limit
            assert r.ask(headers={"X-Forwarded-For": f"6.6.6.{i}, 10.0.0.1"})[0] == 200
        assert r.ask(headers={"X-Forwarded-For": "7.7.7.7, 10.0.0.1"})[0] == 429
        assert r.ask(headers={"X-Forwarded-For": "7.7.7.7, 10.0.0.2"})[0] == 200
    finally:
        r.close()


def test_rate_limit_prefers_cf_connecting_ip_when_trusted():
    r = Relay(trust_proxy=True, queue_cap=100)
    try:
        for i in range(10):
            assert r.ask(headers={"CF-Connecting-IP": "3.3.3.3", "X-Forwarded-For": f"1.1.1.{i}"})[0] == 200
        assert r.ask(headers={"CF-Connecting-IP": "3.3.3.3", "X-Forwarded-For": "9.9.9.9"})[0] == 429
        assert r.ask(headers={"CF-Connecting-IP": "4.4.4.4"})[0] == 200
    finally:
        r.close()


def test_queue_cap_20_then_429():
    r = Relay(rate_per_min=100)
    try:
        for _ in range(20):
            assert r.ask()[0] == 200
        assert r.ask()[0] == 429
        # handing asks out frees the queue
        r.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)
        assert r.ask()[0] == 200
    finally:
        r.close()


def test_cors_only_pages_origin(rl):
    _, _, h = rl.ask(headers={"Origin": PAGES})
    assert h["Access-Control-Allow-Origin"] == PAGES
    _, _, h = rl.ask(headers={"Origin": "https://evil.example"})
    assert h["Access-Control-Allow-Origin"] is None
    _, _, h = rl.ask()
    assert h["Access-Control-Allow-Origin"] is None
    ask_id = rl.ask()[1]["ask_id"]
    _, _, h = rl.call("GET", f"/v1/answer/{ask_id}?wait=0", headers={"Origin": PAGES})
    assert h["Access-Control-Allow-Origin"] == PAGES
    # the dev origin passed in is allowed too
    _, _, h = rl.ask(headers={"Origin": "http://localhost:8000"})
    assert h["Access-Control-Allow-Origin"] == "http://localhost:8000"


def test_preflight(rl):
    status, _, h = rl.call("OPTIONS", f"/v1/ask/{MAC}", headers={
        "Origin": PAGES, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert status == 204
    assert h["Access-Control-Allow-Origin"] == PAGES
    assert "POST" in h["Access-Control-Allow-Methods"]
    assert "Content-Type" in h["Access-Control-Allow-Headers"]
    _, _, h = rl.call("OPTIONS", "/v1/answer/xyz", headers={"Origin": "https://evil.example"})
    assert h["Access-Control-Allow-Origin"] is None


def test_log_lines_carry_no_body(rl, caplog):
    secret_text = "SEALED-NEEDLE-123"
    with caplog.at_level(logging.DEBUG):
        ask_id = rl.ask(secret_text)[1]["ask_id"]
        rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)
        rl.call("POST", f"/v1/answer/{ask_id}", {"sealed": secret_text}, secret=SECRET)
        rl.call("GET", f"/v1/answer/{ask_id}?wait=0")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text
    for needle in (secret_text, SECRET, ask_id, ENTRY):
        assert needle not in text
    assert "POST /v1/ask 200" in text


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "NaN", "abc"])
def test_non_finite_wait_returns_promptly(rl, bad):
    ask_id = rl.ask()[1]["ask_id"]
    t0 = time.monotonic()
    assert rl.call("GET", f"/v1/answer/{ask_id}?wait={bad}")[0] == 204
    assert rl.call("GET", f"/v1/pending/{MAC}?wait={bad}", secret=SECRET)[0] == 200
    assert rl.call("GET", f"/v1/pending/{MAC}?wait={bad}", secret=SECRET)[1] == {"asks": []}
    assert time.monotonic() - t0 < 2


def test_silent_client_is_dropped_after_the_socket_timeout():
    r = Relay(sock_timeout=0.3)
    try:
        s = socket.create_connection(("127.0.0.1", r.server.server_address[1]), timeout=5)
        s.sendall(f"POST /v1/ask/{MAC} HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n".encode())
        t0 = time.monotonic()
        data = b""
        while True:  # the server gives up and closes; recv ends with b"" (or an error reply first)
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        assert time.monotonic() - t0 < 3
        s.close()
        # and the relay still works
        assert r.ask()[0] == 200
    finally:
        r.close()


def test_log_names_only_known_routes(rl, caplog):
    with caplog.at_level(logging.DEBUG):
        rl.call("GET", "/v1/EVIL-attacker-text/x")
        rl.call("GET", "/v1/answer/x?wait=0")
        for _ in range(100):  # the log line lands just after the response is sent
            if len(caplog.records) >= 2:
                break
            time.sleep(0.02)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "EVIL" not in text
    assert "GET ? 404 " in text
    assert "GET /v1/answer 404 " in text


def test_pending_asks_return_when_the_send_fails(rl, monkeypatch):
    # asks are marked handed before the response goes out; a send that fails un-marks them
    status, body, _ = rl.ask("sealed-question")
    ask_id = body["ask_id"]
    real = relay.Handler._send
    failed = []

    def flaky(self, status, payload=None, *a, **kw):
        if not failed and isinstance(payload, dict) and payload.get("asks"):
            failed.append(status)
            raise BrokenPipeError("the Mac went away")
        return real(self, status, payload, *a, **kw)

    monkeypatch.setattr(relay.Handler, "_send", flaky)
    with pytest.raises(Exception):
        rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)
    assert failed == [200]
    status, body, _ = rl.call("GET", f"/v1/pending/{MAC}?wait=1", secret=SECRET)
    assert status == 200
    assert [a["ask_id"] for a in body["asks"]] == [ask_id]
    status, body, _ = rl.call("GET", f"/v1/pending/{MAC}?wait=0", secret=SECRET)
    assert (status, body) == (200, {"asks": []})  # handed once it really went out
