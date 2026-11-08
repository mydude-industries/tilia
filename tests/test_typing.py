"""The typing… indicator: shown while Tilia writes, never in the way of a reply."""
import threading
import time

import pytest

from tilia.telegram import LIMIT, Telegram
from tilia.typing_indicator import Typing, pause_for

from tests.test_telegram import TOKEN, FakePost


class TG:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def typing(self, chat_id):
        self.calls.append(chat_id)
        if self.fail:
            raise RuntimeError("boom")
        return True


def fast_wait(waits):
    """Stands in for Event.wait(interval): records the interval, barely waits."""
    def wait(ev, seconds):
        waits.append(seconds)
        return ev.wait(0.001)
    return wait


def until(cond, limit=2.0):
    end = time.monotonic() + limit
    while not cond() and time.monotonic() < end:
        time.sleep(0.001)
    return cond()


def typing_threads():
    return [t for t in threading.enumerate() if t.name == "tilia-typing" and t.is_alive()]


def test_typing_is_sent_immediately_on_entry():
    tg = TG()
    blocked = lambda ev, s: ev.wait(10)  # noqa: E731  the thread never ticks on its own
    with Typing(tg, 7, wait=blocked):
        assert tg.calls == [7]
    assert typing_threads() == []


def test_typing_repeats_every_interval_until_done_then_stops():
    tg, waits = TG(), []
    with Typing(tg, 7, interval=4, wait=fast_wait(waits)) as t:
        assert until(lambda: len(tg.calls) >= 3)
    n = len(tg.calls)
    assert set(waits) == {4}
    assert not t.thread.is_alive() and typing_threads() == []
    time.sleep(0.01)
    assert len(tg.calls) == n  # nothing after exit


def test_typing_stops_on_exception():
    tg, waits = TG(), []
    with pytest.raises(ValueError):
        with Typing(tg, 7, wait=fast_wait(waits)) as t:
            assert until(lambda: len(tg.calls) >= 2)
            raise ValueError("coach blew up")
    assert not t.thread.is_alive() and typing_threads() == []


def test_typing_failures_are_swallowed_and_logged_by_type(capsys):
    tg = TG(fail=True)
    with Typing(tg, 7, wait=fast_wait([])):
        assert until(lambda: len(tg.calls) >= 2)
    assert typing_threads() == []
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "boom" not in err


def test_tg_without_typing_is_fine():
    with Typing(object(), 7, wait=fast_wait([])):
        pass
    assert typing_threads() == []


def test_pause_between_parts_is_scaled_and_capped():
    assert pause_for("") == pytest.approx(1.5)
    assert 1.5 < pause_for("x" * 1000) < 2.5
    assert pause_for("x" * LIMIT) == pytest.approx(2.5)
    assert pause_for("x" * 10 * LIMIT) == 3.0


def test_telegram_typing_calls_send_chat_action():
    post = FakePost({"ok": True})
    assert Telegram(TOKEN, post=post).typing(7) is True
    url, params, _ = post.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    assert params == {"chat_id": 7, "action": "typing"}
    assert Telegram(TOKEN, post=FakePost({"ok": False})).typing(7) is False


def test_telegram_typing_never_raises_or_leaks_the_token(capsys):
    t = Telegram(TOKEN, post=FakePost(exc=OSError(f"bot{TOKEN} unreachable")))
    assert t.typing(7) is False
    err = capsys.readouterr().err
    assert "typing" in err and "OSError" in err and TOKEN not in err


def test_first_after_skips_the_entry_ping_and_waits_out_the_rest():
    """Typing already shown 1 s ago: no burst on entry, the next one ~3 s later."""
    tg, waits = TG(), []
    with Typing(tg, 7, interval=4, wait=fast_wait(waits), first_after=3) as t:
        assert tg.calls == []
        assert until(lambda: len(tg.calls) >= 2)
    assert waits[0] == 3 and set(waits[1:]) <= {4}
    assert not t.thread.is_alive()
