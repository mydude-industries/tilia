"""The address Tilia puts in a Telegram button. Path and query carry only non-personal
values; any personalized wording goes only in the fragment, which is never sent to a server."""

import base64
import json
import tomllib
from dataclasses import dataclass

from . import entries, paths, telegram

PAGES_BASE = "https://mydude-industries.github.io/tilia/"
LABEL = "Open"
LANG = "en"
FRAGMENT_WORDING = True  # device check 2026-11-08: iPhone keeps the fragment (5,084 chars tested); Telegram Desktop not tested (falls back to generic wording)


def tag() -> str:
    with open(paths.REPO_ROOT / "pyproject.toml", "rb") as f:
        return "v" + tomllib.load(f)["project"]["version"]


def art_url(file: str) -> str:
    """A tarot card's public picture on this release's Pages site (the page's own art)."""
    return f"{PAGES_BASE}{tag()}/tarot/{file}"


_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _q(text: str) -> str:
    """Percent-encode everything but RFC 3986 unreserved characters (net.py alone imports
    urllib, so this is a few lines here)."""
    return "".join(chr(b) if b in _UNRESERVED else f"%{b:02X}" for b in text.encode("utf-8"))


def relay_params(cfg) -> tuple[str, str, str] | None:
    """(base url, mac id, public key) for a candle button, or None if no relay is set up.
    All three are public: the private key and the secret stay in the Keychain."""
    if cfg.relay_url and cfg.relay_id and cfg.relay_public:
        return (cfg.relay_url, cfg.relay_id, cfg.relay_public)
    return None


def launch_url(type: str, token: str, *, card=None, seed=None, wording=None, v=1,
               mode=None, relay=None) -> str:
    """`relay` is relay_params(cfg); only a candle's button carries it."""
    q = [("t", type), ("v", v), ("id", token)]
    if card is not None:
        q.append(("card", card))
    if seed is not None:
        q.append(("seed", seed))
    if mode is not None:
        q.append(("mode", mode))
    q.append(("lang", LANG))
    if relay is not None and type in entries.RITUALS:
        base, mac, key = relay
        q += [("relay", _q(base)), ("mac", _q(mac)), ("k", _q(key))]
    page = entries.RITUAL_PAGES.get(type, "")  # a ritual has a page of its own
    url = f"{PAGES_BASE}{tag()}/{page}?" + "&".join(f"{k}={v}" for k, v in q)
    if wording and FRAGMENT_WORDING:
        raw = json.dumps(wording, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        url += "#tw=" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return url


def wording_text(sheet: dict, wording: dict) -> str:
    """The personalized pitch and prompts as plain chat text (the fallback for the fragment)."""
    lines = []
    if wording.get("pitch"):
        lines.append(wording["pitch"])
    prompts = wording.get("prompts") or {}
    for c in sheet["cards"]:
        if c["id"] in prompts:
            lines.append(f"- {prompts[c['id']]}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Offer:
    token: str
    url: str
    preface: str | None

    @property
    def markup(self) -> dict:
        return telegram.webapp_markup(LABEL, self.url)


def make_offer(sheet: dict, token: str, *, card=None, seed=None, wording=None,
               mode=None, relay=None) -> Offer:
    url = launch_url(sheet["type"], token, card=card, seed=seed, wording=wording, v=sheet["v"],
                     mode=mode, relay=relay)
    preface = wording_text(sheet, wording) if wording and not FRAGMENT_WORDING else None
    return Offer(token, url, preface or None)
