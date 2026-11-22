"""The Mac's side of the candle relay: long-poll for asks, answer each one. No real network,
no real Keychain (every `run` and `urlopen` is a fake)."""
import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

from tilia import config, keychain, relay

BASE = "https://relay.example.com"
MAC = "sam-mac-01"
SECRET = "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LXNlYw"


class Resp(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeNet:
    """Scripted replies to GET /v1/pending; every POST /v1/answer is recorded (204)."""

    def __init__(self, pending):
        self.pending = list(pending)  # each item: a dict body, or an Exception to raise
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((req.get_method(), url, req.data, dict(req.header_items()), timeout))
        if "/v1/pending/" in url:
            if not self.pending:
                raise Stop()
            nxt = self.pending.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return Resp(nxt)
        if "/v1/answer/" in url:
            return Resp(None, status=204)
        raise AssertionError(url)

    def answers(self):
        return [(u.rsplit("/", 1)[1], json.loads(d)) for m, u, d, h, t in self.calls
                if m == "POST"]


class Stop(BaseException):  # not an Exception: run_forever swallows those
    pass


def client(net, handle, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return relay.RelayClient(BASE, MAC, SECRET, handle, urlopen=net, sleep=sleeps.append)


def ask(i, entry="abcdEFGH_-12", sealed="c2VhbGVk"):
    return {"ask_id": f"ask{i}", "entry": entry, "sealed": sealed}


def test_client_answers_each_pending_ask():
    net = FakeNet([{"asks": [ask(1), ask(2, entry="zyxwVUTS_-98")]}, {"asks": []}])
    seen = []

    def handle(entry, sealed):
        seen.append((entry, sealed))
        return "sealed-answer" if entry == "abcdEFGH_-12" else None

    c = client(net, handle)
    with pytest.raises(Stop):
        c.run_forever()
    assert seen == [("abcdEFGH_-12", "c2VhbGVk"), ("zyxwVUTS_-98", "c2VhbGVk")]
    assert net.answers() == [("ask1", {"sealed": "sealed-answer"}), ("ask2", {})]
    get = net.calls[0]
    assert get[0] == "GET" and get[1] == f"{BASE}/v1/pending/{MAC}?wait=25"
    assert get[3].get("Authorization") == f"Bearer {SECRET}"
    assert get[4] > 25  # the socket outlives the long-poll
    posts = [c for c in net.calls if c[0] == "POST"]
    assert all(p[3].get("Authorization") == f"Bearer {SECRET}" for p in posts)
    assert all(p[3].get("Content-type") == "application/json" for p in posts)


def test_client_backs_off_on_errors():
    err = urllib.error.URLError("down")
    http = urllib.error.HTTPError(BASE, 401, "no", {}, io.BytesIO(b""))
    net = FakeNet([err, err, http, err, err, err, {"asks": []}, err])
    sleeps = []
    c = client(net, lambda e, s: None, sleeps)
    with pytest.raises(Stop):
        c.run_forever()
    # 5 s doubling to a 60 s cap; a good poll resets it
    assert sleeps == [5, 10, 20, 40, 60, 60, 5]


def test_bad_pending_body_backs_off_without_answering():
    net = FakeNet([{"asks": [{"ask_id": "../../x", "entry": "e", "sealed": "s"}]},
                   {"nope": 1}])
    sleeps = []
    calls = []
    c = client(net, lambda e, s: calls.append(e), sleeps)
    with pytest.raises(Stop):
        c.run_forever()
    assert calls == [] and net.answers() == []
    assert sleeps == [5]  # the malformed ask is skipped; the body with no asks list backs off


def test_handler_crash_answers_empty(capsys):
    net = FakeNet([{"asks": [ask(1, sealed="SECRET-WORDS")]}])

    def handle(entry, sealed):
        raise ValueError("SECRET-WORDS in the message")

    c = client(net, handle)
    with pytest.raises(Stop):
        c.run_forever()
    assert net.answers() == [("ask1", {})]
    err = capsys.readouterr().err
    assert "ValueError" in err and "SECRET-WORDS" not in err


def test_answer_post_failure_does_not_kill_the_client():
    class Net(FakeNet):
        def __call__(self, req, timeout=None):
            if req.get_method() == "POST":
                self.calls.append(("POST-FAIL", req.full_url, req.data, {}, timeout))
                raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, io.BytesIO(b""))
            return super().__call__(req, timeout)

    net = Net([{"asks": [ask(1), ask(2)]}])
    seen = []
    c = client(net, lambda e, s: seen.append(e) or "x")
    with pytest.raises(Stop):
        c.run_forever()
    assert len(seen) == 2  # the second ask is still answered


def test_poll_marks_seen_only_on_success():
    net = FakeNet([urllib.error.URLError("down"), {"asks": []}])
    seen = []
    c = relay.RelayClient(BASE, MAC, SECRET, lambda e, s: None, urlopen=net,
                          sleep=lambda s: None, on_seen=lambda: seen.append(1))
    with pytest.raises(Stop):
        c.run_forever()
    assert seen == [1]


# ---- checks and helpers --------------------------------------------------------------

@pytest.mark.parametrize("url,want", [
    ("https://relay.example.com/", "https://relay.example.com"),
    ("https://relay.example.com/tilia", "https://relay.example.com/tilia"),
    ("http://127.0.0.1:8787", "http://127.0.0.1:8787"),
    ("http://localhost:8787/", "http://localhost:8787"),
])
def test_check_url_ok(url, want):
    assert relay.check_url(url) == want


@pytest.mark.parametrize("url", ["http://relay.example.com", "ftp://x.example.com", "",
                                 "relay.example.com", "https://", "https://a b.com",
                                 "https://x.example.com/?q=1", "https://x.example.com/#f",
                                 "https://u:p@x.example.com"])
def test_check_url_refuses(url):
    with pytest.raises(ValueError):
        relay.check_url(url)


def test_pem_round_trips_as_one_line():
    from tilia import seal
    pem, _ = seal.new_keypair()
    one = relay.encode_pem(pem)
    assert "\n" not in one and " " not in one
    assert relay.decode_pem(one) == pem


def test_new_secret_and_id():
    s = relay.new_secret()
    assert len(s) == 43 and s != relay.new_secret()
    i = relay.new_id()
    assert len(i) == 10 and i.isalnum()
    import hashlib
    assert relay.server_line("sam-mac-01", "abc") == \
        "sam-mac-01:" + hashlib.sha256(b"abc").hexdigest()


# ---- the Keychain: values go on stdin, never argv ------------------------------------

class FakeRun:
    def __init__(self, rc=0, out=""):
        self.calls = []
        self.rc, self.out = rc, out

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return SimpleNamespace(returncode=self.rc, stdout=self.out, stderr="")


def test_secret_goes_via_stdin_not_argv():
    r = FakeRun()
    keychain.set_secret(relay.SECRET_SERVICE, SECRET, run=r)
    (cmd, kw), = r.calls
    assert cmd == ["security", "-i"]
    assert SECRET not in " ".join(cmd)
    assert SECRET in kw["input"] and relay.SECRET_SERVICE in kw["input"]
    assert kw["input"].endswith("\n") and kw["input"].count("\n") == 1


@pytest.mark.parametrize("bad", ["", "has space", "two\nlines", 'quo"te', "it's", "back\\slash"])
def test_set_secret_refuses_unsafe_values(bad):
    r = FakeRun()
    with pytest.raises(ValueError) as ei:
        keychain.set_secret(relay.SECRET_SERVICE, bad, run=r)
    assert r.calls == []
    assert not bad or bad not in str(ei.value)


def test_set_secret_failure_is_runtime_error():
    with pytest.raises(RuntimeError):
        keychain.set_secret(relay.KEY_SERVICE, "abc", run=FakeRun(rc=1))


def test_get_secret():
    r = FakeRun(out="abc\n")
    assert keychain.get_secret(relay.KEY_SERVICE, run=r) == "abc"
    assert r.calls[0][0] == ["security", "find-generic-password", "-s", relay.KEY_SERVICE, "-w"]
    with pytest.raises(RuntimeError):
        keychain.get_secret(relay.KEY_SERVICE, run=FakeRun(rc=44))


# ---- starting the thread --------------------------------------------------------------

class FakeThread:
    made = []

    def __init__(self, target, name, daemon):
        self.target, self.name, self.daemon = target, name, daemon
        self.started = False
        FakeThread.made.append(self)

    def start(self):
        self.started = True


def secrets_from(d):
    def get(service):
        if service not in d:
            raise RuntimeError("missing")
        return d[service]
    return get


def test_start_needs_config_and_keychain(home, capsys):
    from tilia import seal
    pem, pub = seal.new_keypair()
    have = {relay.KEY_SERVICE: relay.encode_pem(pem), relay.SECRET_SERVICE: SECRET}
    FakeThread.made = []
    assert relay.start(config.Config(), get_secret=secrets_from(have),
                       thread=FakeThread) is None
    assert FakeThread.made == []
    cfg = config.Config(relay_url=BASE, relay_id=MAC, relay_public=pub)
    assert relay.start(cfg, get_secret=secrets_from({}), thread=FakeThread) is None
    assert "./setup relay" in capsys.readouterr().err
    t = relay.start(cfg, get_secret=secrets_from(have), thread=FakeThread)
    assert t is FakeThread.made[-1] and t.started and t.daemon


def test_handler_opens_and_closes_its_own_store(home, monkeypatch):
    from tilia import store as store_mod
    from tilia import voice
    opened, closed, got = [], [], []
    real = store_mod.Store

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            opened.append(self)

        def close(self):
            closed.append(self)
            super().close()

    monkeypatch.setattr(relay, "Store", Spy)
    monkeypatch.setattr(voice, "answer_ask",
                        lambda store, cfg, now, pem, entry, sealed: got.append(
                            (store, pem, entry, sealed, now.tzinfo is not None)) or "ans")
    h = relay.make_handler("PEM")
    assert h("abcdEFGH_-12", "s") == "ans"
    assert opened == closed and len(opened) == 1
    assert got[0][1:] == ("PEM", "abcdEFGH_-12", "s", True)


def test_seen_writes_meta(home):
    from tilia import paths
    from tilia.store import Store
    relay.mark_seen()
    s = Store(paths.db_path())
    assert s.get_meta("relay_seen")
