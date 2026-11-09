import base64
import json
import re
import tomllib
from urllib.parse import parse_qs, urlsplit

from tilia import entries, paths, webapp

TOKEN = "abcdEFGH_-12"
WORDING = {"pitch": "About Riley and Sunday dinner", "prompts": {"story": "What did Riley say?"}}
VALUE = re.compile(r"[a-z_]+|\d+|[A-Za-z0-9_-]{12}|[a-z0-9-]+(?:\.[a-z0-9-]+)*")


def test_url_path_and_query_hold_no_personal_text():
    u = webapp.launch_url("check_story", TOKEN, card="c-1", seed=42, wording=WORDING)
    parts = urlsplit(u)
    assert parts.path == f"/tilia/{webapp.tag()}/"
    q = parse_qs(parts.query)
    assert set(q) <= {"t", "v", "id", "card", "seed", "lang"}
    assert {"t", "v", "id", "lang"} <= set(q)
    for vals in q.values():
        for v in vals:
            assert VALUE.fullmatch(v)
    assert "Riley" not in parts.path + parts.query
    frag = parts.fragment
    assert frag.startswith("tw=")
    raw = frag[3:]
    decoded = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert "Riley" in json.dumps(decoded)


def test_fallback_mode_moves_wording_to_chat(monkeypatch):
    monkeypatch.setattr(webapp, "FRAGMENT_WORDING", False)
    sheet = entries.load_sheet("check_story")
    offer = webapp.make_offer(sheet, TOKEN, wording=WORDING)
    assert urlsplit(offer.url).fragment == ""
    assert "Riley" in offer.preface
    assert offer.markup["keyboard"][0][0]["web_app"]["url"] == offer.url


def test_generic_offer_has_no_fragment_or_preface():
    offer = webapp.make_offer(entries.load_sheet("check_story"), TOKEN)
    assert urlsplit(offer.url).fragment == "" and offer.preface is None
    assert offer.markup["keyboard"][0][0]["text"] == webapp.LABEL


def test_tag_matches_pyproject():
    with open(paths.REPO_ROOT / "pyproject.toml", "rb") as f:
        assert webapp.tag() == "v" + tomllib.load(f)["project"]["version"]


def test_spread_card_value_is_dotted_ids():
    ids = "00-the-fool.13-death.cups-02"
    u = webapp.launch_url("spread_three", TOKEN, card=ids, wording=WORDING)
    q = parse_qs(urlsplit(u).query)
    assert q["card"] == [ids] and q["t"] == ["spread_three"]
    for vals in q.values():
        for v in vals:
            assert VALUE.fullmatch(v)
    assert not VALUE.fullmatch("00-the-fool.Riley said")  # dots join ids, nothing else
