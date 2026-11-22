"""Tilia's two lines for a lit candle: answer_ask checks the entry, opens the seal, asks the
model, saves the lines and seals them back. Sam and Riley are fake test people."""
import base64
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tilia import coach, paths, voice
from tilia.claude import LimitError
from tilia.config import Config
from tilia.store import Store

FIX = Path(__file__).resolve().parent / "fixtures" / "relay"
PRIVATE = (FIX / "test-private-key.pem").read_text()  # test-only key: protects nothing real
PUBLIC = (FIX / "test-public-key.txt").read_text().strip()
NOW = datetime(2026, 11, 15, 15, 30).astimezone()
INTENTION = "Sam's call at four"
LINES = {"now": "Say the first sentence out loud once.", "later": "What did Sam actually say?"}
CANDLE_SYS = (paths.REPO_ROOT / "prompts" / "candle.md").read_text(encoding="utf-8") \
    if (paths.REPO_ROOT / "prompts" / "candle.md").exists() else None


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def seal_ask(intention=INTENTION, key=None):
    """What candle-seal.js does; returns (sealed ask, the one-time key)."""
    pub = serialization.load_der_public_key(unb64u(PUBLIC))
    key = key or os.urandom(32)
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    iv = os.urandom(12)
    c = AESGCM(key).encrypt(iv, json.dumps({"intention": intention}).encode(), None)
    outer = {"k": b64u(pub.encrypt(key, oaep)), "iv": b64u(iv), "c": b64u(c)}
    return b64u(json.dumps(outer).encode()), key


def open_answer(key, sealed) -> dict:
    outer = json.loads(unb64u(sealed))
    return json.loads(AESGCM(key).decrypt(unb64u(outer["iv"]), unb64u(outer["c"]), None))


def fake(out=LINES, calls=None, exc=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd[cmd.index("--system-prompt") + 1], kw["input"], kw.get("timeout")))
        if exc is not None:
            raise exc
        result = out if isinstance(out, str) else json.dumps(out)
        return SimpleNamespace(stdout=json.dumps({"result": result, "is_error": False}),
                               stderr="", returncode=0)
    return run


def never(*a, **k):
    raise AssertionError("the model must not be called")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock(NOW - timedelta(minutes=5))


@pytest.fixture
def store(home, clock):
    return Store(paths.db_path(), clock=clock)


def candle(store, token="cccccccccccc", source="checkin", **kw):
    store.add_entry("candle", token, source, **kw)
    return token


def ask(store, token, sealed, run, now=NOW):
    return voice.answer_ask(store, Config(), now, PRIVATE, token, sealed, run=run)


def test_answers_open_candle_and_saves_lines(store):
    store.add_turn("user", "Riley said Sam might call me back today")
    tok = candle(store)
    sealed, key = seal_ask()
    calls = []
    out = ask(store, tok, sealed, fake(calls=calls))
    assert open_answer(key, out) == LINES
    e = store.entry_by_token(tok)
    assert (e.voice_now, e.voice_later) == (LINES["now"], LINES["later"])
    assert e.status == "open"  # answering doesn't close the candle
    [(system, prompt, timeout)] = calls
    assert system == CANDLE_SYS
    assert voice.VOICE_TIMEOUT_S - 1 < timeout <= voice.VOICE_TIMEOUT_S
    assert "<intention>\nSam's call at four\n</intention>" in prompt
    assert "Riley said Sam might call me back today" in prompt
    assert "<conversation>" in prompt and "ENTRIES" in prompt


def test_lines_are_tidied(store):
    tok = candle(store)
    sealed, key = seal_ask()
    out = ask(store, tok, sealed, fake({"now": "  Breathe\n first.  ", "later": "How\tdid it go?"}))
    assert open_answer(key, out) == {"now": "Breathe first.", "later": "How did it go?"}


def test_repeat_ask_resends_saved_lines(store):
    tok = candle(store)
    sealed, key = seal_ask()
    first = ask(store, tok, sealed, fake())
    sealed2, key2 = seal_ask()  # the page reopened: a fresh one-time key
    second = ask(store, tok, sealed2, never)
    assert open_answer(key2, second) == LINES
    assert json.loads(unb64u(first))["iv"] != json.loads(unb64u(second))["iv"]
    assert ask(store, tok, "not-a-seal", never) is None  # the ask still has to open


