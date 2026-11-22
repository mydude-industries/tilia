"""Sealing the candle's words for the Mac only, and Tilia's lines back for the page only.

The cross-language fixtures in tests/fixtures/relay/ are made by both suites: the Node test
seals the fixed intention to the test public key (ask.json), this suite opens it and seals
Tilia's two lines back (answer.json), and the Node test opens those. Each side writes its
fixture only when it is missing, so the committed fixtures stay put and both suites read
what the other wrote."""
import base64
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tilia import seal
from tilia.seal import SealError, new_keypair, open_ask, seal_answer

FIX = Path(__file__).resolve().parent / "fixtures" / "relay"
PRIVATE = FIX / "test-private-key.pem"   # a test-only key: it protects nothing real
PUBLIC = FIX / "test-public-key.txt"
ASK = FIX / "ask.json"
ANSWER = FIX / "answer.json"
INTENTION = "Sam's call at four"           # Sam is a fake test person
FIXED_KEY = bytes(range(32))               # the Node test seals the ask with this AES key
NOW, LATER = "Breathe before you dial.", "What did you hear?"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def keypair():
    """The test key pair exists (made once, if missing) for both suites to use."""
    if not PRIVATE.exists() or not PUBLIC.exists():
        priv, pub = new_keypair()
        FIX.mkdir(parents=True, exist_ok=True)
        PRIVATE.write_text(priv)
        PUBLIC.write_text(pub + "\n")
    return PRIVATE.read_text(), PUBLIC.read_text().strip()


def seal_ask_like_the_page(public_b64: str, intention, key: bytes | None = None,
                           payload: dict | None = None) -> str:
    """What candle-seal.js does, written out by hand so Python round trips need no Node."""
    pub = serialization.load_der_public_key(unb64u(public_b64))
    key = key if key is not None else os.urandom(32)
    oaep = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    iv = os.urandom(12)
    body = payload if payload is not None else {"intention": intention}
    c = AESGCM(key).encrypt(iv, json.dumps(body).encode(), None)
    outer = {"k": b64u(pub.encrypt(key, oaep)), "iv": b64u(iv), "c": b64u(c)}
    return b64u(json.dumps(outer).encode())


def open_answer_like_the_page(key: bytes, sealed: str) -> dict:
    outer = json.loads(unb64u(sealed))
    return json.loads(AESGCM(key).decrypt(unb64u(outer["iv"]), unb64u(outer["c"]), None))


def test_new_keypair_shapes():
    priv, pub = new_keypair()
    assert priv.startswith("-----BEGIN PRIVATE KEY-----")
    key = serialization.load_pem_private_key(priv.encode(), password=None)
    assert key.key_size == 2048
    assert "=" not in pub and "+" not in pub and "/" not in pub
    spki = serialization.load_der_public_key(unb64u(pub))
    assert spki.public_numbers() == key.public_key().public_numbers()


def test_round_trip():
    priv, pub = new_keypair()
    sealed = seal_ask_like_the_page(pub, "Riley's walk")
    intention, key = open_ask(priv, sealed)
    assert intention == "Riley's walk" and len(key) == 32
    answer = seal_answer(key, NOW, LATER)
    assert "=" not in answer
    assert open_answer_like_the_page(key, answer) == {"now": NOW, "later": LATER}
    outer = json.loads(unb64u(answer))
    assert set(outer) == {"iv", "c"} and len(unb64u(outer["iv"])) == 12


def test_open_node_sealed_ask():
    priv, _ = keypair()
    if not ASK.exists():
        pytest.skip("ask.json not written yet: run node --test webapp/test/seal.test.js")
    fixture = json.loads(ASK.read_text())
    intention, key = open_ask(priv, fixture["sealed"])
    assert intention == INTENTION == fixture["intention"]
    assert key == FIXED_KEY


def test_seal_answer_fixture():
    priv, _ = keypair()
    if not ASK.exists():
        pytest.skip("ask.json not written yet: run node --test webapp/test/seal.test.js")
    _, key = open_ask(priv, json.loads(ASK.read_text())["sealed"])
    if not ANSWER.exists():
        ANSWER.write_text(json.dumps(
            {"now": NOW, "later": LATER, "sealed": seal_answer(key, NOW, LATER)}, indent=2) + "\n")
    fixture = json.loads(ANSWER.read_text())
    assert open_answer_like_the_page(key, fixture["sealed"]) == {"now": NOW, "later": LATER}


def test_tampered_ask_raises():
    priv, pub = keypair()
    sealed = seal_ask_like_the_page(pub, INTENTION)
    outer = json.loads(unb64u(sealed))
    c = bytearray(unb64u(outer["c"]))
    c[0] ^= 1
    outer["c"] = b64u(bytes(c))
    with pytest.raises(SealError):
        open_ask(priv, b64u(json.dumps(outer).encode()))
    for bad in ("", "!!!", b64u(b"not json"), b64u(b"[]"), b64u(b'{"k": "a"}'), sealed[:-4]):
        with pytest.raises(SealError):
            open_ask(priv, bad)


def test_wrong_key_raises():
    _, pub = keypair()
    other_priv, _ = new_keypair()
    with pytest.raises(SealError):
        open_ask(other_priv, seal_ask_like_the_page(pub, INTENTION))
    with pytest.raises(SealError):
        open_ask("not a pem", seal_ask_like_the_page(pub, INTENTION))


def test_wrong_payload_shape_raises():
    priv, pub = keypair()
    for payload in ({}, {"intention": 4}, {"now": "x"}, [1]):
        with pytest.raises(SealError):
            open_ask(priv, seal_ask_like_the_page(pub, None, payload=payload))


def test_short_aes_key_raises():
    priv, pub = keypair()
    with pytest.raises(SealError):
        open_ask(priv, seal_ask_like_the_page(pub, INTENTION, key=bytes(16)))


def test_seal_answer_rejects_bad_key():
    with pytest.raises(SealError):
        seal_answer(bytes(16), NOW, LATER)
    assert seal.SealError is SealError


def test_padding_optional():
    priv, pub = keypair()
    sealed = seal_ask_like_the_page(pub, INTENTION)
    padded = sealed + "=" * (-len(sealed) % 4)
    assert open_ask(priv, padded)[0] == INTENTION
