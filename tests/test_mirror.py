import json
import os
import re
import stat
from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest

from tilia import commands, entries, mirror, paths
from tilia.config import Config
from tilia.store import Store

from tests.test_commands import FakeTG

NOW = datetime(2026, 11, 7, 12, 0).astimezone()
BLOT_JS = (paths.REPO_ROOT / "webapp" / "blot.js").read_text(encoding="utf-8")
STORY = [3, "Riley went quiet at Sunday dinner", "that I'd said something wrong",
         "she smiled earlier", "she was tired", 6]
# check_story as answered against its first version (v1, nine answer cards), before Task 17
STORY_V1 = [3, "Riley went quiet at Sunday dinner", "that I'd said something wrong", 70,
            "she smiled earlier", ["They were stressed"], "she was tired",
            "it's probably not about you", 6]
BLOT = [4, None, "two herons bowing", "a cracked mask", ["Tender"], "soft, like an apology", 5]
EVIL = "</script><img src=x onerror=alert(1)><b>hi</b>"


@pytest.fixture
def store(home):
    return Store(paths.db_path(), clock=lambda: NOW)


def done(store, type_, answers, *, seed=None, card=None, reply="Thanks for writing that.",
         themes=("closeness",), wording=None, turn=True):
    turn_id = None
    if turn:
        store.add_turn("user", f"[{type_}] answers")
        turn_id = store.last_turn_id("user")
    eid = store.add_entry(type_, entries.new_token(), "ask", seed=seed, card=card,
                          wording=wording, turn_id=turn_id)
    store.answer_entry(eid, answers, answers[0], answers[-1])
    store.finish_entry(eid, reply, list(themes), turn_id)
    return eid


def data_of(html: str) -> list:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.starts = []

    def handle_starttag(self, tag, attrs):
        self.starts.append(tag)


def tags(html):
    p = Tags()
    p.feed(html)
    return p.starts


def test_mirror_is_0600_in_data_folder(store):
    done(store, "check_story", STORY)
    p = mirror.write(store, NOW)
    assert p == mirror.path() and mirror.path().parent == paths.data_dir()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert [f.name for f in paths.data_dir().iterdir() if "mirror" in f.name] == ["mirror.html"]


def test_mirror_has_no_external_refs(store):
    done(store, "check_story", STORY)
    done(store, "blot", BLOT, seed=777)
    done(store, "tarot", [5, "the dog at the edge", 6], card="00-the-fool")
    html = mirror.render(store, NOW)
    for bad in ("http://", "https://", 'src="//', "<link", "@import", "<script src"):
        assert bad not in html, bad
    assert "default-src 'none'" in html  # a CSP that refuses any load


def test_mirror_escapes_answers(store):
    template = tags(mirror.render(store, NOW)).count("script")
    answers = list(STORY)
    answers[1] = EVIL
    done(store, "check_story", answers, reply=EVIL)
    html = mirror.render(store, NOW)
    starts = tags(html)
    assert "img" not in starts and "b" not in starts
    assert starts.count("script") == template
    assert EVIL not in html
    assert EVIL in json.dumps(data_of(html))  # still there, as data, intact


def test_open_and_expired_entries_not_shown(store):
    shown = done(store, "check_story", STORY)
    store.add_entry("check_story", entries.new_token(), "ask")  # open
    gone = store.add_entry("check_story", entries.new_token(), "ask")
    store._x("UPDATE entries SET status='expired' WHERE id=?", (gone,))
    half = store.add_entry("check_story", entries.new_token(), "ask")
    store.answer_entry(half, STORY, 3, 6)  # answered, no reply yet
    items = data_of(mirror.render(store, NOW))["entries"]
    assert [i["id"] for i in items] == [shown]