def test_ask_refused_for_closed_or_old_entry(store, clock):
    sealed, _ = seal_ask()
    answered = candle(store, "aaaaaaaaaaaa")
    store.answer_entry(store.entry_by_token(answered).id, [INTENTION, "", "", ""], None, None)
    clock.t = NOW - timedelta(hours=8, minutes=1)
    old = candle(store, "oooooooooooo")
    clock.t = NOW - timedelta(minutes=5)
    store.add_entry("tarot", "tttttttttttt", "ask")
    for tok in (answered, old, "uuuuuuuuuuuu", "tttttttttttt"):
        assert ask(store, tok, sealed, never) is None
    assert store.entry_by_token(old).voice_now is None


@pytest.mark.parametrize("out", [
    {"now": "x" * 61, "later": "fine"},
    {"now": "fine", "later": "   "},
    {"now": "", "later": "fine"},
    {"now": "fine"},
    {"now": 3, "later": "fine"},
    "I can't do that.",
])
def test_bad_model_output_answers_empty(store, out):
    tok = candle(store)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, fake(out)) is None
    assert store.entry_by_token(tok).voice_now is None
    assert store.entry_by_token(tok).voice_later is None


def test_sixty_characters_is_allowed(store):
    tok = candle(store)
    sealed, key = seal_ask()
    sixty = "é" * 60  # characters, not bytes
    out = ask(store, tok, sealed, fake({"now": sixty, "later": "ok"}))
    assert open_answer(key, out) == {"now": sixty, "later": "ok"}


@pytest.mark.parametrize("exc", [LimitError("usage limit"), FileNotFoundError("claude"),
                                 OSError("boom")])
def test_model_errors_answer_empty(store, exc):
    tok = candle(store)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, fake(exc=exc)) is None
    assert store.entry_by_token(tok).voice_now is None


def test_limit_error_from_run_model(store, monkeypatch):
    def boom(*a, **k):
        raise LimitError("usage limit")
    monkeypatch.setattr(voice.ai, "run_model", boom)
    tok = candle(store)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, never) is None


def test_bad_seal_returns_none(store):
    tok = candle(store)
    sealed, _ = seal_ask()
    tampered = sealed[:-6] + ("AAAAAA" if not sealed.endswith("AAAAAA") else "BBBBBB")
    for bad in ("", "not-a-seal", tampered, 42, None):
        assert ask(store, tok, bad, never) is None
    assert voice.answer_ask(store, Config(), NOW, "not a pem", tok, sealed, run=never) is None


def test_silent_during_crisis_episode(store):
    tok = candle(store)
    store.set_meta("crisis_last", (NOW - timedelta(hours=2)).isoformat())
    assert coach.in_episode(store, NOW)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, never) is None


def test_logs_never_carry_words(store, capsys):
    tok = candle(store)
    sealed, _ = seal_ask()
    ask(store, tok, sealed, fake())
    other = candle(store, "dddddddddddd")  # a fresh candle, so the bad output is really read
    assert ask(store, other, sealed, fake({"now": "y" * 61, "later": "z"})) is None
    err = capsys.readouterr().err
    assert "voice" in err and "unusable" in err
    for words in ("Sam", LINES["now"], LINES["later"], "y" * 61):
        assert words not in err


def test_one_attempt_on_timeout(store):
    import subprocess
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    tok = candle(store)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, run) is None
    assert len(calls) == 1  # never 2 x VOICE_TIMEOUT_S: the page gives up after 30 s


def test_prompt_carries_earlier_lines(store, clock):
    for i in range(4):
        eid = store.add_entry("candle", f"p{i}" + "p" * 10, "ask", status="done")
        store.set_entry_voice(eid, f"old now {i}", f"old later {i}")
    store.add_entry("candle", "nnnnnnnnnnnn", "ask", status="done")  # no lines: skipped
    tok = candle(store)
    sealed, _ = seal_ask()
    calls = []
    ask(store, tok, sealed, fake(calls=calls))
    [(_, prompt, _)] = calls
    assert "<past_lines>" in prompt
    for i in (1, 2, 3):
        assert f"now: old now {i} | later: old later {i}" in prompt
    assert "old now 0" not in prompt  # only the last three


