import os
import tempfile
from datetime import datetime, timedelta

import pytest

from tilia import checkins, coach, commands, entries, mirror, paths, patterns, profile
from tilia.config import Config
from tilia.store import Store

from tests.test_coach import fake
from tests.test_commands import FakeTG
from tests.test_mirror import STORY, done

T0 = datetime(2026, 11, 7, 12, 0).astimezone()
DAY = timedelta(days=1)
SYS = (paths.REPO_ROOT / "prompts" / "patterns.md").read_text(encoding="utf-8")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock(T0)


@pytest.fixture
def store(home, clock):
    return Store(paths.db_path(), clock=clock)


def items(*dates, n=3, text="When plans change late, you go quiet before you say why."):
    return {"patterns": [{"text": f"{text} ({i})", "dates": list(dates)} for i in range(n)]}


def day(t):
    return f"{t:%Y-%m-%d}"


def sysp(call):
    cmd = call[0]
    return cmd[cmd.index("--system-prompt") + 1]


def stored(store):
    return store.entries(types=("patterns",))


def test_patterns_weekly_once(store, clock):
    calls = []
    run = fake(items(day(T0)), calls=calls)
    assert patterns.maybe_run(store, Config(), T0, run) is False  # nothing answered yet
    assert calls == []

    done(store, "check_story", STORY)
    assert patterns.maybe_run(store, Config(), T0, run) is True
    assert len(calls) == 1 and sysp(calls[0]) == SYS
    assert patterns.maybe_run(store, Config(), T0, run) is False  # a second, immediate run
    assert len(calls) == 1

    assert patterns.maybe_run(store, Config(), T0 + 8 * DAY, run) is False  # no new entries
    assert len(calls) == 1

    clock.t = T0 + 6 * DAY
    done(store, "check_story", STORY)
    assert patterns.maybe_run(store, Config(), T0 + 6 * DAY, run) is False  # under 7 days
    assert len(calls) == 1

    run2 = fake(items(day(T0 + 6 * DAY)), calls=calls)
    assert patterns.maybe_run(store, Config(), T0 + 7 * DAY + timedelta(hours=1), run2) is True
    assert len(calls) == 2
    [first, second] = stored(store)
    assert second.status == "done" and second.source == "patterns"
    assert store.get_meta("patterns_at") == (T0 + 7 * DAY + timedelta(hours=1)).isoformat()


def _fresh(store):
    store.set_meta("patterns_at", None)


def test_patterns_validated(store, clock):
    done(store, "check_story", STORY)
    clock.t = T0 + DAY
    done(store, "check_story", STORY)
    d0, d1 = day(T0), day(T0 + DAY)

    assert patterns.maybe_run(store, Config(), T0 + DAY, fake(items(d0, n=2))) is False
    assert stored(store) == []  # fewer than 3: nothing

    _fresh(store)
    assert patterns.maybe_run(store, Config(), T0 + DAY, fake(items(d0, d1, n=7))) is True
    [p] = stored(store)
    assert len(p.answers) == 6 and p.answers[0]["dates"] == [d0, d1]

    _fresh(store)
    obj = {"patterns": [
        {"text": "One.", "dates": [d0, "2026-10-01"]},     # the invented date goes
        {"text": "Two.", "dates": ["2026-10-02"]},         # nothing left: the item goes
        {"text": "x" * 201, "dates": [d0]},                # too long: the item goes
        {"text": "x" * 200, "dates": [d1]},
        {"text": "Three.", "dates": [d1, d1]},
        {"text": "Four.", "dates": "2026-11-07"},          # not a list
        {"text": 5, "dates": [d0]},
        {"text": "Five.", "dates": [d0, 20261107]},
    ]}
    assert patterns.maybe_run(store, Config(), T0 + DAY, fake(obj)) is True
    newest = stored(store)[-1].answers
    assert newest == [{"text": "One.", "dates": [d0]}, {"text": "x" * 200, "dates": [d1]},
                      {"text": "Three.", "dates": [d1]}, {"text": "Five.", "dates": [d0]}]

    _fresh(store)
    before = len(stored(store))
    assert patterns.maybe_run(store, Config(), T0 + DAY, fake({"nope": 1})) is False
    assert len(stored(store)) == before
    # a junk answer still counts as this week's pass: no call every 15 minutes
    assert store.get_meta("patterns_at") is not None


