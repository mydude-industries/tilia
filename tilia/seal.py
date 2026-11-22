"""The candle's words, sealed for this Mac only, and Tilia's two lines sealed back.

The candle page makes a one-time AES-256-GCM key, seals the intention with it and wraps
the key with this Mac's RSA-2048 public key (OAEP, SHA-256). Only the private key, kept in
the Keychain, opens it. The answer goes back under the same one-time key, so only that
page can read it. The relay in between sees base64url strings and nothing else.

Wire format (base64url without padding everywhere):
  ask    = b64u(JSON {"k": b64u(RSA-OAEP(raw key)), "iv": b64u(12 bytes), "c": b64u(AES-GCM({"intention": ...}))})
  answer = b64u(JSON {"iv": b64u(12 bytes), "c": b64u(AES-GCM({"now": ..., "later": ...}))})
"""
import base64
import binascii
import json
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
IV_BYTES = 12
_B64U = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


class SealError(Exception):
    """A sealed ask that can't be opened: malformed, tampered, or for another key."""


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64u(text) -> bytes:
    if not isinstance(text, str) or not _B64U.fullmatch(text):
        raise SealError("not base64url")
    text = text.rstrip("=")  # padding is optional: accepted, never required
    if len(text) % 4 == 1:
        raise SealError("not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as e:
        raise SealError("not base64url") from e


def _json64(text) -> dict:
    try:
        obj = json.loads(_unb64u(text))
    except (UnicodeDecodeError, ValueError) as e:
        raise SealError("not JSON") from e
    if not isinstance(obj, dict):
        raise SealError("not an object")
    return obj


def new_keypair() -> tuple[str, str]:
    """(private key as unencrypted PKCS8 PEM, public key as base64url SPKI DER).

    The public half is what WebCrypto's importKey("spki", ...) takes; the private half
    belongs in the Keychain and nowhere else."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("ascii")
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private_pem, _b64u(public_der)


def public_of(private_pem: str) -> str:
    """The public half (base64url SPKI DER, as new_keypair gives it) of a PEM private key.
    Raises SealError if it isn't one."""
    try:
        key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
        der = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    except (ValueError, TypeError, UnicodeEncodeError, AttributeError) as e:
        raise SealError("not a private key") from e
    return _b64u(der)


def open_ask(private_pem: str, sealed: str) -> tuple[str, bytes]:
    """(intention, one-time answer key) from a sealed ask; SealError on any failure."""
    try:
        private = serialization.load_pem_private_key(private_pem.encode(), password=None)
    except (AttributeError, TypeError, ValueError) as e:
        raise SealError("not a private key") from e
    if not isinstance(private, rsa.RSAPrivateKey):
        raise SealError("not an RSA key")
    outer = _json64(sealed)
    wrapped, iv, body = (_unb64u(outer.get(f)) for f in ("k", "iv", "c"))
    if len(iv) != IV_BYTES:
        raise SealError("bad IV")
    try:
        key = private.decrypt(wrapped, _OAEP)
    except ValueError as e:
        raise SealError("wrapped key does not open") from e
    if len(key) != KEY_BYTES:
        raise SealError("bad key length")
    try:
        plain = AESGCM(key).decrypt(iv, body, None)
    except InvalidTag as e:
        raise SealError("tampered") from e
    try:
        payload = json.loads(plain)
    except (UnicodeDecodeError, ValueError) as e:
        raise SealError("payload not JSON") from e
    if not isinstance(payload, dict) or set(payload) != {"intention"} \
            or not isinstance(payload["intention"], str):
        raise SealError("payload is not {intention}")
    return payload["intention"], key


def seal_answer(key: bytes, now: str, later: str) -> str:
    """Tilia's two lines, sealed under the page's one-time key."""
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise SealError("bad key length")
    iv = os.urandom(IV_BYTES)
    plain = json.dumps({"now": now, "later": later}).encode()
    c = AESGCM(key).encrypt(iv, plain, None)
    return _b64u(json.dumps({"iv": _b64u(iv), "c": _b64u(c)}).encode())