def test_newest_first_with_both_sides(store):
    first = done(store, "check_story", STORY, wording={"prompts": {"story": "What did you tell yourself Riley meant?"}})
    second = done(store, "blot", BLOT, seed=12)
    d = data_of(mirror.render(store, NOW))
    assert [i["id"] for i in d["entries"]] == [second, first]
    story = d["entries"][1]
    assert {"q": "What did you tell yourself Riley meant?",
            "a": "that I'd said something wrong"} in story["qa"]
    assert {"q": "If a friend told you this, what else might it mean?",
            "a": "she was tired"} in story["qa"]
    assert story["mood"] == [3, 6] and story["themes"] == ["closeness"]
    assert story["reply"] == "Thanks for writing that." and story["title"] == "Check the story"


def test_old_sheet_version_shows_the_words_alone(store):
    """An entry answered against check_story v1 still renders after the sheet moved to v2:
    every word they wrote shows, none under a question it didn't answer."""
    eid = done(store, "check_story", STORY_V1)
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert item["id"] == eid and item["mood"] == [3, 6]
    assert all(qa["q"] == "" for qa in item["qa"])
    words = [qa["a"] for qa in item["qa"]]
    for said in ("Riley went quiet at Sunday dinner", "that I'd said something wrong",
                 "she smiled earlier", "They were stressed", "she was tired",
                 "it's probably not about you"):
        assert said in words


def test_same_count_wrong_kinds_is_not_paired(store):
    """A stored list that happens to have the right length but not the right kinds."""
    done(store, "check_story", [3, ["They were stressed"], "that I'd said something wrong",
                                "she smiled earlier", "she was tired", 6])
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert all(qa["q"] == "" for qa in item["qa"])
    assert "They were stressed" in [qa["a"] for qa in item["qa"]]


def test_what_happened_after_followup(store):
    eid = done(store, "check_story", STORY)
    store.set_entry_followup_at(eid, (NOW + timedelta(days=1)).isoformat())
    later = Store(paths.db_path(), clock=lambda: NOW + timedelta(days=2))
    later.add_turn("user", "I asked Riley and she was just tired")
    later.add_turn("user", "second message")
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert item["happened"] == "I asked Riley and she was just tired"


def test_blot_redrawn_from_seed(store):
    done(store, "blot", BLOT, seed=12345)
    html = mirror.render(store, NOW)
    assert BLOT_JS in html
    assert '"seed": 12345' in html


def test_tarot_without_deck_shows_name(store, monkeypatch, tmp_path):
    monkeypatch.setattr(entries, "DECK_DIR", tmp_path)
    monkeypatch.setattr(mirror, "TAROT_DIR", tmp_path)
    done(store, "tarot", [5, "the dog at the edge", 6], card="00-the-fool")
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert item["card"]["name"] == "The Fool" and "art" not in item["card"]


