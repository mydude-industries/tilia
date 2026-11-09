import json

import pytest

from tilia import entries, paths

GOOD_ID = "abcdEFGH_-12"


def _payload(**kw):
    d = {"v": 1, "type": "check_story", "id": GOOD_ID, "answers": []}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def _sentences(s):
    return sum(s.count(c) for c in ".?!")


def test_check_story_sheet_valid():
    sheet = entries.load_sheet("check_story")
    cards = sheet["cards"]
    assert cards[0]["kind"] == "pitch"
    assert cards[1]["kind"] == "mood" and cards[-1]["kind"] == "mood"
    for c in entries.answer_cards(sheet):
        assert len(c["why"]) <= 140 and _sentences(c["why"]) == 1 and c["why"][-1] in ".?!"
    assert sum(c.get("max_chars", 0) for c in cards) <= 1800
    assert any(c["personal"] for c in cards)
    assert "check_story" in entries.available_types()


CBT_TYPES = [t for t in entries.TYPES if t not in entries.CARD_TYPES]


@pytest.mark.parametrize("type_", CBT_TYPES)
def test_every_cbt_sheet_valid(type_):
    sheet = entries.load_sheet(type_)
    cards = sheet["cards"]
    assert cards[0]["kind"] == "pitch"
    assert cards[1]["kind"] == "mood" and cards[-1]["kind"] == "mood"
    for c in entries.answer_cards(sheet):
        assert len(c["why"]) <= 140 and _sentences(c["why"]) == 1 and c["why"][-1] in ".?!"
    assert sum(c.get("max_chars", 0) for c in cards) <= 1800
    assert any(c["personal"] for c in cards)
    assert type_ in entries.available_types()
    # R4: the pitch is what the person reads first, so the card shows the same pitch,
    # and it fits the personalized-wording limit.
    assert cards[0]["prompt"] == sheet["pitch"]
    assert len(sheet["pitch"]) <= entries.WORDING_MAX


@pytest.mark.parametrize("type_", CBT_TYPES)
def test_cbt_sheets_have_personal_and_generic(type_):
    sheet = entries.load_sheet(type_)
    assert sum(c["personal"] for c in sheet["cards"]) >= 1
    for c in sheet["cards"]:
        assert isinstance(c["prompt"], str) and c["prompt"].strip()


def test_coach_prompt_names_every_type():
    coach_md = (paths.REPO_ROOT / "prompts" / "coach.md").read_text(encoding="utf-8")
    for t in entries.TYPES:
        assert f"`{t}`" in coach_md, t


def test_ride_wave_never_promises_the_feeling_comes_down():
    """It's sent when a feeling is loudest; if it doesn't drop, the copy must not read as
    failure. It may shift, and noticing either way is useful."""
    sheet = entries.load_sheet("ride_wave")
    words = " ".join([sheet["pitch"]] + [c[k] for c in sheet["cards"]
                                         for k in ("prompt", "why")]).lower()
    for promise in ("came down", "come down", "peaks and passes", "ride it down", "dropped",
                    "start to pass", "while it settles", "turns the feeling down"):
        assert promise not in words, promise
    assert "may shift" in sheet["pitch"] and "either way" in sheet["pitch"]


def test_blot_sheet_shape():
    sheet = entries.load_sheet("blot")
    kinds = [c["kind"] for c in sheet["cards"]]
    assert kinds == ["pitch", "mood", "reveal", "text", "text", "chips", "text", "mood"]
    assert not any(c["personal"] for c in sheet["cards"])  # a blot has no wording
    assert sheet["cards"][0]["prompt"] == sheet["pitch"]
    assert "blot" in entries.available_types()
    text = json.dumps(sheet).lower()
    for word in ("rorschach", "test", "score", "diagnos"):
        assert word not in text, word


def test_available_types_has_all_cbt():
    assert set(CBT_TYPES) <= set(entries.available_types())


def _write(tmp_path, monkeypatch, cards, **top):
    sheet = {"type": "check_story", "v": 1, "title": "T", "pitch": "p", "cards": cards}
    sheet.update(top)
    (tmp_path / "check_story.json").write_text(json.dumps(sheet))
    monkeypatch.setattr(entries, "SHEET_DIR", tmp_path)


