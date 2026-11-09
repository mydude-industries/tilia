import base64
import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tilia import coach, entries, mirror, outbox, paths, profile, telegram, webapp, worksheets
from tilia.claude import LimitError, MissingCLI, NotLoggedIn, UnsupportedCLI, parse_coach
from tilia.config import Config
from tilia.store import Store

NOW = datetime(2026, 11, 7, 12, 0).astimezone()
H = timedelta(hours=1)
TOMORROW = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")
PROMPTS = paths.REPO_ROOT / "prompts"
COACH_SYS = (PROMPTS / "coach.md").read_text(encoding="utf-8")
FOCUS = "the Sunday dinner with Riley"
STORY = "What did you tell yourself when Riley went quiet at Sunday dinner?"
GOOD = {"pitch": "A few quick questions about Sunday dinner with Riley.",
        "prompts": {"story": STORY},
        "options": {"story": ["She's angry with me", "I said the wrong thing again",
                              "She'd rather not be with me"]}}


def ws_sys():
    return (PROMPTS / "worksheet.md").read_text(encoding="utf-8")


def tarot_sys():
    return (PROMPTS / "tarot_read.md").read_text(encoding="utf-8")


@pytest.fixture
def store(home):
    return Store(paths.db_path(), clock=lambda: NOW)


def cfg(**kw):
    base = dict(tone="direct_soft", referrals="never", goal="be calmer when Riley goes quiet",
                threads=[{"name": "Riley", "chat_id": 7}])
    base.update(kw)
    return Config(**base)


def spread_sys():
    return (PROMPTS / "spread_read.md").read_text(encoding="utf-8")


def fake(coach_out, wording=None, calls=None, read=None, spread=None):
    """subprocess.run stand-in that answers by which system prompt is used."""
    def run(cmd, **kw):
        system = cmd[cmd.index("--system-prompt") + 1]
        if calls is not None:
            calls.append((system, kw["input"]))
        if system == COACH_SYS:
            out = coach_out
        elif system == ws_sys():
            out = wording if wording is not None else GOOD
        elif system == tarot_sys():
            out = read if read is not None else {"read": SEALED}
        elif system == spread_sys():
            out = spread if spread is not None else {"read": SPREAD_SEALED}
        else:
            raise AssertionError("unexpected system prompt")
        result = out if isinstance(out, str) else json.dumps(out)
        return SimpleNamespace(stdout=json.dumps({"result": result, "is_error": False}),
                               stderr="", returncode=0)
    return run


def coach_json(text="here's one", state="ok", **kw):
    return {"reply": text, "state": state, **kw}


ASSIGNED = coach_json("Try this before Sunday.", new_assignment={
    "kind": "worksheet", "type": "check_story", "focus": FOCUS, "due": TOMORROW})
ASKED = coach_json("Here you go.", worksheet={"type": "check_story", "focus": FOCUS})
ASKED_GENERIC = coach_json("Here you go.", worksheet={"type": "check_story", "focus": None})


def say(store, run, c=None, text="give me a worksheet"):
    r = coach.respond(store, c or cfg(), NOW, [text], run=run)
    outbox.enqueue_reply(store, r, NOW)
    return r


def box(store):
    return json.loads(store.get_meta("outbox") or "[]")


def url_of(item):
    return item["markup"]["keyboard"][0][0]["web_app"]["url"]


def fragment(url):
    tw = url.split("#tw=", 1)[1]
    return json.loads(base64.urlsafe_b64decode(tw + "=" * (-len(tw) % 4)))


def test_parse_worksheet_fields():
    r = parse_coach(json.dumps(coach_json(new_assignment={
        "kind": "worksheet", "type": "check_story", "focus": FOCUS, "due": TOMORROW})))
    assert r.new_assignment == {"kind": "worksheet", "type": "check_story", "focus": FOCUS,
                                "due": TOMORROW}
    r = parse_coach(json.dumps(coach_json(new_assignment={
        "kind": "worksheet", "type": "horoscope", "focus": None, "due": None})))
    assert r.new_assignment is None
    r = parse_coach(json.dumps(coach_json(worksheet={"type": "again"})))
    assert r.worksheet == {"type": "again", "focus": None}
    r = parse_coach(json.dumps(coach_json(worksheet={"type": "nope", "focus": None})))
    assert r.worksheet is None
    r = parse_coach(json.dumps(coach_json(themes=["anger", "jealousy", "rest", "loss", "hope"])))
    assert r.themes == ["anger", "rest", "loss"]
    r = parse_coach(json.dumps(coach_json(themes=["anger", "anger", "rest"])))
    assert r.themes == ["anger", "rest"]
    r = parse_coach(json.dumps(coach_json(themes="anger")))
    assert r.themes == [] and r.offer is None and r.remove_keyboard is False
    assert r.intent_signal is None
    # the older kinds still need their text
    r = parse_coach(json.dumps(coach_json(new_assignment={"kind": "challenge", "due": None})))
    assert r.new_assignment is None


def test_assigned_worksheet_creates_assignment_and_entry(store):
    say(store, fake(ASSIGNED))
    [a] = store.open_assignments()
    assert a.kind == "worksheet" and a.due == TOMORROW
    assert a.text == f"Check the story: {FOCUS}"
    [e] = store.entries()
    assert e.source == "assignment" and e.assignment_id == a.id and e.type == "check_story"
    assert e.turn_id == store.last_turn_id()
    [item] = box(store)
    assert item["text"] == "Try this before Sunday."
    assert f"id={e.token}" in url_of(item)


def test_requested_worksheet_is_entry_only(store):
    say(store, fake(ASKED))
    assert store.open_assignments() == []
    [e] = store.entries()
    assert e.source == "ask" and e.assignment_id is None
    [item] = box(store)
    assert f"id={e.token}" in url_of(item)


def test_no_focus_no_personalizer_call(store):
    calls = []
    say(store, fake(ASKED_GENERIC, calls=calls))
    assert [s for s, _ in calls if s == ws_sys()] == []
    [e] = store.entries()
    assert e.wording is None
    assert "#tw=" not in url_of(box(store)[0])


def test_personalized_wording_kept(store):
    calls = []
    profile_note = "Sam goes quiet under stress."
    profile.write(profile_note)
    say(store, fake(ASKED, calls=calls))
    [(_, prompt)] = [(s, p) for s, p in calls if s == ws_sys()]
    assert coach.DATA_NOTE in prompt and profile_note in prompt and f"<focus>\n{FOCUS}\n</focus>" in prompt
    assert "story:" in prompt and "other_way:" in prompt and "happened:" not in prompt
    # each personal reflection card shows its generic taps; starters are never sent (R25)
    assert "answers: They're upset with me | I messed up again" in prompt
    assert "starters" not in prompt and "They said…" not in prompt
    [e] = store.entries()
    assert e.wording == GOOD
    url = url_of(box(store)[0])
    assert fragment(url) == GOOD
    assert "Riley" not in url.split("#", 1)[0]