def test_patterns_prompt_has_entries_previous_patterns_and_profile(store):
    profile.write("Sam, 34. Goes quiet under stress.")
    a = list(STORY)
    a[1] = "Riley skipped <dinner>"
    done(store, "check_story", a)
    store.add_entry("patterns", entries.new_token(), "patterns", status="done",
                    answers=[{"text": "Last week's line.", "dates": ["2026-11-01"]}])
    calls = []
    patterns.maybe_run(store, Config(), T0, fake(items(day(T0)), calls=calls))
    prompt = calls[0][1]["input"]
    assert coach.DATA_NOTE in prompt
    assert "Goes quiet under stress." in prompt
    assert "Last week's line." in prompt
    assert f"[{day(T0)}] Check the story: " in prompt and "Riley skipped &lt;dinner&gt;" in prompt


def test_patterns_prompt_rules():
    flat = " ".join(SYS.split())
    assert "never a case against" in flat
    assert "with dates" in flat
    assert "about the person only" in flat
    assert "never a diagnosis" in flat
    assert '{"patterns": [{"text":' in flat


# ---- privacy: /forget takes the patterns with it (R10) -------------------------

@pytest.mark.parametrize("scope", ["last", "day"])
def test_forget_deletes_patterns_and_reruns_next_week(store, clock, scope):
    a = list(STORY)
    a[1] = "planning Riley's birthday"
    done(store, "check_story", a)
    clock.t = T0 + DAY
    done(store, "check_story", STORY)
    patterns.maybe_run(store, Config(), T0 + DAY, fake(items(day(T0), text="Birthday worry")))
    mirror.write(store, T0 + DAY)
    assert "Birthday worry" in mirror.path().read_text(encoding="utf-8")

    commands.handle(f"/forget {scope}", store, Config(), T0 + DAY, FakeTG(), 1)
    assert stored(store) == []
    assert "Birthday worry" not in mirror.path().read_text(encoding="utf-8")
    assert store.get_meta("patterns_at") is None

    # the next pass regenerates them from what is left, without waiting for a new entry
    calls = []
    assert patterns.maybe_run(store, Config(), T0 + DAY, fake(items(day(T0)), calls=calls))
    assert len(calls) == 1


# ---- the slow tick ------------------------------------------------------------

def test_slow_runs_patterns_after_checkins_then_writes_mirror(store, monkeypatch):
    from tests.test_loop import FakeClaude, FakeTG as LoopTG, mkloop, Clock as LoopClock
    order = []
    monkeypatch.setattr(checkins, "maybe_send", lambda *a, **k: order.append("checkins"))

    def run_patterns(st, cfg, now, run):
        order.append("patterns")
        return True
    monkeypatch.setattr(patterns, "maybe_run", run_patterns)
    monkeypatch.setattr(mirror, "write", lambda st, now: order.append("mirror"))
    loop = mkloop(store, LoopTG(), LoopClock(T0), FakeClaude())
    loop._slow(T0)
    assert order == ["checkins", "patterns", "mirror"]

    order.clear()
    monkeypatch.setattr(patterns, "maybe_run", lambda *a, **k: order.append("patterns") and False)
    loop._slow(T0)
    assert order == ["checkins", "patterns"]


# ---- mirror.write and /mirror when writing fails (R11) ---------------------------

def test_mirror_write_closes_the_temp_file_when_fchmod_fails(store, monkeypatch):
    made = []
    real = tempfile.mkstemp

    def mkstemp(*a, **k):
        fd, p = real(*a, **k)
        made.append((fd, p))
        return fd, p

    def boom(fd, mode):
        raise OSError("no chmod")
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(os, "fchmod", boom)
    with pytest.raises(OSError):
        mirror.write(store, T0)
    [(fd, p)] = made
    with pytest.raises(OSError):
        os.fstat(fd)  # closed
    assert not os.path.exists(p)


def test_mirror_command_replies_even_when_writing_fails(store, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(mirror, "render", boom)
    tg = FakeTG()
    assert commands.handle("/mirror", store, Config(), T0, tg, 1)
    assert tg.sent == [mirror.WRITE_FAILED]
    assert "couldn't" in mirror.WRITE_FAILED


def test_mirror_command_failure_is_logged_without_content(store, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("Riley went quiet at Sunday dinner")
    monkeypatch.setattr(mirror, "render", boom)
    commands.handle("/mirror", store, Config(), T0, FakeTG(), 1)
    err = capsys.readouterr().err
    assert "mirror failed: OSError" in err and len(err.strip().splitlines()) == 1
    assert "Riley" not in err