def _base():
    pitch = {"id": "p", "kind": "pitch", "short": "p", "prompt": "hi", "why": "", "personal": False}
    mood = {"id": "m1", "kind": "mood", "short": "feeling", "prompt": "How do you feel right now?",
            "why": "It shows what moved.", "personal": False, "min": 0, "max": 10}
    mood2 = dict(mood, id="m2")
    text = {"id": "t", "kind": "text", "short": "what", "prompt": "What?",
            "why": "It gets it out.", "personal": False, "max_chars": 100,
            "options": ["It was late", "Nothing much", "I'm not sure"], "option_mode": "answer"}
    return pitch, mood, text, mood2


def test_load_sheet_rejects_bad(tmp_path, monkeypatch):
    pitch, mood, text, mood2 = _base()
    _write(tmp_path, monkeypatch, [pitch, mood, text, mood2])
    assert entries.load_sheet("check_story")["title"] == "T"
    for bad in (
        [pitch, mood, dict(text, kind="nope"), mood2],
        [pitch, mood, {k: v for k, v in text.items() if k != "why"}, mood2],
        [text, mood, mood2],
        # R24: every text card carries 3-5 one-line tap options and a mode
        [pitch, mood, {k: v for k, v in text.items() if k != "options"}, mood2],
        [pitch, mood, {k: v for k, v in text.items() if k != "option_mode"}, mood2],
        [pitch, mood, dict(text, options=["a", "b"]), mood2],
        [pitch, mood, dict(text, options=["a", "b", "c", "d", "e", "f"]), mood2],
        [pitch, mood, dict(text, options=["a", "b", "x" * 81]), mood2],
        [pitch, mood, dict(text, options=["a", "b", "c\nd"]), mood2],
        [pitch, mood, dict(text, options=["a", "b", " "]), mood2],
        [pitch, mood, dict(text, options=["a", "a", "b"]), mood2],
        [pitch, mood, dict(text, option_mode="guess"), mood2],
        # a starter is the start of their own sentence: it ends with an ellipsis
        [pitch, mood, dict(text, option_mode="starter",
                           options=["They said…", "I walked in and…", "It started"]), mood2],
    ):
        _write(tmp_path, monkeypatch, bad)
        with pytest.raises(ValueError):
            entries.load_sheet("check_story")


def test_sheet_version_is_a_positive_int(tmp_path, monkeypatch):
    pitch, mood, text, mood2 = _base()
    _write(tmp_path, monkeypatch, [pitch, mood, text, mood2], v=2)
    assert entries.load_sheet("check_story")["v"] == 2
    for v in (0, "2", True, None):
        _write(tmp_path, monkeypatch, [pitch, mood, text, mood2], v=v)
        with pytest.raises(ValueError):
            entries.load_sheet("check_story")


def test_starter_card_accepted(tmp_path, monkeypatch):
    pitch, mood, text, mood2 = _base()
    starter = dict(text, option_mode="starter",
                   options=["They said…", "I walked in and…", "It started when…"])
    _write(tmp_path, monkeypatch, [pitch, mood, starter, mood2])
    assert entries.load_sheet("check_story")["cards"][2]["option_mode"] == "starter"


@pytest.mark.parametrize("type_", entries.TYPES)
def test_every_text_card_is_tap_first(type_):
    """R24: no text card asks for typing alone; each offers 3-5 short taps."""
    sheet = entries.load_sheet(type_)
    assert sheet["v"] >= 2  # every sheet changed in Task 17
    for c in sheet["cards"]:
        if c["kind"] != "text":
            assert "options" not in c and "option_mode" not in c, c["id"]
            continue
        opts = c["options"]
        assert 3 <= len(opts) <= 5 and len(set(opts)) == len(opts), c["id"]
        assert all(1 <= len(o) <= entries.OPTION_MAX and "\n" not in o for o in opts)
        assert c["option_mode"] in ("answer", "starter")
        if c["option_mode"] == "starter":
            assert all(o.endswith("…") for o in opts), c["id"]


@pytest.mark.parametrize("type_,card", [("check_story", "happened"), ("week_back", "hard")])
def test_what_happened_cards_offer_starters_only(type_, card):
    """Tilia never puts words in their mouth about what happened: sentence starters only."""
    [c] = [c for c in entries.load_sheet(type_)["cards"] if c["id"] == card]
    assert c["option_mode"] == "starter"