@pytest.mark.parametrize("bad", [
    "not json at all",
    {"prompts": {"mood_start": "How do you feel about Riley right now?"}},
    {"pitch": "x" * 300},
    {"options": {"happened": ["Riley ignored you", "She was cold", "You were rude"]}},
    {"prompts": {"happened": "What happened at Sunday dinner with Riley?"}},
])
def test_bad_personalization_is_generic(store, bad):
    say(store, fake(ASKED, wording=bad))
    [e] = store.entries()
    assert e.wording is None
    items = box(store)
    assert len(items) == 1 and "#tw=" not in url_of(items[0])


def test_worksheet_prompt_explains_options():
    """R24: tailored taps, plain words, and only sentence starters where they tell what
    happened."""
    text = ws_sys()
    assert '"options": {"<id>": [' in text and "80 characters" in text
    assert "3 to 5" in text and "[answers: …]" in text
    assert "starter" not in text.lower()  # R25: starters are fixed; the model never sees them
    assert "what happened is theirs to tell" in text


def test_offer_url_carries_sheet_version(store):
    say(store, fake(ASKED))
    q = dict(p.split("=", 1) for p in url_of(box(store)[0]).split("?", 1)[1]
             .split("#", 1)[0].split("&"))
    assert q["v"] == str(entries.load_sheet("check_story")["v"]) == "2"


def test_personalizer_failure_is_generic(store, monkeypatch):
    sheet = entries.load_sheet("check_story")

    def boom(*a, **k):
        raise RuntimeError("claude failed")
    monkeypatch.setattr(worksheets.ai, "ask_json", boom)
    assert worksheets.personalize(cfg(), sheet, FOCUS, None) is None


def test_no_offer_in_crisis_reply(store):
    r = say(store, fake(coach_json("Are you safe right now?", "crisis",
                                   worksheet={"type": "check_story", "focus": None})))
    assert r.offer is None and store.entries() == []
    [item] = box(store)
    # a stale "Open" button from an earlier offer must not survive into a crisis
    assert item["markup"] == telegram.REMOVE_KEYBOARD and "Are you safe right now?" in item["text"]


def test_no_offer_during_episode(store):
    store.set_meta("crisis_last", (NOW - 2 * H).isoformat())
    for out in (ASKED, ASSIGNED):
        store.set_meta("outbox", None)
        r = say(store, fake(out))
        assert r.offer is None and store.entries() == []
        assert store.open_assignments() == []
        [item] = box(store)
        assert item["markup"] == telegram.REMOVE_KEYBOARD and item["text"] == out["reply"]


def test_crisis_reply_takes_away_an_earlier_button(store):
    e = store.entry_by_token(worksheets.prepare(store, cfg(), NOW, "check_story").token)
    store.set_meta("outbox", None)
    say(store, fake(coach_json("I'm here. Are you safe right now?", "crisis")), text="x")
    assert box(store)[-1]["markup"] == telegram.REMOVE_KEYBOARD
    assert store.get_entry(e.id).status == "open"  # the entry itself is untouched


def test_episode_reply_takes_away_the_keyboard_ordinary_reply_does_not(store):
    say(store, fake(coach_json("ok, tell me more")), text="hi")
    assert "markup" not in box(store)[-1]  # outside an episode nothing changes
    store.set_meta("crisis_last", (NOW - 2 * H).isoformat())
    say(store, fake(coach_json("I'm still here.", "struggling")), text="hi")
    assert box(store)[-1]["markup"] == telegram.REMOVE_KEYBOARD


def test_worksheets_off_drops_coach_offers_not_asks(store):
    say(store, fake(ASSIGNED), c=cfg(worksheets=False))
    assert store.entries() == [] and store.open_assignments() == []
    assert "markup" not in box(store)[0]
    store.set_meta("outbox", None)
    say(store, fake(ASKED_GENERIC), c=cfg(worksheets=False))
    [e] = store.entries()
    assert e.source == "ask" and "markup" in box(store)[0]


def test_unavailable_type_is_dropped(store, monkeypatch):
    monkeypatch.setattr(entries, "available_types", lambda: [])
    say(store, fake(ASKED_GENERIC))
    assert store.entries() == [] and "markup" not in box(store)[0]


def test_again_resends_newest_open(store):
    assert worksheets.again(store) is None
    first = worksheets.prepare(store, cfg(), NOW, "check_story")
    second = worksheets.prepare(store, cfg(), NOW, "check_story")
    assert first.token != second.token
    assert worksheets.again(store).token == second.token
    e = store.entry_by_token(second.token)
    assert worksheets.offer_for(store, e).url == second.url
    say(store, fake(coach_json("Here it is again.", worksheet={"type": "again"})))
    assert f"id={second.token}" in url_of(box(store)[0])
    assert len(store.entries()) == 2  # again re-sends; it never makes a new entry


def test_fallback_preface_then_button(store, monkeypatch):
    monkeypatch.setattr(webapp, "FRAGMENT_WORDING", False)
    say(store, fake(ASKED))
    reply_item, preface = box(store)
    assert reply_item["text"] == "Here you go." and "markup" not in reply_item
    assert STORY in preface["text"]
    assert "#tw=" not in url_of(preface)


def test_remove_keyboard_markup(store):
    r = coach.CoachReply("done, thanks", remove_keyboard=True)
    outbox.enqueue_reply(store, r, NOW)
    assert box(store)[0]["markup"] == telegram.REMOVE_KEYBOARD


def test_settings_line_shows_toggles():
    s = coach._settings(cfg(worksheets=False))
    assert ("worksheets: off (when off, offer one only if they ask); surprise cards: on"
            in s)


def test_settings_lists_available_types(monkeypatch):
    s = coach._settings(cfg())
    assert "worksheet types available: " + ", ".join(entries.available_types()) in s
    assert "check_story" in entries.available_types()
    monkeypatch.setattr(entries, "available_types", lambda: [])
    assert "worksheet types available: none" in coach._settings(cfg())
    assert "worksheet types available" in COACH_SYS


@pytest.mark.parametrize("err", [LimitError("resets 3pm"), NotLoggedIn("x"), MissingCLI("x")])
def test_personalizer_limit_or_login_is_generic(store, monkeypatch, err):
    def boom(*a, **k):
        raise err
    monkeypatch.setattr(worksheets.ai, "ask_json", boom)
    sheet = entries.load_sheet("check_story")
    assert worksheets.personalize(cfg(), sheet, FOCUS, None) is None
    offer = worksheets.prepare(store, cfg(), NOW, "check_story", focus=FOCUS)
    assert offer is not None and store.entry_by_token(offer.token).wording is None


# ---- answers come back -----------------------------------------------------

ANSWERS = [3, "Riley went quiet at Sunday dinner", "that I'd said something wrong",
           "she smiled earlier", "  she was tired  ", 6]


