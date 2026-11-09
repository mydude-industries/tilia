import json
import os
import sys
from pathlib import Path

import pytest

from tilia import entries, paths

sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
import build_deck  # noqa: E402

FOOL = "Young traveler at cliff edge, small dog, white rose, bundle on staff, rising sun"
RWS = "Traditional Rider-Waite-Smith narrative composition and recognizable symbolism for Ace of Wands"
DECK = paths.REPO_ROOT / "webapp" / "tarot"


def _entry(file, scene=FOOL):
    return {"name": file, "number": "0", "file": file, "scene": scene}


def test_gitignore_keeps_webapp_tarot():
    lines = (paths.REPO_ROOT / ".gitignore").read_text().splitlines()
    assert "/tarot/" in lines and "tarot/" not in lines


def test_card_id_and_symbols():
    assert build_deck.card_id("00-the-fool.png") == "00-the-fool"
    assert build_deck.card_id("17-the-star-v2.png") == "17-the-star"
    s = build_deck.symbols(FOOL)
    assert len(s) >= 3 and "small dog" in s
    # commas only: "between dark and light pillars" is one thing, not two
    assert build_deck.symbols("A cup and a sword, a wand") == ["A cup and a sword", "a wand"]
    assert build_deck.symbols("A cup and a sword") == []
    assert build_deck.symbols(RWS) == []


def test_plan_skips_missing_and_reports_orphans(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.png").write_bytes(b"x")
    (src / "stray.png").write_bytes(b"x")
    jobs, orphans = build_deck.plan([_entry("a.png"), _entry("gone.png")], src, dst)
    assert [j.id for j in jobs] == ["a"]
    assert orphans == ["stray.png"]


def test_rerun_skips_up_to_date(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.png").write_bytes(b"x")
    (src / "b.png").write_bytes(b"x")
    (dst / "a.webp").write_bytes(b"y")
    os.utime(src / "a.png", (1000, 1000))
    os.utime(dst / "a.webp", (2000, 2000))
    jobs, _ = build_deck.plan([_entry("a.png"), _entry("b.png")], src, dst)
    assert [j.id for j in jobs] == ["b"]


def test_encode(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    img = Image.effect_noise((1024, 1536), 40).convert("RGB")
    img.save(src / "a.png")
    out = build_deck.build(src, dst, manifest=[_entry("a.png")])
    assert out["built"] == 1 and out["orphans"] == []
    webp = dst / "a.webp"
    assert webp.stat().st_size <= 150 * 1024
    assert Image.open(webp).size == (600, 900)
    man = json.loads((dst / "manifest.json").read_text())
    assert man[0]["id"] == "a" and man[0]["file"] == "a.webp"
    assert "small dog" in man[0]["symbols"]


def test_card_back_has_no_symbols(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (dst / "card-back.webp").write_bytes(b"RIFF")
    (dst / "a.webp").write_bytes(b"RIFF")
    build_deck.build(src, dst, manifest=[_entry("card-back.png"), _entry("a.png")])
    man = json.loads((dst / "manifest.json").read_text())
    assert [e["id"] for e in man] == ["card-back", "a"]
    assert man[0]["symbols"] == [] and "small dog" in man[1]["symbols"]


def test_committed_deck():
    man = json.loads((DECK / "manifest.json").read_text())
    assert len(man) >= 2 and any(e["id"] == "card-back" for e in man)
    by_id = {e["id"]: e for e in man}
    assert by_id["card-back"]["symbols"] == []  # the back is not a card to read
    assert "Seated priestess between dark and light pillars" in \
        by_id["02-the-high-priestess"]["symbols"]
    for e in man:
        data = (DECK / e["file"]).read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        assert len(data) <= 150 * 1024


@pytest.fixture
def deck_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(entries, "DECK_DIR", tmp_path)
    return tmp_path


def test_available_cards_excludes_back_and_missing(deck_dir):
    assert entries.available_cards() == []
    man = [{"id": i, "name": i, "number": "", "file": f"{i}.webp", "scene": "", "symbols": []}
           for i in ("a", "b", "c", "card-back")]
    (deck_dir / "manifest.json").write_text(json.dumps(man))
    for i in ("a", "c", "card-back"):
        (deck_dir / f"{i}.webp").write_bytes(b"RIFF")
    assert [c["id"] for c in entries.available_cards()] == ["a", "c"]
    assert entries.card_info("a")["id"] == "a"
    assert entries.card_info("card-back") is None
    assert entries.card_info("zzz") is None


def test_credits_names_tilia_tarot():
    text = (DECK / "CREDITS.md").read_text()
    assert "Tilia Tarot" in text and "MIT" in text