@pytest.mark.parametrize("type_", CBT_TYPES)
def test_cbt_sheets_are_short(type_):
    """R24: 5-6 cards in all, pitch and both feeling cards included (check_story keeps
    its evidence card: 7)."""
    cards = entries.load_sheet(type_)["cards"]
    assert 5 <= len(cards) <= (7 if type_ == "check_story" else 6)
    assert [c["kind"] for c in cards][:2] == ["pitch", "mood"] and cards[-1]["kind"] == "mood"


def test_available_types_follows_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(entries, "SHEET_DIR", tmp_path)
    assert entries.available_types() == []


@pytest.mark.parametrize("data", [
    "not json", "[1]", _payload(v=2), _payload(id="a/b"), _payload(type="nope"),
    _payload(answers="x"), _payload(id="short"),
])
def test_parse_payload_rejects(data):
    assert entries.parse_payload(data) is None


def test_parse_payload_ok():
    assert entries.parse_payload(_payload())["id"] == GOOD_ID


def test_payload_limit_is_bytes():
    emoji = _payload(answers=["é😀" * 1400])
    assert len(emoji) < 4096 < len(emoji.encode())
    assert entries.parse_payload(emoji) is None
    base = len(_payload(answers=[""]).encode())
    ok = _payload(answers=["a" * (4096 - base)])
    assert len(ok.encode()) == 4096
    assert entries.parse_payload(ok) is not None
    assert entries.parse_payload(_payload(answers=["a" * (4097 - base)])) is None


def _sheet():
    return {"type": "check_story", "title": "Check the story", "cards": [
        {"id": "p", "kind": "pitch"},
        {"id": "m1", "kind": "mood", "short": "feeling", "min": 0, "max": 10},
        {"id": "t", "kind": "text", "short": "what happened", "max_chars": 10},
        {"id": "c", "kind": "chips", "short": "other reads", "chips": ["a", "b"], "multi": True},
        {"id": "s", "kind": "slider", "short": "sure", "min": 0, "max": 100},
        {"id": "m2", "kind": "mood", "short": "feeling", "min": 0, "max": 10},
    ]}


def test_validate_answers_trims_and_cuts():
    s = _sheet()
    out = entries.validate_answers(s, [3, "  hi  ", ["a"], 50, 6])
    assert out == [3, "hi", ["a"], 50, 6]
    assert entries.validate_answers(s, [3, "x" * 5000, [], 1, 6])[1] == "x" * 10
    assert entries.validate_answers(s, [3, "", ["a", "zzz", "b"], 1, 6])[2] == ["a", "b"]
    assert entries.validate_answers(s, [11, "", [], 1, 6])[0] is None
    assert entries.validate_answers(s, [3, "", [], 101, True])[3:] == [None, None]
    assert entries.validate_answers(s, [3, "x"]) is None


def test_compact_shape():
    s = _sheet()
    out = entries.compact(s, [3, "he left", ["a"], 50, 6])
    assert out.startswith("[check the story] ")
    assert "feeling 3→6" in out and "what happened: he left" in out
    assert len(out) <= 1500
    assert len(entries.compact(s, [3, "x" * 10, ["a"], 50, 6])) <= 1500
    assert entries.moods(s, [3, "", [], 1, 6]) == (3, 6)


def test_clean_wording():
    s = {"cards": [{"id": "a", "personal": True}, {"id": "b", "personal": False}]}
    ok = entries.clean_wording(s, {"pitch": "About Riley", "prompts": {"a": "x", "b": "y"}})
    assert ok == {"pitch": "About Riley", "prompts": {"a": "x"}}
    assert entries.clean_wording(s, {"pitch": "x" * 201, "prompts": {"a": "x"}}) == {"prompts": {"a": "x"}}
    assert entries.clean_wording(s, {"pitch": "a\nb", "prompts": {"a": "x"}}) == {"prompts": {"a": "x"}}
    assert entries.clean_wording(s, {"pitch": "a\nb", "prompts": {"a": "x" * 201, "b": "y"}}) is None
    assert entries.clean_wording(s, "junk") is None


OPTS = ["Riley might have been tired", "It was about the dinner, not me", "I don't know yet"]
STARTS = ["When Riley got home…", "At dinner she…", "It started when…"]


def _optsheet():
    return {"cards": [
        {"id": "a", "kind": "text", "personal": True, "option_mode": "answer"},
        {"id": "h", "kind": "text", "personal": True, "option_mode": "starter"},
        {"id": "b", "kind": "text", "personal": False, "option_mode": "answer"},
        {"id": "c", "kind": "chips", "personal": True}]}