def payload(token, type="check_story", answers=ANSWERS, **kw):
    return json.dumps({"v": 1, "type": type, "id": token, "answers": answers, **kw})


def open_entry(store, wording=None):
    token = entries.new_token()
    return store.get_entry(store.add_entry("check_story", token, "ask", wording=wording))


def test_accept_ok_stores_clean_answers(store):
    e = open_entry(store)
    assert worksheets.accept(store, NOW, payload(e.token)) == "ok"
    got = store.get_entry(e.id)
    assert got.status == "answered"
    assert got.answers[2] == "that I'd said something wrong" and got.answers[4] == "she was tired"
    assert (got.mood_before, got.mood_after, got.outcome) == (3, 6, 3)
    assert worksheets.reply_due(store) == [got]
    assert store.get_meta("ignored_payloads") is None


def test_accept_outcomes(store):
    e = open_entry(store)
    assert worksheets.accept(store, NOW, "x" * 10) == "invalid"
    assert worksheets.accept(store, NOW, payload("B" * 12)) == "unknown"
    assert store.get_meta("ignored_payloads") == "2"
    assert worksheets.accept(store, NOW, payload(e.token, type="ride_wave")) == "invalid"
    assert worksheets.accept(store, NOW, payload(e.token, answers="x")) == "invalid"
    assert store.get_entry(e.id).status == "open"
    assert worksheets.accept(store, NOW, payload(e.token)) == "ok"
    assert worksheets.accept(store, NOW, payload(e.token)) == "answered"
    store.finish_entry(e.id, "ok", [], None)
    assert worksheets.accept(store, NOW, payload(e.token)) == "answered"
    old = open_entry(store)
    store._x("UPDATE entries SET created=? WHERE id=?",
             ((NOW - timedelta(days=8)).isoformat(), old.id))
    assert worksheets.accept(store, NOW, payload(old.token)) == "expired"
    assert store.get_entry(old.id).status == "expired"
    assert worksheets.expire(store, NOW) == 0


def test_reply_due_oldest_first(store):
    a, b = open_entry(store), open_entry(store)
    worksheets.accept(store, NOW + H, payload(b.token))
    store._x("UPDATE entries SET answered_at=? WHERE id=?", ((NOW + 2 * H).isoformat(), b.id))
    worksheets.accept(store, NOW, payload(a.token))
    store._x("UPDATE entries SET answered_at=? WHERE id=?", ((NOW + 3 * H).isoformat(), a.id))
    assert [e.id for e in worksheets.reply_due(store)] == [b.id, a.id]


def test_entry_prompt_shows_wording_answers_and_moods(store):
    e = open_entry(store, wording=GOOD)
    worksheets.accept(store, NOW, payload(e.token))
    e = store.get_entry(e.id)
    p = coach.build_prompt(store, cfg(), NOW, ["[check the story] x"], mode="entry", entry=e)
    assert ('MODE: entry. They just finished "Check the story". Answer it the way '
            '"Answering a worksheet" says.') in p
    section = p.split("ENTRY (what they filled out)\n", 1)[1]
    body = section.split("</entry>", 1)[0]
    assert body.startswith("<entry>")
    assert STORY in body  # personalized wording, as they saw it
    assert "What happened? Just the facts, like a camera would see it." in body  # generic
    assert "Riley went quiet at Sunday dinner" in body and "she was tired" in body
    assert "3 → 6" in body
    assert p.index("ENTRY (what they filled out)") < p.index("NEW MESSAGES")
    assert "ENTRY (what they filled out)" not in coach.build_prompt(store, cfg(), NOW, ["hi"])


def test_answer_closes_entry_and_assignment(store):
    aid = store.add_assignment("worksheet", "Check the story: generic", None)
    token = entries.new_token()
    eid = store.add_entry("check_story", token, "assignment", assignment_id=aid)
    worksheets.accept(store, NOW, payload(token))
    calls = []
    run = fake(coach_json("That story was doing a lot of work.", themes=["fear"],
                          remember="intent: say one line before going quiet, Thursday"),
               calls=calls)
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, store.get_entry(eid), run)
    assert r.remove_keyboard is True
    e = store.get_entry(eid)
    assert e.status == "done" and e.themes == ["fear"] and e.followup_signal == r.intent_signal
    assert r.intent_signal is not None
    assert [a.status for a in store.all_assignments()] == ["done"]
    assert worksheets.reply_due(store) == []


def test_intent_signal_set_in_chat_too(store):
    r = coach.respond(store, cfg(), NOW, ["I'll text Riley tonight"],
                      run=fake(coach_json(remember="intent: text Riley tonight")))
    [sig] = store.signals_since(NOW - H)
    assert r.intent_signal == sig["id"]


def test_coach_prompt_has_answering_section():
    sec = COACH_SYS.split("# Answering a worksheet", 1)[1].split("\n# ", 1)[0]
    assert "intent:" in sec and "themes" in sec and "crisis" in sec.lower()


# ---- blots -------------------------------------------------------------------------

ASK_BLOT = coach_json("Here's a blot.", worksheet={"type": "blot", "focus": FOCUS})


def test_blot_offer_has_seed_only(store):
    calls = []
    say(store, fake(ASK_BLOT, calls=calls))
    [e] = store.entries()
    assert e.type == "blot" and e.wording is None
    assert 1 <= e.seed < 2**31
    url = url_of(box(store)[0])
    query = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("#", 1)[0].split("&"))
    assert query["t"] == "blot" and query["seed"] == str(e.seed)
    assert url.split("#", 1)[1:] in ([], [""])  # no fragment: a blot has no wording
    assert [s for s, _ in calls] == [COACH_SYS]  # no personalizer call, even with a focus


# JS palette (mulberry32 over mix32(seed ^ 0x7a11a)) for seeds 1..40, from blot.js.
JS_PALETTES = "bbggbbbggbbgbbbgbbgbbbbbbgbbbgbbbbbbbggg"


def test_blot_palette_matches_js():
    got = "".join(entries.blot_palette(s)[0] for s in range(1, 41))
    assert got == JS_PALETTES
    assert entries.blot_palette(1234567) == "green"
    assert entries.blot_palette(2147483647) == "black"


BLOT_ANSWERS = [4, None, "a moth with torn wings", "two people pulling apart",
                ["Uneasy", "Tender"], "like something about to leave", 6]


def test_blot_answer_prompt_has_seed_not_image(store):
    token = entries.new_token()
    eid = store.add_entry("blot", token, "ask", seed=1234567)
    assert worksheets.accept(store, NOW, payload(token, type="blot", answers=BLOT_ANSWERS)) == "ok"
    e = store.get_entry(eid)
    p = coach.build_prompt(store, cfg(), NOW, [entries.compact(entries.load_sheet("blot"), e.answers)],
                           mode="entry", entry=e)
    body = p.split("ENTRY (what they filled out)\n", 1)[1].split("</entry>", 1)[0]
    assert "seed 1234567" in body and "green and gold" in body
    assert "a moth with torn wings" in body and "Uneasy, Tender" in body
    assert "4 → 6" in body
    assert "(skipped)" not in body  # the blot itself is not an unanswered question
    for image in ("data:", "base64", "rgba", "png"):
        assert image not in p.lower()


