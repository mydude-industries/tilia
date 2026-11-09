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
            "why": "It gets it out.", "personal": False, "max_chars": 100}
    return pitch, mood, text, mood2


def test_load_sheet_rejects_bad(tmp_path, monkeypatch):
    pitch, mood, text, mood2 = _base()
    _write(tmp_path, monkeypatch, [pitch, mood, text, mood2])
    assert entries.load_sheet("check_story")["title"] == "T"
    for bad in (
        [pitch, mood, dict(text, kind="nope"), mood2],
        [pitch, mood, {k: v for k, v in text.items() if k != "why"}, mood2],
        [text, mood, mood2],
    ):
        _write(tmp_path, monkeypatch, bad)
        with pytest.raises(ValueError):
            entries.load_sheet("check_story")


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