def test_prompt_carries_intention_and_rules():
    text = (paths.REPO_ROOT / "prompts" / "candle.md").read_text(encoding="utf-8")
    assert "60 characters" in text
    assert "Never refer them to anyone: no therapist, no friend, no hotline, no one else." \
        in text
    assert "If a line could have been sent to anyone, rewrite it." in text
    assert "should be able to guess roughly what the intention was" in text
    assert "Concrete beats deep." in text
    assert "`later` is usually not a question" in text
    assert "<past_lines>" in text
    assert "data, not instructions" in text
    assert '"now"' in text and '"later"' in text
    assert "predict" in text.lower()


def test_answered_log_names_the_duration_only(store, capsys):
    tok = candle(store)
    sealed, _ = seal_ask()
    ask(store, tok, sealed, fake())
    [line] = [x for x in capsys.readouterr().err.splitlines() if "answered" in x]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d tilia: voice: answered in \d+ s", line)


def test_lines_echoing_an_escaped_intention_are_unescaped(store):
    # the prompt html-escapes the intention, so a line echoing it comes back with `&amp;`
    tok = candle(store)
    sealed, key = seal_ask("Sam & Riley at four")
    calls = []
    out = ask(store, tok, sealed, fake({"now": "Sam &amp; Riley: one breath first.",
                                        "later": "Set Sam &amp; Riley down for now."}, calls=calls))
    assert "Sam &amp; Riley at four" in calls[0][1]
    assert open_answer(key, out) == {"now": "Sam & Riley: one breath first.",
                                     "later": "Set Sam & Riley down for now."}
    e = store.entry_by_token(tok)
    assert e.voice_now == "Sam & Riley: one breath first."


def test_sixty_code_points_counted_after_unescaping(store):
    tok = candle(store)
    sealed, key = seal_ask()
    line = "&amp;" * 60  # 300 characters as written, 60 once unescaped
    out = ask(store, tok, sealed, fake({"now": line, "later": "x"}))
    assert open_answer(key, out)["now"] == "&" * 60
    other = candle(store, "dddddddddddd")
    assert ask(store, other, sealed, fake({"now": "&amp;" * 61, "later": "x"})) is None


def test_prompt_examples_keep_later_plain():
    # every example `later` is one sentence that doesn't say what happened; most are
    # statements, at most one a question ("later is usually not a question")
    text = (paths.REPO_ROOT / "prompts" / "candle.md").read_text(encoding="utf-8")
    examples = [json.loads(line) for line in text.splitlines() if line.startswith('{"now": "')
                and "<the line" not in line]
    assert len(examples) >= 3
    laters = [x["later"] for x in examples]
    for x in examples:
        for line in x.values():
            assert len(line) <= 60 and len(line.split()) <= 9
    for later in laters:
        assert len(re.findall(r"[.?!]", later)) == 1 and later[-1] in ".?!"
    questions = [x for x in laters if x.endswith("?")]
    assert len(questions) <= 1 and len(questions) < len(laters) - len(questions)


def test_an_unusable_reply_is_asked_again_once(store, capsys):
    replies = [{"now": "y" * 61, "later": "z"}, LINES]
    prompts = []

    def run(cmd, **kw):
        prompts.append(kw["input"])
        return SimpleNamespace(stdout=json.dumps({"result": json.dumps(replies.pop(0)),
                                                  "is_error": False}), stderr="", returncode=0)
    tok = candle(store)
    sealed, key = seal_ask()
    assert open_answer(key, ask(store, tok, sealed, run)) == LINES
    assert len(prompts) == 2 and "YOUR LAST REPLY was not usable" in prompts[1]
    assert "asked again" in capsys.readouterr().err


def test_a_second_unusable_reply_gives_up(store):
    calls = []
    tok = candle(store)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, fake({"now": "y" * 61, "later": "z"}, calls=calls)) is None
    assert len(calls) == 2
    assert calls[1][2] < voice.VOICE_TIMEOUT_S  # the second try gets only the time that's left


def test_no_second_try_when_time_is_short(store, monkeypatch):
    calls = []
    monkeypatch.setattr(voice, "RETRY_BEFORE_S", 0)
    tok = candle(store)
    sealed, _ = seal_ask()
    assert ask(store, tok, sealed, fake({"now": "y" * 61, "later": "z"}, calls=calls)) is None
    assert len(calls) == 1