def test_coach_blot_stance():
    assert "never score" in COACH_SYS and "never diagnose" in COACH_SYS
    [line] = [s for s in COACH_SYS.replace("\n", " ").split(". ") if "Rorschach" in s]
    assert "never call it" in line


def test_new_assignment_never_a_card(store):
    """D11: cards and blots are entries only, never assignments."""
    for t in entries.CARD_TYPES:
        calls = []
        store.set_meta("outbox", None)
        say(store, fake(coach_json("Try this by Sunday.", new_assignment={
            "kind": "worksheet", "type": t, "focus": FOCUS, "due": TOMORROW}), calls=calls))
        assert store.open_assignments() == [] and store.entries() == []
        assert "markup" not in box(store)[0]
        assert [s for s, _ in calls] == [COACH_SYS]


# ---- tarot -------------------------------------------------------------------------

SEALED = ("The Fool is the first step taken before the ground is certain. With the Denver "
          "move still undecided, it meets you at that edge: wanting to go, and wanting a "
          "guarantee first.")
COMPARISON = ("You went straight to the little dog, the one trying to warn him. I read the "
              "leap; you read the warning. This week, notice when caution speaks first.")
ASK_TAROT = coach_json("Drew you one.", worksheet={"type": "tarot", "focus": None})
TAROT_ANSWERS = [4, None, "the little dog at his heels", "the move to Denver", ["Chest"],
                 "tight, like before a jump", 6]


@pytest.fixture
def fool(monkeypatch):
    card = next(c for c in entries.available_cards() if c["id"] == "00-the-fool")
    monkeypatch.setattr(entries, "available_cards", lambda: [card])
    return card


def query_of(url):
    return dict(p.split("=", 1) for p in url.split("?", 1)[1].split("#", 1)[0].split("&"))


def test_tarot_sheet_shape():
    sheet = entries.load_sheet("tarot")
    kinds = [c["kind"] for c in sheet["cards"]]
    assert kinds == ["pitch", "mood", "reveal", "text", "text", "chips", "text", "mood"]
    [where] = [c for c in sheet["cards"] if c["kind"] == "chips"]
    assert [x.lower() for x in where["chips"]] == ["chest", "throat", "stomach", "shoulders",
                                                   "hands", "nowhere"]
    assert [c["personal"] for c in sheet["cards"]] == [False, False, False, False, True, False,
                                                       False, False]
    assert "tarot" in entries.available_types()


def test_draw_uniform_from_available(monkeypatch, tmp_path):
    import random
    monkeypatch.setattr(entries, "DECK_DIR", tmp_path)
    man = [{"id": i, "name": i, "number": "", "file": f"{i}.webp", "scene": "", "symbols": []}
           for i in ("a", "b", "c", "card-back")]
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    for e in man:
        (tmp_path / e["file"]).write_bytes(b"RIFF")
    rng = random.Random(7)
    counts = {}
    for _ in range(3000):
        c = worksheets.draw(rng)
        counts[c["id"]] = counts.get(c["id"], 0) + 1
    assert set(counts) == {"a", "b", "c"}  # never the back
    assert all(850 <= n <= 1150 for n in counts.values()), counts
    assert worksheets.draw()["id"] in counts  # the default rng works too
    (tmp_path / "manifest.json").write_text("[]")
    assert worksheets.draw() is None


def test_tarot_offer_seals_before_button(store, fool):
    calls = []
    profile.write("Sam is weighing a move to Denver.")
    store.add_turn("user", "can't stop thinking about Denver")
    r = say(store, fake(ASK_TAROT, calls=calls), text="pull me a card")
    tarot_calls = [p for s, p in calls if s == tarot_sys()]
    assert len(tarot_calls) == 1
    [prompt] = tarot_calls
    assert "The Fool" in prompt and fool["scene"] in prompt
    assert coach.DATA_NOTE in prompt and "Sam is weighing a move to Denver." in prompt
    assert "can't stop thinking about Denver" in prompt
    [e] = store.entries()
    assert e.type == "tarot" and e.card == "00-the-fool" and e.sealed_read == SEALED
    assert r.offer.token == e.token
    url = url_of(box(store)[0])
    q = query_of(url)
    assert q["t"] == "tarot" and q["card"] == "00-the-fool" and "seed" not in q
    rest = url.replace(q["id"], "").lower()
    for w in set(re.findall(r"[a-z]{4,}", SEALED.lower())) - {"fool"}:
        assert w not in rest, w


def answered_tarot(store, sealed=SEALED):
    token = entries.new_token()
    eid = store.add_entry("tarot", token, "ask", card="00-the-fool")
    if sealed is not None:
        store.set_sealed_read(eid, sealed)
    assert worksheets.accept(store, NOW, payload(token, type="tarot",
                                                 answers=TAROT_ANSWERS)) == "ok"
    return store.get_entry(eid)


def test_sealed_read_never_regenerated(store, fool):
    e = answered_tarot(store)
    calls = []
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e, fake(coach_json(COMPARISON), calls=calls))
    assert [s for s, _ in calls] == [COACH_SYS]
    [(_, prompt)] = calls
    assert f"<sealed_read>\n{SEALED}\n</sealed_read>" in prompt
    assert "YOUR SEALED READ (written before they looked; they will see it verbatim above " \
           "your reply; never repeat or rewrite it)" in prompt
    body = prompt.split("ENTRY (what they filled out)\n", 1)[1]
    assert "The Fool" in body and "the little dog at his heels" in body
    assert r.reply == coach.REVEAL_HEAD + "\n\n" + SEALED + "\n\n" + COMPARISON
    assert coach.REVEAL_HEAD == "Here's the read I wrote before you looked:"
    assert store.recent_turns(1)[0].text == r.reply  # the reveal is in the stored turn
    assert store.get_entry(e.id).reply == r.reply
    calls.clear()
    worksheets.seal(store, cfg(), NOW, store.get_entry(e.id),
                    fake(coach_json("x"), calls=calls, read={"read": "A brand new read."}))
    assert store.get_entry(e.id).sealed_read == SEALED and calls == []


def test_crisis_answer_skips_reveal(store, fool):
    e = answered_tarot(store)
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e,
                              fake(coach_json("Are you safe right now?", "crisis")))
    assert coach.REVEAL_HEAD not in r.reply and SEALED not in r.reply
    assert "Are you safe right now?" in r.reply


@pytest.mark.parametrize("bad", ["garbage, not json", {"read": ""}, {"read": "x" * 1201},
                                 {"reading": "wrong key"}, {"read": ["not", "text"]}])