def test_clean_wording_keeps_good_options():
    s = _optsheet()
    assert entries.clean_wording(s, {"options": {"a": OPTS}}) == {"options": {"a": OPTS}}
    # a non-personal card, a chips card and an unknown id never get options
    out = entries.clean_wording(s, {"options": {"b": OPTS, "c": OPTS, "zz": OPTS, "a": OPTS}})
    assert out == {"options": {"a": OPTS}}


@pytest.mark.parametrize("bad", [
    OPTS[:2],                                   # too few
    OPTS + ["d", "e", "f"],                     # too many
    OPTS[:2] + ["x" * 81],                      # too long
    OPTS[:2] + ["two\nlines"],                  # not one line
    OPTS[:2] + [7],                             # not text
    "Riley might have been tired",              # not a list
    [OPTS[0], OPTS[0], OPTS[1]],                # repeats
])
def test_clean_wording_drops_bad_options(bad):
    assert entries.clean_wording(_optsheet(), {"options": {"a": bad}}) is None


def test_clean_wording_never_tailors_starters():
    """R25: a what-happened card's starters are fixed and generic. Tilia never offers its
    own version of what happened, not even as the start of a sentence."""
    for tailored in (STARTS, ["Riley ignored you", "She was angry", "You said it wrong"],
                     ["It started when…", "Then…", "They said…"]):
        assert entries.clean_wording(_optsheet(), {"options": {"h": tailored}}) is None
    out = entries.clean_wording(_optsheet(), {"options": {"h": STARTS, "a": OPTS}})
    assert out == {"options": {"a": OPTS}}


@pytest.mark.parametrize("type_", entries.TYPES)
def test_starters_are_short_and_generic(type_):
    """At most 4 words before the ellipsis: room to begin, nothing said for them."""
    for c in entries.load_sheet(type_)["cards"]:
        if c.get("option_mode") == "starter":
            for o in c["options"]:
                assert len(o[:-1].split()) <= entries.STARTER_WORDS, (c["id"], o)


def test_load_sheet_rejects_long_starter(tmp_path, monkeypatch):
    pitch, mood, text, mood2 = _base()
    long = dict(text, option_mode="starter",
                options=["They said…", "I walked in and…", "When Riley got home from work…"])
    _write(tmp_path, monkeypatch, [pitch, mood, long, mood2])
    with pytest.raises(ValueError):
        entries.load_sheet("check_story")


def test_check_story_facts_prompt_is_never_reworded():
    sheet = entries.load_sheet("check_story")
    [c] = [c for c in sheet["cards"] if c["id"] == "happened"]
    assert c["personal"] is False and c["option_mode"] == "starter"
    assert c["options"] == ["They said…", "I walked in and…", "It started when…", "Then…"]
    [ev] = [c for c in sheet["cards"] if c["id"] == "evidence"]
    assert ev["prompt"] == "What fits that story, and what doesn't?"
    assert ev["option_mode"] == "answer"


def test_clean_wording_keeps_the_fragment_small():
    """The whole wording fits WORDING_BYTES: taps go first (last card first), then
    personalized prompts (last card first); the pitch stays."""
    long = [("é" * 79) + str(n) for n in range(5)]
    sheet = {"cards": [{"id": f"c{n}", "kind": "text", "personal": True, "option_mode": "answer"}
                       for n in range(8)]}
    raw = {"pitch": "p" * 200, "prompts": {f"c{n}": "q" * 200 for n in range(3)},
           "options": {f"c{n}": long for n in range(8)}}
    out = entries.clean_wording(sheet, raw)
    size = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= entries.WORDING_BYTES
    assert out["pitch"] == "p" * 200 and len(out["prompts"]) == 3  # wording before taps
    assert 0 < len(out["options"]) < 8
    assert list(out["options"]) == [f"c{n}" for n in range(len(out["options"]))]
    # prompts alone over the cap: they go too, last first, and the pitch stays
    raw = {"pitch": "😀" * 200, "prompts": {f"c{n}": "é" * 200 for n in range(8)},
           "options": {"c0": long}}
    out = entries.clean_wording(sheet, raw)
    assert "options" not in out and out["pitch"] == "😀" * 200
    assert list(out["prompts"]) == [f"c{n}" for n in range(len(out["prompts"]))]
    assert len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) \
        <= entries.WORDING_BYTES


