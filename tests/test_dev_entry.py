"""The dev-only helper (scripts/, outside the audited package): throwaway folder only, its
own Codex login (never ~/.codex), and the card-after-crisis check makes no AI call."""
import json
import sys
from types import SimpleNamespace

import pytest

from tilia import paths, profile
from tilia.store import Store

sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
import dev_entry  # noqa: E402


def no_ai(cmd, **kw):
    raise AssertionError(f"no AI call expected: {cmd[0]}")


@pytest.fixture
def real(tmp_path, monkeypatch):
    """A stand-in for the real data folder, so a broken guard can never write to the real one."""
    r = tmp_path / "real Tilia"
    monkeypatch.setattr(paths, "_DEFAULT", str(r))
    return r


def test_refuses_without_a_throwaway_home(monkeypatch, real):
    monkeypatch.delenv("TILIA_HOME")
    with pytest.raises(SystemExit):
        dev_entry.offer("blot", None, "claude", run=no_ai)
    for inside in (real, real / "sub" / "dir"):
        monkeypatch.setenv("TILIA_HOME", str(inside))
        with pytest.raises(SystemExit):
            dev_entry.offer("blot", None, "claude", run=no_ai)
    assert not real.exists()


def test_offer_blot_seeds_the_made_up_profile_and_prints_the_url(home, capsys):
    assert dev_entry.offer("blot", None, "claude", run=no_ai) == 0
    out = capsys.readouterr().out
    assert "[entry] type=blot" in out and "[url] https://mydude-industries.github.io/tilia/v" in out
    assert "Riley" in profile.read() and "Sam" in profile.read()


def test_chatgpt_needs_its_own_dev_login(home, tmp_path, monkeypatch, capsys):
    dev_home = tmp_path / "dev" / "codex-home"
    monkeypatch.setattr(dev_entry, "DEV_CODEX_HOME", dev_home)
    monkeypatch.setattr(dev_entry.shutil, "which", lambda name: "/usr/bin/true")
    monkeypatch.setattr(paths, "codex_home", paths.codex_home)  # restored after the test
    with pytest.raises(SystemExit) as e:
        dev_entry.offer("blot", None, "chatgpt", run=no_ai)
    assert e.value.code != 0
    assert "CODEX_HOME=~/.tilia-dev/codex-home codex login" in capsys.readouterr().out
    assert dev_home.is_dir() and (dev_home.stat().st_mode & 0o777) == 0o700
    (dev_home / "auth.json").write_text("{}")
    assert dev_entry.offer("blot", None, "chatgpt", run=no_ai) == 0
    assert paths.codex_home() == dev_home  # Tilia's codex runs with the dev login


def test_nothing_reads_the_users_own_codex_folder():
    for d in ("tilia", "scripts"):
        for f in (paths.REPO_ROOT / d).iterdir():
            if f.suffix in (".py", ".sh") or f.name in ("pre-commit", "dev-setup"):
                text = f.read_text(encoding="utf-8")
                assert '".codex"' not in text and "'.codex'" not in text, f
                assert "~/.codex/" not in text and "$HOME/.codex" not in text, f


def test_card_after_crisis_sends_nothing_and_calls_no_ai(home, capsys):
    assert dev_entry.card_after_crisis("claude", run=no_ai) == 0
    out = capsys.readouterr().out
    assert "[candidates before the crisis mark] ['card']" in out  # the gate is what stops it
    assert "[candidates after] []" in out
    assert "[card sent] False" in out and "[ai calls] 0" in out
    assert Store(paths.db_path()).entries() == []


def test_answer_runs_the_coach_on_the_entry(home, capsys):
    dev_entry.offer("blot", None, "claude", run=no_ai)
    token = capsys.readouterr().out.split("token=")[1].split()[0]
    reply = {"reply": "A bird taking off. Notice when that lift shows up this week.",
             "state": "ok", "themes": ["hope"]}

    def run(cmd, **kw):
        return SimpleNamespace(stdout=json.dumps({"result": json.dumps(reply), "is_error": False}),
                               stderr="", returncode=0)
    answers = json.dumps([5, None, "a bird", "two faces", ["Calm"], "light", 6])
    assert dev_entry.answer(token, answers, "claude", run=run) == 0
    out = capsys.readouterr().out
    assert "[accept] ok" in out and "['hope']" in out and "A bird taking off" in out