def test_unreadable_sealed_read(store, fool, bad):
    say(store, fake(ASK_TAROT, read=bad), text="pull me a card")
    [e] = store.entries()
    assert e.sealed_read is None and "markup" in box(store)[0]  # the card still goes
    worksheets.accept(store, NOW, payload(e.token, type="tarot", answers=TAROT_ANSWERS))
    e = store.get_entry(e.id)
    p = coach.build_prompt(store, cfg(), NOW, ["[tarot] x"], mode="entry", entry=e)
    assert "didn't manage to write a read" in p and "<sealed_read>" not in p
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e, fake(coach_json(COMPARISON)))
    assert r.reply == COMPARISON


@pytest.mark.parametrize("err", [LimitError("resets 3pm"), NotLoggedIn("x"), MissingCLI("x"),
                                 UnsupportedCLI("x")])
def test_seal_ai_down_still_offers_the_card(store, fool, monkeypatch, err):
    """Like personalize (R8): the AI being down never undoes the coach's stored reply."""
    def boom(*a, **k):
        raise err
    monkeypatch.setattr(worksheets.ai, "ask_json", boom)
    offer = worksheets.prepare(store, cfg(), NOW, "tarot")
    e = store.entry_by_token(offer.token)
    assert e.card == "00-the-fool" and e.sealed_read is None
    assert "card=00-the-fool" in offer.url


def test_no_reveal_during_crisis_episode(store, fool):
    e = answered_tarot(store)
    store.set_meta("crisis_last", (NOW - 2 * H).isoformat())
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e,
                              fake(coach_json(COMPARISON, "struggling")))
    assert coach.REVEAL_HEAD not in r.reply and SEALED not in r.reply
    assert store.get_entry(e.id).sealed_read == SEALED  # kept, just not shown


def test_unparsable_entry_reply_stays_answered_never_try_again(store, fool):
    """Both parses fail and no prose: the canned "try that again?" never becomes an
    entry's final reply. Nothing is stored, so the entry is retried like pending words."""
    e = answered_tarot(store)
    with pytest.raises(coach.EntryUnanswered):
        with store.atomic():
            worksheets.answer(store, cfg(), NOW, e, fake("{not json at all"))
    got = store.get_entry(e.id)
    assert got.status == "answered" and got.reply is None and got.sealed_read == SEALED
    assert store.recent_turns(10) == [] and worksheets.reply_due(store) == [got]


def test_unparsable_entry_reply_with_prose_still_answers(store):
    e = open_entry(store)
    worksheets.accept(store, NOW, payload(e.token))
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, store.get_entry(e.id),
                              fake("That story was doing a lot of work."))
    assert r.reply == "That story was doing a lot of work."
    assert store.get_entry(e.id).status == "done"


def test_give_up_is_honest_and_keeps_the_sealed_read_unshown(store, fool):
    aid = store.add_assignment("worksheet", "Tarot: generic", None)
    e = answered_tarot(store)
    store._x("UPDATE entries SET assignment_id=? WHERE id=?", (aid, e.id))
    e = store.get_entry(e.id)
    with store.atomic():
        r = worksheets.give_up(store, NOW, e)
    assert worksheets.GAVE_UP == (
        "I couldn't put a proper reply together for that one, but your answers are saved "
        "in your mirror. Want to talk it through here?")
    assert r.reply == worksheets.GAVE_UP and r.remove_keyboard is True and r.offer is None
    done = store.get_entry(e.id)
    assert done.status == "done" and done.reply == worksheets.GAVE_UP
    assert done.sealed_read == SEALED  # stays stored
    user, said = store.recent_turns(10)
    assert user.role == "user" and user.text.startswith("[") and done.turn_id == user.id
    assert said.role == "coach" and said.text == worksheets.GAVE_UP
    assert worksheets.reply_due(store) == []
    assert [a.status for a in store.all_assignments()] == ["done"]
    page = mirror.data(store, NOW)
    # fix 4: never in the reply, so the mirror shows it on its own, labelled; answers kept
    assert page["entries"][0]["held_read"] == SEALED
    assert "the little dog at his heels" in json.dumps(page)


def test_empty_deck_falls_back_to_blot(store, monkeypatch):
    monkeypatch.setattr(entries, "available_cards", lambda: [])
    calls = []
    say(store, fake(ASK_TAROT, calls=calls), text="pull me a card")
    [e] = store.entries()
    assert e.type == "blot" and e.seed is not None and e.card is None
    assert [s for s, _ in calls] == [COACH_SYS]
    assert query_of(url_of(box(store)[0]))["t"] == "blot"


def test_expired_tarot_loses_sealed_read(store, fool):
    token = entries.new_token()
    eid = store.add_entry("tarot", token, "ask", card="00-the-fool")
    store.set_sealed_read(eid, SEALED)
    store._x("UPDATE entries SET created=? WHERE id=?",
             ((NOW - timedelta(days=8)).isoformat(), eid))
    assert worksheets.accept(store, NOW, payload(token, type="tarot",
                                                 answers=TAROT_ANSWERS)) == "expired"
    e = store.get_entry(eid)
    assert e.status == "expired" and e.sealed_read is None


def test_tarot_stance():
    for text in (tarot_sys(), COACH_SYS):
        flat = " ".join(text.split())
        assert "prompts for honest thinking, not prediction" in flat
    sec = COACH_SYS.split("# Worksheets", 1)[1].split("\n# ", 1)[0]
    [line] = [ln for ln in sec.splitlines() if ln.startswith("- `tarot`")]
    assert "Fits when" in line


# ---- spreads (D13) -------------------------------------------------------------------

SPREAD_SEALED = ("The Fool behind you is the leap you already took; the Magician where you "
                 "are says the tools are on the table now. Two majors in a row: this is big "
                 "for you, not a passing mood. The High Priestess next asks for quiet before "
                 "the next move, which pulls against the Magician's hurry.")
SPREAD_COMPARISON = "You saw the hurry too. This week, wait one breath before answering Riley."


@pytest.fixture
def deck5(monkeypatch):
    cards = entries.available_cards()[:5]
    assert len(cards) == 5
    monkeypatch.setattr(entries, "available_cards", lambda: cards)
    return cards


def spread_answers(type_, text="it feels like waiting"):
    out = []
    for c in entries.answer_cards(entries.load_sheet(type_)):
        out.append({"mood": 5, "reveal": None, "text": text}[c["kind"]])
    return out