def test_fits_pairs_only_matching_answers():
    s = _sheet()
    assert entries.fits(s, [3, "hi", ["a"], 50, 6])
    assert entries.fits(s, [None, "", [], None, None])
    assert not entries.fits(s, [3, "hi", ["a"], 50])          # wrong count
    assert not entries.fits(s, [3, ["a"], "hi", 50, 6])       # same count, wrong kinds
    assert not entries.fits(s, [3, "hi", ["a"], "50", 6])
    assert not entries.fits(s, None)


def test_compact_old_answers_against_new_sheet():
    """An answer stored against an older sheet never lands under the wrong question."""
    out = entries.compact(_sheet(), [3, ["a"], "he left", 50, 6])
    assert out.startswith("[check the story] ") and "what happened" not in out
    assert "he left" in out and "a" in out


def test_unpaired_keeps_their_words():
    """R26: answers that don't pair with the current sheet are kept, bounded, unpaired."""
    sheet = _sheet()  # 5 answer cards
    raw = [3, "  he left  ", 70, ["a", " b ", 7], "x" * 5000, None, True, {"k": 1}, 6.5, 6]
    out = entries.unpaired(raw)
    assert out == [3, "he left", 70, ["a", "b"], "x" * entries.TEXT_MAX, None, None, None,
                   None, 6]
    assert not entries.fits(sheet, out)
    assert entries.moods(sheet, out) == (None, None)  # positions aren't trusted
    assert entries.unpaired_moods(out) == (3, 6)
    assert entries.unpaired_moods([11, "x", -1]) == (None, None)
    assert entries.unpaired("nope") is None


def test_tokens():
    assert len(entries.new_token()) == 12
    assert 1 <= entries.new_seed() <= 2**31 - 1


# ---- spreads (D13) ------------------------------------------------------------------

SPREADS = {"spread_three": ["What's behind you", "Where you are", "What's next"],
           "spread_cross": ["The heart of it", "What crosses it", "The root",
                            "What's being asked of you",
                            "Where this is heading if nothing changes"],
           "spread_relationship": ["You", "Them", "The space between"]}


def test_spreads_defined():
    assert entries.SPREADS == SPREADS
    for t, positions in SPREADS.items():
        assert t in entries.TYPES and t in entries.CARD_TYPES
        assert t in entries.available_types()
        sheet = entries.load_sheet(t)
        kinds = [c["kind"] for c in sheet["cards"]]
        assert kinds == (["pitch", "mood"] + ["reveal", "text"] * len(positions)
                         + ["text", "mood"])
        reveals = [c for c in sheet["cards"] if c["kind"] == "reveal"]
        assert [c["prompt"] for c in reveals] == positions
        texts = [c for c in sheet["cards"] if c["kind"] == "text"]
        assert [c["prompt"] for c in texts[:-1]] == (
            ["What does this one say to you here?"] * len(positions))
        assert texts[-1]["prompt"] == "Looking at all of them together, what do you notice?"
        assert sum(c["max_chars"] for c in texts) <= entries.TOTAL_TEXT_MAX
        assert sheet["cards"][0]["prompt"] == sheet["pitch"]
        assert len(sheet["pitch"]) <= entries.WORDING_MAX
        flat = json.dumps(sheet).lower()
        for word in ("outcome", "future", "will happen"):
            assert word not in flat, (t, word)


@pytest.mark.parametrize("type_", list(SPREADS))
@pytest.mark.parametrize("ch", ["a", "é", "あ", "😀"])
def test_spread_payload_fits(type_, ch):
    """Every text card filled to its max_chars (as the page counts them: UTF-16 units),
    in the widest characters, still fits the 4096-byte payload."""
    sheet = entries.load_sheet(type_)
    units = len(ch.encode("utf-16-le")) // 2
    answers = []
    for c in entries.answer_cards(sheet):
        if c["kind"] == "mood":
            answers.append(10)
        elif c["kind"] == "text":
            answers.append(ch * (c["max_chars"] // units))
        else:
            answers.append(None)
    data = json.dumps({"v": 1, "type": type_, "id": GOOD_ID, "answers": answers},
                      ensure_ascii=False, separators=(",", ":"))
    assert len(data.encode()) <= entries.PAYLOAD_MAX
    obj = entries.parse_payload(data)
    assert obj is not None
    assert entries.validate_answers(sheet, obj["answers"]) == answers
