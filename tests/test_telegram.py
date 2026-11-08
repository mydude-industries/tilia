import subprocess

import pytest

from tilia import keychain
from tilia.telegram import Telegram, Update, split_reply

TOKEN = "123:fake-token-for-tests"


class FakePost:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def __call__(self, url, params, timeout=10):
        self.calls.append((url, params, timeout))
        if self.exc:
            raise self.exc
        return self.result


def test_split_long_reply_on_sentences():
    text = " ".join(f"Sentence number {i} is here." for i in range(400))
    assert len(text) > 8000 or len(text) > 7000
    parts = split_reply(text)
    assert len(parts) >= 2
    assert all(len(p) <= 3500 for p in parts)
    for p in parts:
        assert p.endswith(".")
    assert " ".join(parts) == text


def test_split_single_giant_sentence_hard_cut():
    parts = split_reply("a" * 8000)
    assert all(len(p) <= 3500 for p in parts)
    assert "".join(parts) == "a" * 8000


def test_split_short_unchanged():
    assert split_reply("hi there.") == ["hi there."]


def test_non_text_update_parsed_as_unsupported():
    post = FakePost({"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 42}, "photo": [{"file_id": "x"}]}},
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "hello"}},
        {"update_id": 3, "edited_channel_post": {}},
    ]})
    ups = Telegram(TOKEN, post=post).updates(5)
    assert ups == [Update(1, 42, None, "unsupported"), Update(2, 42, "hello", "text")]
    url, params, timeout = post.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert params["timeout"] == 0 and params["offset"] == 5
    assert params["allowed_updates"] == '["message"]'


def test_updates_never_raises_on_network_error():
    assert Telegram(TOKEN, post=FakePost(exc=OSError("down"))).updates(None) == []
    assert Telegram(TOKEN, post=FakePost({"ok": False})).updates(None) == []


def test_send_splits_and_reports(capsys):
    post = FakePost({"ok": True})
    t = Telegram(TOKEN, post=post)
    text = " ".join(f"Sentence number {i} is here." for i in range(400))
    assert t.send(7, text) is True
    assert len(post.calls) >= 2
    assert all(c[1]["chat_id"] == 7 for c in post.calls)
    assert Telegram(TOKEN, post=FakePost(exc=OSError(f"bot{TOKEN}"))).send(7, "x") is False
    assert TOKEN not in capsys.readouterr().err


def test_send_document_and_get_me(monkeypatch):
    seen = {}

    def multi(url, fields, files, timeout=30):
        seen.update(url=url, fields=fields, files=files)
        return {"ok": True}

    from tilia import net
    monkeypatch.setattr(net, "post_multipart", multi)
    t = Telegram(TOKEN, post=FakePost({"ok": True, "result": {"id": 9, "username": "b"}}))
    assert t.send_document(7, "a.txt", b"hi") is True
    assert seen["files"]["document"] == ("a.txt", b"hi", "application/octet-stream")
    assert t.get_me() == {"id": 9, "username": "b"}
    assert Telegram(TOKEN, post=FakePost(exc=OSError("x"))).get_me() is None


class FakeRun:
    def __init__(self, rc=0, out=""):
        self.rc, self.out, self.calls = rc, out, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        self.kw = kw
        return subprocess.CompletedProcess(args, self.rc, self.out, "")


def test_keychain_roundtrip_commands():
    r = FakeRun(0, "tok\n")
    assert keychain.get_token(run=r) == "tok"
    assert r.calls[0] == ["security", "find-generic-password", "-s", "tilia-telegram", "-w"]
    tok = _fake_token()
    keychain.set_token(tok, run=r)
    assert r.calls[1] == ["security", "-i"]
    assert tok not in " ".join(r.calls[1])
    assert r.kw["input"] == f"add-generic-password -U -s tilia-telegram -a tilia -w {tok}\n"
    keychain.delete_token(run=r)
    assert r.calls[2] == ["security", "delete-generic-password", "-s", "tilia-telegram"]


def test_keychain_missing_raises():
    with pytest.raises(RuntimeError):
        keychain.get_token(run=FakeRun(44, ""))


def _fake_token():
    return "4242424" + ":" + "x" * 5 + "-" + "y" * 20  # built at runtime


@pytest.mark.parametrize("bad", ["", "nocolon", "12:short", "4242424:abc def" + "z" * 20,
                                 "4242424:" + "a" * 20 + "\nquit"])
def test_set_token_invalid_raises_without_echo(bad):
    r = FakeRun()
    with pytest.raises(ValueError) as ei:
        keychain.set_token(bad, run=r)
    assert not bad or bad not in str(ei.value)
    assert r.calls == []


def test_set_token_timeout_is_runtime_error():
    def run(*a, **k):
        raise subprocess.TimeoutExpired("security", 10)
    with pytest.raises(RuntimeError):
        keychain.set_token(_fake_token(), run=run)


def test_one_malformed_update_does_not_wedge_the_batch():
    body = {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {}, "text": "no chat id"}},
        {"update_id": 2, "message": {"chat": {"id": 7}, "text": "fine"}},
    ]}
    ups = Telegram(TOKEN, post=FakePost(body)).updates(None)
    assert [(u.update_id, u.text) for u in ups] == [(2, "fine")]


def test_update_carries_chat_type_and_sender():
    post = FakePost({"ok": True, "result": [
        {"update_id": 7, "message": {"chat": {"id": 42, "type": "private"},
                                     "from": {"id": 42, "first_name": "Alex", "username": "alex_fake"},
                                     "text": "418207"}},
        {"update_id": 8, "message": {"chat": {"id": -5, "type": "group"}, "text": "hi"}},
    ]})
    a, b = Telegram(TOKEN, post=post).updates(None)
    assert (a.chat_type, a.sender_name, a.sender_username) == ("private", "Alex", "alex_fake")
    assert (b.chat_type, b.sender_name, b.sender_username) == ("group", "", "")
    assert a.update_id == 7 and a.chat_id == 42 and a.text == "418207" and a.kind == "text"