def test_draw_many_distinct(monkeypatch, tmp_path):
    import random
    monkeypatch.setattr(entries, "DECK_DIR", tmp_path)
    man = [{"id": i, "name": i, "number": "", "file": f"{i}.webp", "scene": "", "symbols": []}
           for i in ("a", "b", "c", "d", "e", "card-back")]
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    for e in man:
        (tmp_path / e["file"]).write_bytes(b"RIFF")
    rng = random.Random(3)
    seen = set()
    for _ in range(300):
        ids = [c["id"] for c in worksheets.draw_many(3, rng)]
        assert len(ids) == len(set(ids)) == 3 and "card-back" not in ids
        seen.update(ids)
    assert seen == {"a", "b", "c", "d", "e"}
    assert len(worksheets.draw_many(5)) == 5  # the default rng works too
    assert worksheets.draw_many(6) is None


def test_spread_offer_seals_once_with_positions(store, deck5):
    calls = []
    profile.write("Sam is weighing a move to Denver.")
    store.add_turn("user", "can't stop thinking about Denver")
    ask = coach_json("Laid out five for you.", worksheet={"type": "spread_cross", "focus": None})
    r = say(store, fake(ask, calls=calls), text="do a 5-card read for me")
    assert [s for s, _ in calls if s != COACH_SYS] == [spread_sys()]  # one sealed read
    [(_, prompt)] = [(s, p) for s, p in calls if s == spread_sys()]
    [e] = store.entries()
    ids = e.card.split(".")
    assert e.type == "spread_cross" and len(ids) == 5 == len(set(ids))
    assert set(ids) <= {c["id"] for c in deck5}
    for n, (pos, cid) in enumerate(zip(entries.SPREADS["spread_cross"], ids), 1):
        info = entries.card_info(cid)
        assert f"{n}. {pos}: {info['name']}" in prompt
        assert info["scene"] in prompt
    assert prompt.index(entries.SPREADS["spread_cross"][0]) < prompt.index(
        entries.SPREADS["spread_cross"][-1])
    assert coach.DATA_NOTE in prompt and "Sam is weighing a move to Denver." in prompt
    assert "can't stop thinking about Denver" in prompt
    assert e.sealed_read == SPREAD_SEALED and r.offer.token == e.token
    url = url_of(box(store)[0])
    q = query_of(url)
    assert q["t"] == "spread_cross" and q["card"] == e.card and "seed" not in q
    rest = url.replace(q["id"], "").lower()
    for w in set(re.findall(r"[a-z]{4,}", SPREAD_SEALED.lower())) - set(
            re.findall(r"[a-z]{4,}", e.card)):
        assert w not in rest, w
    # sealed once: a second seal makes no call
    calls.clear()
    worksheets.seal(store, cfg(), NOW, e, fake(coach_json("x"), calls=calls,
                                                spread={"read": "A brand new read."}))
    assert calls == [] and store.get_entry(e.id).sealed_read == SPREAD_SEALED


@pytest.mark.parametrize("bad", ["garbage", {"read": ""}, {"read": "x" * 2001}])
def test_spread_read_limits(store, deck5, bad):
    worksheets.prepare(store, cfg(), NOW, "spread_three", run=fake(coach_json("x"), spread=bad))
    [e] = store.entries()
    assert e.type == "spread_three" and e.sealed_read is None and len(e.card.split(".")) == 3
    store._x("DELETE FROM entries")
    ok = {"read": "y" * 2000}
    worksheets.prepare(store, cfg(), NOW, "spread_three", run=fake(coach_json("x"), spread=ok))
    assert store.entries()[0].sealed_read == "y" * 2000


def test_spread_prompt_gives_counted_facts(store):
    """R20: the read is handed the counts, so the model never has to count suits itself."""
    ids = ["00-the-fool", "cups-03-three", "swords-03-three", "cups-12-knight", "21-the-world"]
    eid = store.add_entry("spread_cross", entries.new_token(), "ask", card=".".join(ids))
    calls = []
    worksheets.seal(store, cfg(), NOW, store.get_entry(eid), fake(coach_json("x"), calls=calls))
    [(_, prompt)] = calls
    facts = prompt.split("<facts>", 1)[1].split("</facts>", 1)[0]
    assert "wands 0, cups 2, swords 1, pentacles 0" in facts
    assert "no cards from: wands, pentacles" in facts
    assert "major arcana: 2 of 5" in facts
    assert "repeated ranks: Three (2 cards)" in facts
    assert "use these counts" in prompt.lower()
    assert "not a suit count" in spread_sys()  # M6


def test_spread_facts_no_repeats():
    ids = ["wands-01-ace", "pentacles-14-king", "02-the-high-priestess"]
    facts = worksheets._spread_facts(ids)
    assert "wands 1, cups 0, swords 0, pentacles 1" in facts
    assert "no cards from: cups, swords" in facts  # live check: "one from each suit"
    assert "major arcana: 1 of 3" in facts and "repeated ranks: none" in facts


@pytest.mark.parametrize("err", [LimitError("resets 3pm"), NotLoggedIn("x")])
def test_spread_seal_ai_down_still_offers(store, deck5, monkeypatch, err):
    def boom(*a, **k):
        raise err
    monkeypatch.setattr(worksheets.ai, "ask_json", boom)
    offer = worksheets.prepare(store, cfg(), NOW, "spread_relationship")
    e = store.entry_by_token(offer.token)
    assert e.sealed_read is None and f"card={e.card}" in offer.url


def answered_spread(store, deck5, type_="spread_three", sealed=SPREAD_SEALED):
    token = entries.new_token()
    n = len(entries.SPREADS[type_])
    eid = store.add_entry(type_, token, "ask", card=".".join(c["id"] for c in deck5[:n]))
    if sealed is not None:
        store.set_sealed_read(eid, sealed)
    assert worksheets.accept(store, NOW, payload(token, type=type_,
                                                 answers=spread_answers(type_))) == "ok"
    return store.get_entry(eid)


def test_spread_reveal_and_crisis(store, deck5):
    e = answered_spread(store, deck5)
    calls = []
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e,
                              fake(coach_json(SPREAD_COMPARISON), calls=calls))
    assert [s for s, _ in calls] == [COACH_SYS]
    [(_, prompt)] = calls
    assert f"<sealed_read>\n{SPREAD_SEALED}\n</sealed_read>" in prompt
    body = prompt.split("ENTRY (what they filled out)\n", 1)[1]
    for pos, c in zip(entries.SPREADS["spread_three"], deck5):
        assert f"{pos}: {c['name']}" in body
    assert "it feels like waiting" in body
    assert r.reply == coach.REVEAL_HEAD + "\n\n" + SPREAD_SEALED + "\n\n" + SPREAD_COMPARISON
    # a crisis reply: no reveal
    e2 = answered_spread(store, deck5, "spread_relationship")
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e2,
                              fake(coach_json("Are you safe right now?", "crisis")))
    assert coach.REVEAL_HEAD not in r.reply and SPREAD_SEALED not in r.reply
    # during an open episode: no reveal, the read is kept
    e3 = answered_spread(store, deck5, "spread_cross")
    with store.atomic():
        r = worksheets.answer(store, cfg(), NOW, e3,
                              fake(coach_json(SPREAD_COMPARISON, "struggling")))
    assert coach.REVEAL_HEAD not in r.reply and SPREAD_SEALED not in r.reply
    assert store.get_entry(e3.id).sealed_read == SPREAD_SEALED