def test_tarot_art_embedded_when_present(store, monkeypatch, tmp_path):
    (tmp_path / "00-the-fool.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    monkeypatch.setattr(mirror, "TAROT_DIR", tmp_path)
    monkeypatch.setattr(entries, "card_info", lambda cid: {"id": cid, "name": "The Fool"},
                        raising=False)
    done(store, "tarot", [5, "the dog", 6], card="00-the-fool")
    d = data_of(mirror.render(store, NOW))
    [item] = d["entries"]
    assert item["card"]["name"] == "The Fool"
    assert d["art"][item["card"]["art"]].startswith("data:image/webp;base64,UklGR")


THREE = ["00-the-fool", "cups-03-three", "21-the-world"]
SEALED = "The Fool behind you, the Three of Cups where you are, the World next."


def spread_answers(type_, says):
    out, n = [], 0
    for c in entries.answer_cards(entries.load_sheet(type_)):
        if c["kind"] == "mood":
            out.append(5)
        elif c["kind"] == "reveal":
            out.append(None)
        elif c["id"] == "together":
            out.append("they all lean toward people")
        else:
            out.append(says[n])
            n += 1
    return out


def test_mirror_renders_spread_positions(store, monkeypatch, tmp_path):
    (tmp_path / "00-the-fool.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    monkeypatch.setattr(mirror, "TAROT_DIR", tmp_path)  # one card with art, two without
    says = ["the leap I took", EVIL, "a finished circle"]
    reply = f"Here's the read I wrote before you looked:\n\n{SEALED}\n\nYou saw the circle too."
    done(store, "spread_three", spread_answers("spread_three", says), card=".".join(THREE),
         reply=reply)
    store._x("UPDATE entries SET sealed_read=?", (SEALED,))
    html = mirror.render(store, NOW)
    d = data_of(html)
    [item] = d["entries"]
    sp = item["spread"]
    assert sp["layout"] == "spread_three" and item["title"] == "Three-card spread"
    assert [s["pos"] for s in sp["slots"]] == entries.SPREADS["spread_three"]
    assert [s["card"]["name"] for s in sp["slots"]] == ["The Fool", "Three of Cups", "The World"]
    assert [s["says"] for s in sp["slots"]] == says
    assert sp["slots"][0]["card"]["art"] == "00-the-fool" and "art" not in sp["slots"][1]["card"]
    assert d["art"]["00-the-fool"].startswith("data:image/webp;base64,")
    assert sp["together"] == "they all lean toward people"
    assert item["read"] == SEALED and item["reply"] == "You saw the circle too."
    for name in entries.SPREADS["spread_three"] + ["The Fool", "Three of Cups", "The World"]:
        assert name in html
    for bad in ("http://", "https://", 'src="//', "<link", "@import", "<script src"):
        assert bad not in html, bad
    starts = tags(html)
    assert "img" not in starts and "b" not in starts and EVIL not in html


def test_mirror_spread_read_only_when_it_was_shown(store):
    cross = ["00-the-fool", "01-the-magician", "02-the-high-priestess", "03-the-empress",
             "04-the-emperor"]
    done(store, "spread_cross", spread_answers("spread_cross", ["a", "b", "c", "d", "e"]),
         card=".".join(cross), reply="Are you safe right now?")
    store._x("UPDATE entries SET sealed_read=?", (SEALED,))  # kept, never shown (crisis)
    store.set_meta("crisis_last", (NOW - timedelta(hours=2)).isoformat())  # still open
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert "read" not in item and item["reply"] == "Are you safe right now?"
    assert [s["pos"] for s in item["spread"]["slots"]] == entries.SPREADS["spread_cross"]


def test_mirror_spread_keeps_words_when_answers_dont_pair(store):
    # a sheet that changed since: the answers can't be paired, but their words still show
    done(store, "spread_three", [4, "the leap I took", "they all lean toward people", 6],
         card=".".join(THREE))
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert [x["a"] for x in item["qa"]] == ["the leap I took", "they all lean toward people"]
    assert all(s["says"] is None for s in item["spread"]["slots"])


def test_mirror_spread_slot_keeps_its_position(store):
    done(store, "spread_three", spread_answers("spread_three", ["a", "b", "c"]),
         card="00-the-fool.NOT_A_CARD.21-the-world")
    [item] = data_of(mirror.render(store, NOW))["entries"]
    slots = item["spread"]["slots"]
    assert [(s["n"], s["pos"], s["says"]) for s in slots] == [
        (1, "What's behind you", "a"), (3, "What's next", "c")]
    assert "'slot p' + x.n" in mirror._RENDER  # the grid area follows the position


def test_fallback_card_names_match_the_page():
    assert mirror._card_name("cups-03-three") == "Three of Cups"
    assert mirror._card_name("pentacles-14-king") == "King of Pentacles"
    assert mirror._card_name("00-the-fool") == "The Fool"
    assert mirror._card_name("10-wheel-of-fortune") == "Wheel of Fortune"


def test_patterns_only_when_present(store):
    done(store, "check_story", STORY)
    assert data_of(mirror.render(store, NOW))["patterns"] is None
    store.add_entry("patterns", entries.new_token(), "patterns", status="done",
                    answers=[{"text": "Quiet moments with Riley read as rejection.",
                              "dates": ["2026-11-01", "2026-11-05"]}])
    d = data_of(mirror.render(store, NOW))
    assert d["patterns"]["items"][0]["text"].startswith("Quiet moments")
    assert all(i["type"] != "patterns" for i in d["entries"])


def test_forget_regenerates_mirror(store):
    tg, cfg = FakeTG(), Config()
    answers = list(STORY)
    answers[1] = "planning Riley's birthday"
    done(store, "check_story", answers)
    mirror.write(store, NOW)
    assert "Riley's birthday" in mirror.path().read_text(encoding="utf-8")
    commands.handle("/forget last", store, cfg, NOW, tg, 1)
    assert "Riley's birthday" not in mirror.path().read_text(encoding="utf-8")
    done(store, "check_story", answers)
    mirror.write(store, NOW)
    assert "Riley's birthday" in mirror.path().read_text(encoding="utf-8")
    commands.handle("/forget all", store, cfg, NOW, tg, 1)
    commands.handle("FORGET", store, cfg, NOW, tg, 1)
    assert mirror.path().exists()
    assert "Riley's birthday" not in mirror.path().read_text(encoding="utf-8")


def test_forget_removes_mirror_if_it_cannot_be_rewritten(store, monkeypatch):
    done(store, "check_story", STORY)
    mirror.write(store, NOW)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(mirror, "render", boom)
    commands.handle("/forget day", store, Config(), NOW, FakeTG(), 1)
    assert not mirror.path().exists()  # never a stale copy of what was forgotten


def test_mirror_command(store):
    tg = FakeTG()
    done(store, "check_story", STORY)
    assert commands.handle("/mirror", store, Config(), NOW, tg, 1)
    assert tg.sent[-1].startswith("Open Tilia Mirror on your Mac") and tg.sent[-1] == mirror.REPLY
    assert mirror.path().exists()
    assert "/mirror" in commands.HELP


def test_alias_symlink(home, tmp_path):
    h = tmp_path / "me"
    h.mkdir()
    link = mirror.ensure_alias(h)
    assert link == h / mirror.ALIAS_NAME and link.is_symlink()
    assert link.resolve() == mirror.path().resolve()
    link.unlink()
    link.symlink_to(tmp_path / "elsewhere.html")
    assert mirror.ensure_alias(h).resolve() == mirror.path().resolve()  # a symlink is replaced
    link.unlink()
    link.write_text("mine")
    assert mirror.ensure_alias(h) is None
    assert link.read_text() == "mine" and not link.is_symlink()


def test_mirror_never_in_icloud_folders(home, tmp_path):
    assert "Desktop" not in mirror.path().parts and "Documents" not in mirror.path().parts
    h = tmp_path / "me"
    h.mkdir()
    assert mirror.ensure_alias(h).parent == h


def test_main_writes_mirror_and_alias(store, tmp_path, monkeypatch):
    from tilia import __main__
    monkeypatch.setenv("HOME", str(tmp_path))
    done(store, "check_story", STORY)
    assert __main__.main(["mirror"]) == 0
    assert mirror.path().exists()
    assert (tmp_path / mirror.ALIAS_NAME).resolve() == mirror.path().resolve()


# ---- install / uninstall and the home-folder alias -------------------------------------

def test_install_runs_the_mirror_from_the_repo_folder():
    inst = (paths.REPO_ROOT / "scripts" / "install.sh").read_text()
    assert '(cd "$REPO" && "$PY" -m tilia mirror)' in inst


@pytest.mark.real_subprocess  # bash, with launchctl faked
def test_uninstall_removes_only_an_alias_into_the_data_folder(tmp_path):
    import os
    import subprocess
    fake_home, data, fakebin = tmp_path / "home", tmp_path / "data", tmp_path / "bin"
    for d in (fake_home, data, fakebin):
        d.mkdir()
    (fakebin / "launchctl").write_text("#!/bin/bash\nexit 0\n")
    (fakebin / "launchctl").chmod(0o755)
    (data / "mirror.html").write_text("<p>mirror</p>")
    alias = fake_home / mirror.ALIAS_NAME
    env = {**os.environ, "HOME": str(fake_home), "TILIA_HOME": str(data),
           "PATH": f"{fakebin}:{os.environ['PATH']}"}

    def uninstall():
        r = subprocess.run(["bash", str(paths.REPO_ROOT / "scripts" / "uninstall.sh")],
                           env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        return r.stdout

    alias.symlink_to(data / "mirror.html")
    assert "Tilia Mirror" in uninstall()
    assert not alias.is_symlink() and (data / "mirror.html").is_file()

    elsewhere = tmp_path / "theirs.html"
    elsewhere.write_text("mine")
    alias.symlink_to(elsewhere)
    uninstall()
    assert alias.is_symlink()  # points outside the data folder: not ours
    alias.unlink()

    alias.write_text("a real file of theirs")
    uninstall()
    assert alias.read_text() == "a real file of theirs"
    alias.unlink()

    alias.symlink_to(data / "mirror.html")
    (data / "mirror.html").unlink()
    data.rmdir()  # data folder already deleted: the dangling alias still goes
    uninstall()
    assert not alias.is_symlink()

    alias.symlink_to(data / "mirror.html")
    env["TILIA_HOME"] = str(data) + "/"  # a trailing slash, folder already gone
    uninstall()
    assert not alias.is_symlink()

    uninstall()  # nothing there: still fine


# ---- v0.2.1 fix 4: a read that was held back shows once it's safe ----------------------

HELD_LABEL = "Tilia's read, written before you looked"


@pytest.mark.parametrize("type_,card", [("tarot", "00-the-fool"),
                                        ("spread_three", ".".join(THREE))])
def test_mirror_shows_a_held_back_read_once_the_episode_is_over(store, type_, card):
    answers = ([4, None, "a dog", "Denver", [], "", 6] if type_ == "tarot"
               else spread_answers(type_, ["a", "b", "c"]))
    done(store, type_, answers, card=card, reply="Are you safe right now?")
    store._x("UPDATE entries SET sealed_read=?", (SEALED,))
    # during the episode: kept, never shown
    store.set_meta("crisis_last", (NOW - timedelta(hours=2)).isoformat())
    html = mirror.render(store, NOW)
    [item] = data_of(html)["entries"]
    assert "held_read" not in item and "read" not in item and SEALED not in html
    # the episode ended (24 h after the last crisis reply): the read shows, labelled
    store.set_meta("crisis_last", (NOW - timedelta(hours=25)).isoformat())
    html = mirror.render(store, NOW)
    [item] = data_of(html)["entries"]
    assert item["held_read"] == SEALED and "read" not in item
    assert item["reply"] == "Are you safe right now?"
    assert HELD_LABEL in mirror._RENDER


def test_mirror_shows_a_read_held_back_by_a_reply_fallback(store):
    done(store, "tarot", [4, None, "a dog", "Denver", [], "", 6], card="00-the-fool",
         reply="I couldn't put a proper reply together for that one.")
    store._x("UPDATE entries SET sealed_read=?", (SEALED,))
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert item["held_read"] == SEALED


def test_mirror_never_doubles_a_read_that_was_shown(store):
    reply = f"Here's the read I wrote before you looked:\n\n{SEALED}\n\nYou saw it too."
    done(store, "tarot", [4, None, "a dog", "Denver", [], "", 6], card="00-the-fool",
         reply=reply)
    store._x("UPDATE entries SET sealed_read=?", (SEALED,))
    [item] = data_of(mirror.render(store, NOW))["entries"]
    assert "held_read" not in item and item["reply"] == reply


def test_mirror_follows_the_dark_setting_and_keeps_card_paper(store):
    """D16: the mirror follows prefers-color-scheme: warm charcoal and cream, the same
    green lifted for contrast; blots and cards stay on their own paper."""
    done(store, "blot", BLOT, seed=777)
    html = mirror.render(store, NOW)
    dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}", html, re.S)
    assert dark, "no dark palette"
    block = dark.group(1)
    for token in ("color-scheme: dark", "--paper: #23211d", "--ink: #ebe5d6", "--green: #8cbba5"):
        assert token in block
    assert "--blot-paper" not in block
    assert re.search(r"\.paper-face \{[^}]*color: var\(--card-ink\)", html)