def test_spread_without_sealed_read_says_so(store, deck5):
    e = answered_spread(store, deck5, sealed=None)
    p = coach.build_prompt(store, cfg(), NOW, ["x"], mode="entry", entry=e)
    assert "didn't manage to write a read" in p and "<sealed_read>" not in p


def test_spread_falls_back(store, monkeypatch, deck5):
    two = deck5[:2]
    monkeypatch.setattr(entries, "available_cards", lambda: two)
    calls = []
    ask = coach_json("Here.", worksheet={"type": "spread_three", "focus": None})
    say(store, fake(ask, calls=calls), text="three card spread please")
    [e] = store.entries()
    assert e.type == "tarot" and e.card in {c["id"] for c in two}
    assert [s for s, _ in calls if s != COACH_SYS] == [tarot_sys()]
    store._x("DELETE FROM entries")
    monkeypatch.setattr(entries, "available_cards", lambda: [])
    calls.clear()
    say(store, fake(ask, calls=calls), text="three card spread please")
    [e] = store.entries()
    assert e.type == "blot" and e.card is None and e.seed is not None
    assert [s for s, _ in calls] == [COACH_SYS]


def test_coach_parses_spread_asks():
    for t in entries.SPREADS:
        r = parse_coach(json.dumps(coach_json(worksheet={"type": t, "focus": "Riley"})))
        assert r.worksheet == {"type": t, "focus": "Riley"}
    sec = " ".join(COACH_SYS.split("# Worksheets", 1)[1].split("\n# ", 1)[0].split())
    for phrase, t in (("5-card", "spread_cross"), ("3-card", "spread_three"),
                      ("relationship spread", "spread_relationship"),
                      ("pull a card", "tarot")):
        assert phrase in sec and f"`{t}`" in sec, phrase
    for pos in sum(entries.SPREADS.values(), []):
        assert pos.lower() in sec.lower(), pos


def test_spread_never_assignment(store, deck5):
    for t in entries.SPREADS:
        calls = []
        store.set_meta("outbox", None)
        say(store, fake(coach_json("Do this by Sunday.", new_assignment={
            "kind": "worksheet", "type": t, "focus": FOCUS, "due": TOMORROW}), calls=calls))
        assert store.open_assignments() == [] and store.entries() == []
        assert [s for s, _ in calls] == [COACH_SYS]


def test_spread_no_offer_in_crisis(store, deck5):
    ask = coach_json("Are you safe?", "crisis", worksheet={"type": "spread_cross", "focus": None})
    say(store, fake(ask))
    assert store.entries() == []
    store.set_meta("crisis_last", (NOW - 2 * H).isoformat())
    say(store, fake(coach_json("Here.", worksheet={"type": "spread_three", "focus": None})))
    assert store.entries() == []


def test_spread_stance():
    flat = " ".join(spread_sys().split())
    assert "prompts for honest thinking, not prediction" in flat
    assert "if nothing changes" in flat.lower()
    assert '{"read": "...", "options": {' in flat


STORY_V1 = [3, "Riley went quiet at Sunday dinner", "that I'd said something wrong", 70,
            "she smiled earlier", ["They were stressed"], "she was tired",
            "it's probably not about you", 6]


def test_old_answers_never_sit_under_new_questions(store):
    """An entry stored against check_story v1 (before Task 17): the coach's digest, the
    entry prompt and the compact line all give the words alone, never paired by position."""
    e = open_entry(store)
    store.answer_entry(e.id, STORY_V1, 3, 6)
    e = store.get_entry(e.id)
    line = coach.entry_line(e, "Sat 12:00")
    assert "she smiled earlier" in line and "They were stressed" in line
    assert "the story I told myself" not in line and "another way" not in line
    body = coach._entry(e)
    assert "she smiled earlier" in body and "What did you tell yourself it meant?" not in body
    assert "3 → 6" in body
    flat = entries.compact(entries.load_sheet("check_story"), STORY_V1)
    assert "she smiled earlier" in flat and "what happened:" not in flat


def test_answers_from_an_older_sheet_are_kept_unpaired(store):
    """R26: a page opened before a sheet changed sends the old number of answers. Nothing
    they wrote is lost: the words are kept, bounded, and shown without questions."""
    e = open_entry(store)
    assert worksheets.accept(store, NOW, payload(e.token, answers=STORY_V1 + ["x" * 900])) == "ok"
    got = store.get_entry(e.id)
    assert got.status == "answered" and (got.mood_before, got.mood_after) == (3, None)
    assert "she smiled earlier" in got.answers and "x" * entries.TEXT_MAX in got.answers
    assert not entries.fits(entries.load_sheet("check_story"), got.answers)
    calls = []
    with store.atomic():
        worksheets.answer(store, cfg(), NOW, got, fake(coach_json("Thanks."), calls=calls))
    [(_, prompt)] = [(s, p) for s, p in calls if s == COACH_SYS]
    assert "she smiled earlier" in prompt and "They were stressed" in prompt
    assert store.get_entry(e.id).status == "done"


def test_unpaired_spread_still_names_its_cards(store, deck5):
    ids = ".".join(c["id"] for c in entries.available_cards()[:3])
    eid = store.add_entry("spread_three", entries.new_token(), "ask", card=ids)
    store.answer_entry(eid, [4, None, "the river", 5], 4, 5)
    body = coach._entry(store.get_entry(eid))
    names = [entries.card_info(i)["name"] for i in ids.split(".")]
    for pos, name in zip(entries.SPREADS["spread_three"], names):
        assert f"{pos}: {name}" in body
    assert "the river" in body


# ---- v0.2.1 (D15): the sealed-read call also writes each card's own answer taps -------

FOOL_OPTS = ["The little dog, trying to warn him", "He's about to step off without looking",
             "It's the part of me that wants Denver", entries.NOT_LANDING]


def test_tarot_seal_writes_the_cards_own_taps(store, fool):
    calls = []
    profile.write("Sam is weighing a move to Denver.")
    say(store, fake(ASK_TAROT, calls=calls, read={"read": SEALED, "options": {"notice": FOOL_OPTS}}),
        text="pull me a card")
    [prompt] = [p for s, p in calls if s == tarot_sys()]
    assert "<answer_cards>\nnotice: The Fool\n</answer_cards>" in prompt
    [e] = store.entries()
    assert e.sealed_read == SEALED and e.wording == {"options": {"notice": FOOL_OPTS}}
    url = url_of(box(store)[0])
    assert fragment(url) == {"options": {"notice": FOOL_OPTS}}
    assert worksheets.offer_for(store, e).url == url  # "again" sends the same taps


def test_tarot_seal_taps_join_the_personalized_wording(store, fool):
    ask = coach_json("Drew you one.", worksheet={"type": "tarot", "focus": "the Denver move"})
    wording = {"prompts": {"touches": "Where does it meet the Denver move?"},
               "options": {"touches": ["Leaving Riley behind", "The new job offer",
                                       "Being scared to want it"],
                           "notice": ["personalize", "never writes", "these taps"]}}
    say(store, fake(ask, wording=wording,
                    read={"read": SEALED, "options": {"notice": FOOL_OPTS}}),
        text="pull me a card about Denver")
    [e] = store.entries()
    assert e.wording["prompts"] == wording["prompts"]
    assert e.wording["options"] == {"notice": FOOL_OPTS,
                                    "touches": wording["options"]["touches"]}
    assert fragment(url_of(box(store)[0])) == e.wording


@pytest.mark.parametrize("opts", [None, "junk", {"notice": ["only", "two"]},
                                  {"notice": ["x" * 81, "a b", "c d"]}, {"touches": FOOL_OPTS}])
def test_tarot_seal_bad_taps_keep_the_generic_ones(store, fool, opts):
    read = {"read": SEALED} if opts is None else {"read": SEALED, "options": opts}
    say(store, fake(ASK_TAROT, read=read), text="pull me a card")
    [e] = store.entries()
    assert e.sealed_read == SEALED and e.wording is None
    assert "#tw=" not in url_of(box(store)[0])


def test_spread_seal_writes_taps_for_every_card(store, deck5):
    calls = []
    opts = {f"says_{n}": [f"Card {n} feels like home", f"Card {n} looks unfinished",
                          f"Card {n} is about Riley"] for n in range(1, 6)}
    ask = coach_json("Laid out five for you.", worksheet={"type": "spread_cross", "focus": None})
    say(store, fake(ask, calls=calls, spread={"read": SPREAD_SEALED, "options": opts}),
        text="do a 5-card read")
    [prompt] = [p for s, p in calls if s == spread_sys()]
    [e] = store.entries()
    rows = prompt.split("<answer_cards>\n", 1)[1].split("\n</answer_cards>", 1)[0].splitlines()
    for n, (row, pos, cid) in enumerate(zip(rows, entries.SPREADS["spread_cross"],
                                            e.card.split(".")), 1):
        assert row == f"says_{n}: {pos}: {entries.card_info(cid)['name']}"
    assert len(rows) == 5
    want = {k: v + [entries.NOT_LANDING] for k, v in opts.items()}
    assert e.wording == {"options": want} == fragment(url_of(box(store)[0]))


def test_spread_taps_fit_the_fragment_cap(store, deck5):
    long = {f"says_{n}": [("é" * 78) + str(k) for k in range(3)] for n in range(1, 6)}
    worksheets.prepare(store, cfg(), NOW, "spread_cross",
                       run=fake(coach_json("x"), spread={"read": SPREAD_SEALED, "options": long}))
    [e] = store.entries()
    size = len(json.dumps(e.wording, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= entries.WORDING_BYTES
    kept = list(e.wording["options"])
    assert 0 < len(kept) < 5 and kept == [f"says_{n}" for n in range(1, len(kept) + 1)]


def test_seal_ai_down_keeps_generic_taps(store, fool, monkeypatch):
    def boom(*a, **k):
        raise LimitError("resets 3pm")
    monkeypatch.setattr(worksheets.ai, "ask_json", boom)
    offer = worksheets.prepare(store, cfg(), NOW, "tarot")
    assert store.entry_by_token(offer.token).wording is None and "#tw=" not in offer.url


def test_read_prompts_ask_for_card_taps():
    for text in (tarot_sys(), spread_sys()):
        flat = " ".join(text.split())
        assert '"options"' in flat and "ANSWER CARDS" in flat
        assert f'"{entries.NOT_LANDING}"' in flat
        assert "80 characters" in flat and "never predict" in flat.lower()


# ---- v0.2.1 (D15): a real blot read, compared with their past blots ---------------------

def done_blot(store, saw, when):
    eid = store.add_entry("blot", entries.new_token(), "ask", seed=1000 + len(store.entries()))
    store.answer_entry(eid, [4, None, saw, "", [], "", 5], 4, 5)
    store.finish_entry(eid, "a reply", ["rest"], None)
    store._x("UPDATE entries SET answered_at=? WHERE id=?", (when.isoformat(), eid))
    return eid


def blot_prompt(store, eid):
    e = store.get_entry(eid)
    return coach.build_prompt(store, cfg(), NOW, ["[ink blot] x"], mode="entry", entry=e)


def test_blot_entry_shows_their_past_blots(store):
    for n in range(6):
        done_blot(store, f"saw number {n}", NOW - timedelta(days=10 - n))
    tarot = store.add_entry("tarot", entries.new_token(), "ask", card="00-the-fool")
    store.answer_entry(tarot, [4, None, "a dog", "", [], "", 5], 4, 5)
    store.finish_entry(tarot, "a reply", [], None)
    token = entries.new_token()
    eid = store.add_entry("blot", token, "ask", seed=1234567)
    assert worksheets.accept(store, NOW, payload(token, type="blot", answers=BLOT_ANSWERS)) == "ok"
    p = blot_prompt(store, eid)
    assert "PAST BLOTS (their last 5 before this one, oldest first)" in p
    past = p.split("<past_blots>\n", 1)[1].split("\n</past_blots>", 1)[0].splitlines()
    assert len(past) == 5
    for n, row in zip(range(1, 6), past):
        assert f"saw number {n}" in row and "Ink blot" in row
    assert "saw number 0" not in p.split("<past_blots>", 1)[1]
    assert "a moth with torn wings" not in "\n".join(past)  # this blot is not its own past
    assert p.index("<entry>") < p.index("<past_blots>")


def test_first_blot_says_there_are_none_yet(store):
    token = entries.new_token()
    eid = store.add_entry("blot", token, "ask", seed=1234567)
    worksheets.accept(store, NOW, payload(token, type="blot", answers=BLOT_ANSWERS))
    p = blot_prompt(store, eid)
    assert "<past_blots>\n(none: this is their first blot)\n</past_blots>" in p
    e = open_entry(store)
    worksheets.accept(store, NOW, payload(e.token))
    other = coach.build_prompt(store, cfg(), NOW, ["x"], mode="entry", entry=store.get_entry(e.id))
    assert "PAST BLOTS" not in other  # only a blot gets them


def test_coach_gives_a_real_blot_read():
    sec = " ".join(COACH_SYS.split("# Answering a worksheet", 1)[1].split("\n# ", 1)[0].split())
    [blot] = [s for s in sec.split("- ") if s.startswith("For a blot")]
    for want in ("interpret", "white space", "first", "PAST BLOTS", "push back",
                 "never score", "never diagnose", '"this means you are'):
        assert want in blot, want
    assert "Rorschach" not in blot or "never call it" in blot
