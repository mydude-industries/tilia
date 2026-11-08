"""Step 4 "Connect your AI" (ChatGPT through Codex, or Claude), step 5c (the import), and
the Mac checks that time out. Scripted io; no real process. Fake people only."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tilia import config, paths, profile
from tilia.claude import LimitError, NotLoggedIn, UnsupportedCLI
from tilia.setup_wizard import (CHATGPT_ACCOUNT, DATA_CONTROLS, IMPORT_AFTER, IMPORT_PROMPT,
                                REDO, REDO_HELP, STEPS, run)
from tests.test_setup import (FakeIO, GOOD, _finished, _flat, _screens_for, done_steps, state,  # noqa: F401
                              write_state)

AT_4 = ["check_mac", "make_bot", "pair_phone"]
AT_5C = ["check_mac", "make_bot", "pair_phone", "claude_login", "texts", "style"]
def CODEX():
    return str(paths.codex_bin())


def codex_runs(io):
    return [c for c in io.runs if Path(c[0]).name == "codex"]


def claude_runs(io):
    return [c for c in io.runs if c[:1] == ["claude"] and c[1:2] != ["auth"]]


# ---- which AI, and the default ----------------------------------------------------------

def test_default_is_chatgpt_when_only_tilias_codex_home_is_logged_in(state):
    write_state(state, AT_4)
    (paths.codex_home() / "auth.json").write_text("{}")  # a login Tilia made earlier
    io = FakeIO(["", ""], codex_installed=True, codex_logged_in=True, claude_logged_in=False)
    run(state, io)  # walks away at texts
    assert "Enter = 1" in io.prompts[0]
    cfg = config.load()
    assert (cfg.provider, cfg.chatgpt_model, cfg.model) == ("chatgpt", "gpt-6-luna", "sonnet")
    assert [c.provider for c in io.ai_cfgs] == ["chatgpt"]
    assert io.ai_cfgs[0].chatgpt_model == "gpt-6-luna"
    t = _flat(io.text())
    assert _flat(" ".join(CHATGPT_ACCOUNT)) in t and _flat(" ".join(DATA_CONTROLS)) in t
    assert "Tilia will use ChatGPT (gpt-6-luna)" in t
    assert claude_runs(io) == []
    assert "claude_login" in done_steps(state)


def test_default_is_claude_when_only_claude_is_logged_in(state):
    write_state(state, AT_4)
    io = FakeIO(["", ""])  # codex not installed; claude logged in
    run(state, io)
    assert "Enter = 2" in io.prompts[0]
    assert config.load().provider == "claude"
    assert [c.provider for c in io.ai_cfgs] == ["claude"]
    assert "Tilia will use Claude (sonnet)" in io.text()


def test_detection_never_runs_codex_before_it_is_verified(state):
    write_state(state, AT_4)
    (paths.codex_home() / "auth.json").write_text("{}")
    io = FakeIO(["", ""], codex_installed=True, codex_logged_in=True, claude_logged_in=False)
    run(state, io)
    names = [Path(c[0]).name for c in io.runs]
    assert names.index("codesign") < names.index("codex")


@pytest.mark.parametrize("kw", [dict(claude_logged_in=True, codex_installed=True),
                                dict(claude_logged_in=False)])
def test_no_default_when_both_or_neither_are_logged_in(state, kw):
    write_state(state, AT_4)
    if kw.get("codex_installed"):
        (paths.codex_home() / "auth.json").write_text("{}")
    io = FakeIO(["", "2", ""], **kw)
    run(state, io)
    assert "Enter =" not in io.prompts[0]
    assert "Please type 1 or 2." in io.text()
    assert config.load().provider == "claude"


# ---- the ChatGPT path: install, verify, log in, check, choose -----------------------------

def test_codex_is_verified_before_it_is_ever_run(state):
    write_state(state, AT_4)
    io = FakeIO(["1", ""], codex_installed=True, codex_logged_in=True)
    run(state, io)
    names = [Path(c[0]).name for c in io.runs]
    before = names[:names.index("codex")]
    assert before.count("codesign") == 2 and "spctl" in before
    assert ["codesign", "--verify", "--strict", CODEX()] in io.runs
    assert ["codesign", "-dvv", CODEX()] in io.runs
    assert ["spctl", "--assess", "--type", "open", "--context", "context:primary-signature",
            "-v", CODEX()] in io.runs
    assert "Codex 0.167.0 is installed, signed by OpenAI and notarized by Apple" in io.text()
    assert "Logged in to ChatGPT as maya@example.com" in io.text()
    assert "Codex saved nothing (no session file)" in io.text()
    assert config.load().provider == "chatgpt"


@pytest.mark.parametrize("kw,why", [
    (dict(codex_signed=False), "fails Apple's signature check"),
    (dict(codex_signer_ok=False), "isn't signed by OpenAI"),
    (dict(codex_notarized=False), "isn't notarized by Apple"),
])
def test_a_bad_signature_means_one_sentence_no_run_and_a_choice(state, kw, why):
    write_state(state, AT_4)
    io = FakeIO(["1", "c", ""], codex_installed=True, codex_logged_in=True, **kw)
    run(state, io)
    assert f"The downloaded Codex program {why}, so Tilia won't run it." in io.text()
    assert "[r] try ChatGPT again later   [c] use Claude instead" in io.text()
    assert codex_runs(io) == []
    assert "Using Claude instead." in io.text()
    assert config.load().provider == "claude"
    assert [c.provider for c in io.ai_cfgs] == ["claude"]


def test_missing_codex_is_downloaded_from_the_pinned_release_then_verified(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "", ""], codex_installed=False, codex_logged_in=True)
    run(state, io)
    assert io.installs == 1
    t = io.text()
    assert "the one version it has checked (0.167.0) from OpenAI's release page" in t
    assert "recorded checksum" in t and "Downloading (about 100 MB)" in t
    names = [Path(c[0]).name for c in io.runs]
    assert "brew" not in names and "npm" not in names and "curl" not in names
    assert names.index("codesign") < names.index("codex")  # verified after the download
    assert config.load().provider == "chatgpt"


@pytest.mark.parametrize("problem", ["couldn't be downloaded",
                                     "didn't match the checksum Tilia has on record for it",
                                     "isn't published for this Mac's processor"])
def test_a_failed_download_offers_later_or_claude_never_forces(state, problem):
    write_state(state, AT_4)
    io = FakeIO(["1", "", "c", ""], codex_install_problem=problem)
    run(state, io)
    assert f"The Codex download {problem}, so Tilia hasn't installed it" in io.text()
    assert config.load().provider == "claude" and codex_runs(io) == []
    write_state(state, AT_4)
    io = FakeIO(["1", "", "r"], codex_install_problem=problem)
    assert run(state, io) == 1
    assert "Run ./setup again when you want to try ChatGPT again" in io.text()
    assert "claude_login" not in done_steps(state)
    assert io.ai_cfgs == []  # nothing was forced on her


def test_typing_claude_at_the_download_prompt_switches(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "claude", ""])
    run(state, io)
    assert io.installs == 0
    assert config.load().provider == "claude"


def test_a_saved_session_after_the_check_is_refused_loudly(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "c", ""], codex_installed=True, codex_logged_in=True,
                codex_sessions=["/x/codex-home/sessions/2026/11/07/rollout.jsonl"])
    run(state, io)
    t = io.text()
    assert "Codex saved a session transcript (1 file(s) under codex-home/sessions)" in t
    assert "Tilia won't use ChatGPT until that is understood" in t
    assert config.load().provider == "claude"
    assert "Codex saved nothing" not in t


def test_no_email_falls_back_to_the_plain_line(state):
    write_state(state, AT_4)
    io = FakeIO(["1", ""], codex_installed=True, codex_logged_in=True, codex_email=None)
    run(state, io)
    assert "You're logged in to ChatGPT" in io.text() and "Logged in to ChatGPT as" not in io.text()


def test_login_runs_codex_login_in_tilias_private_home(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "", ""], codex_installed=True, codex_logged_in=False)
    run(state, io)
    assert [CODEX(), "login"] in io.runs
    home = paths.data_dir() / "codex-home"
    assert io.envs and all(e["CODEX_HOME"] == str(home) for e in io.envs)
    assert not any(k.startswith(("OPENAI_", "CODEX_")) for e in io.envs for k in e
                   if k != "CODEX_HOME")
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert "Logged in to ChatGPT as maya@example.com" in io.text()
    assert config.load().provider == "chatgpt"


def test_typing_claude_at_the_login_prompt_switches(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "claude", ""], codex_installed=True, codex_logged_in=False)
    run(state, io)
    assert [CODEX(), "login"] not in io.runs
    assert config.load().provider == "claude"


def test_limit_during_the_check_is_fine(state):
    write_state(state, AT_4)
    io = FakeIO(["1", ""], codex_installed=True, codex_logged_in=True,
                claude_errors=[LimitError("You've hit your usage limit")])
    run(state, io)
    assert "at your usage limit right now, which is fine" in io.text()
    assert config.load().provider == "chatgpt"


def test_not_logged_in_during_the_check_logs_in_again(state):
    write_state(state, AT_4)
    io = FakeIO(["1", ""], codex_installed=True, codex_logged_in=True,
                claude_errors=[NotLoggedIn("sign in again")])
    run(state, io)
    assert io.runs.count([CODEX(), "login"]) == 1
    assert len(io.ai_cfgs) == 2 and config.load().provider == "chatgpt"


def test_no_answer_during_the_check_offers_later_or_claude(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "r"], codex_installed=True, codex_logged_in=True,
                claude_errors=[RuntimeError("codex failed")])
    assert run(state, io) == 1
    assert "ChatGPT didn't answer; check your internet connection." in io.text()
    assert "claude_login" not in done_steps(state)
    io = FakeIO(["1", "c", ""], codex_installed=True, codex_logged_in=True,
                claude_errors=[RuntimeError("codex failed")])
    write_state(state, AT_4)
    run(state, io)
    assert config.load().provider == "claude"


def test_a_newer_unchecked_codex_means_one_sentence_and_claude(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "c", ""], codex_installed=True, codex_logged_in=True,
                claude_errors=[UnsupportedCLI("codex 9.0.0 is not a version Tilia has checked")])
    run(state, io)
    assert "a version Tilia hasn't checked (it supports 0.167.0)" in io.text()
    assert "./update" in io.text()
    assert config.load().provider == "claude"


def test_stronger_chatgpt_model(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "2"], codex_installed=True, codex_logged_in=True)
    run(state, io)
    assert config.load().chatgpt_model == "gpt-6.1-sol"
    t = io.text()
    assert "lighter (gpt-6-luna), fits the $20 plan (recommended)" in t
    assert "stronger (gpt-6.1-sol)" in t


def test_setup_ai_rerun_keeps_the_current_choice_on_enter(state):
    _finished(state)
    cfg = config.load()
    cfg.provider, cfg.chatgpt_model = "chatgpt", "gpt-6.1-sol"
    config.save(cfg)
    io = FakeIO(["", ""], installed=True, codex_installed=True, codex_logged_in=True)
    assert run(state, io, redo="ai") == 0
    assert set(_screens_for(io)) == {"Step 4 of 6"}
    assert "Enter = 1" in io.prompts[0] and "Enter = 2" in io.prompts[1]
    cfg = config.load()
    assert (cfg.provider, cfg.chatgpt_model, cfg.model) == ("chatgpt", "gpt-6.1-sol", "opus")
    assert io.runs.count(["scripts/install.sh"]) == 1


def test_setup_ai_switches_provider_and_keeps_the_other_model(state):
    _finished(state)  # claude / opus
    io = FakeIO(["1", ""], installed=True, codex_installed=True, codex_logged_in=True)
    assert run(state, io, redo="ai") == 0
    cfg = config.load()
    assert (cfg.provider, cfg.chatgpt_model, cfg.model) == ("chatgpt", "gpt-6-luna", "opus")
    io = FakeIO(["2", ""], installed=True, codex_installed=True, codex_logged_in=True)
    assert run(state, io, redo="model") == 0  # the old name still works
    assert config.load().provider == "claude" and config.load().model == "opus"


def test_redo_help_and_keys():
    assert REDO["ai"] == REDO["model"] == ("claude_login",)
    assert REDO["import"] == ("bring_ai",)
    text = "\n".join(REDO_HELP)
    assert "./setup ai" in text and "./setup import" in text and "./setup model" not in text
    assert STEPS.index("bring_ai") == STEPS.index("style") + 1


# ---- step 1 no longer touches Claude; checks that time out inform, never block -----------

def test_check_mac_does_not_touch_claude(state):
    io = FakeIO([])
    run(state, io)
    assert claude_runs(io) == [] and "Checking two things: Python and FileVault." in io.text()
    assert "check_mac" in done_steps(state)


def test_mac_checks_that_time_out_inform_and_never_block(state):
    io = FakeIO([], slow=True)
    run(state, io)
    assert "Couldn't check FileVault" in io.text()
    assert "FileVault is on" not in io.text()
    assert "administrators" not in io.text()
    assert "check_mac" in done_steps(state)


# ---- step 5c: bring what your AI already knows ------------------------------------------

def _at_5c(state, folder, provider="claude"):
    write_state(state, AT_5C)
    cfg = config.load()
    cfg.provider = provider
    config.save(cfg)
    folder.mkdir(mode=0o700)


def test_import_skip_leaves_everything_alone(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    write_state(state, AT_5C)
    io = FakeIO(["skip"], import_folder=folder)
    run(state, io)  # walks away at keep_running
    assert "bring_ai" in done_steps(state)
    assert io.clipboard == [] and io.opened_folders == [] and not folder.exists()
    assert "Skipped: Tilia starts from what you tell it" in io.text()
    assert "Step 5c of 6" in _screens_for(io)


def test_import_shows_the_prompt_copies_it_and_names_the_ai(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder, provider="chatgpt")
    io = FakeIO(["", "skip"], import_folder=folder)
    run(state, io)
    assert io.clipboard == [IMPORT_PROMPT]
    assert io.opened_folders == [folder]
    assert _flat(IMPORT_PROMPT) in _flat(io.text())
    assert "Paste it into ChatGPT" in io.text()
    assert "tilia-profile.md" in io.text() and "leave it in Downloads" in io.text()
    rows = sum(max(1, -(-len(l) // 80)) for l in io.text().split("Press Enter to copy")[0].split("Step 5c")[-1].split("\n"))
    assert rows <= 24
    assert "Copied to your clipboard" in io.text()


def test_import_file_becomes_the_profile_and_is_wiped(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    body = "# About me\n" + "\n".join(f"line {i}" for i in range(12)) + "\n"
    (folder / "me.txt").write_text(body)
    io = FakeIO(["", "", "y"], import_folder=folder)
    run(state, io)
    t = io.text()
    assert "me.txt (" in t and "| line 6" in t and "| line 7" not in t  # 8 lines shown
    assert "Use this as your starting profile?" in t
    assert profile.read() == body
    assert not (folder / "me.txt").exists() and not folder.exists()
    assert "overwritten and deleted, and the folder removed" in t
    assert "bring_ai" in done_steps(state)


def test_import_merges_under_a_heading_when_notes_exist(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    profile.write("# Sam\nlikes walks\n")
    (folder / "ai.md").write_text("Sam is kind to others and hard on herself.\n")
    io = FakeIO(["", "", "y"], import_folder=folder)
    run(state, io)
    assert profile.read() == ("# Sam\nlikes walks\n\n## What my AI already knew\n\n"
                              "Sam is kind to others and hard on herself.\n")
    assert "Added to Tilia's notes on you" in io.text()


def test_import_needs_exactly_one_file(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    (folder / "a.txt").write_text("a")
    (folder / "b.md").write_text("b")
    (folder / "photo.png").write_bytes(b"x")  # not a candidate
    io = FakeIO(["", "", "skip"], import_folder=folder)
    run(state, io)
    assert "exactly one .txt, .md or .rtf file" in io.text() and "there are 2" in io.text()
    assert sorted(p.name for p in folder.iterdir()) == ["a.txt", "b.md", "photo.png"]
    assert profile.read() == ""


def test_import_with_no_file_yet_asks_again(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    io = FakeIO(["", "", "skip"], import_folder=folder)
    run(state, io)
    assert "there is none" in io.text()


def test_import_rtf_goes_through_textutil(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    (folder / "me.rtf").write_bytes(b"{\\rtf1 hello}")
    io = FakeIO(["", "", "y"], import_folder=folder, rtf="From RTF\nline two")
    run(state, io)
    assert io.rtf_calls == [folder / "me.rtf"]
    assert profile.read() == "From RTF\nline two"


def test_import_refuses_over_100kb(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    (folder / "big.txt").write_text("x" * (100 * 1024 + 1))
    io = FakeIO(["", "", "skip"], import_folder=folder)
    run(state, io)
    assert "over 100 KB" in io.text()
    assert (folder / "big.txt").exists() and profile.read() == ""


def test_import_declined_leaves_the_file_and_says_where(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    (folder / "me.txt").write_text("hello")
    io = FakeIO(["", "", "n"], import_folder=folder)
    run(state, io)
    assert "Left alone:" in io.text() and "me.txt" in io.text()
    assert (folder / "me.txt").read_text() == "hello" and profile.read() == ""
    assert "bring_ai" in done_steps(state)
    assert IMPORT_AFTER in io.text()
    assert "clipboard" in IMPORT_AFTER and "delete that chat" in IMPORT_AFTER


def test_import_rtf_that_converts_to_over_100kb_is_refused_cleanly(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    (folder / "me.rtf").write_bytes(b"{\\rtf1 small}")
    io = FakeIO(["", "", "y", "skip"], import_folder=folder, rtf="x" * (100 * 1024 + 1))
    run(state, io)
    assert "over 100 KB, so trim it" in io.text()
    assert (folder / "me.rtf").exists() and profile.read() == ""


def test_prompt_asks_for_a_downloadable_tilia_profile_file():
    assert IMPORT_PROMPT.endswith(
        " Give it to me as a downloadable Markdown file named tilia-profile.md.")


def _dl(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


def test_downloads_fallback_imports_on_yes_and_deletes(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile.md").write_text("From downloads\n")
    (dl / "other.md").write_text("never touched")
    io = FakeIO(["", "", "y"], import_folder=folder, downloads=dl)
    run(state, io)
    assert profile.read() == "From downloads\n"
    assert not (dl / "tilia-profile.md").exists() and (dl / "other.md").exists()
    assert "bring_ai" in done_steps(state)


def test_downloads_fallback_declined_leaves_file_and_says_where(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile (1).md").write_text("hello")
    io = FakeIO(["", "", "n"], import_folder=folder, downloads=dl)
    run(state, io)
    assert (dl / "tilia-profile (1).md").read_text() == "hello" and profile.read() == ""
    assert "Left alone: ~/Downloads/tilia-profile (1).md" in io.text()


def test_import_folder_wins_over_downloads(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (folder / "mine.md").write_text("from folder")
    (dl / "tilia-profile.md").write_text("from downloads")
    io = FakeIO(["", "", "y"], import_folder=folder, downloads=dl)
    run(state, io)
    assert profile.read() == "from folder" and (dl / "tilia-profile.md").exists()


def test_several_downloads_candidates_ask_to_keep_one(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile.md").write_text("a")
    (dl / "tilia-profile (1).md").write_text("b")
    io = FakeIO(["", "", "skip"], import_folder=folder, downloads=dl)
    run(state, io)
    assert "keep just the one you want" in io.text() and profile.read() == ""
    assert len(list(dl.iterdir())) == 2


def test_wipe_overwrites_the_bytes_before_deleting(tmp_path, monkeypatch):
    import tilia.setup_wizard as sw
    f = tmp_path / "secret.txt"
    f.write_text("very personal")
    seen = {}
    real_unlink = Path.unlink

    def spy(self, missing_ok=False):
        seen["bytes"] = self.read_bytes()
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", spy)
    sw._wipe(f)
    assert seen["bytes"] == b"\0" * len("very personal") and not f.exists()


def test_setup_import_reruns_only_that_step(state, tmp_path):
    _finished(state)
    io = FakeIO(["skip"], installed=True, import_folder=tmp_path / "Tilia Import")
    assert run(state, io, redo="import") == 0
    assert set(_screens_for(io)) == {"Step 5c of 6"}
    assert io.runs.count(["scripts/install.sh"]) == 1


def test_import_dir_is_a_private_folder_in_home_not_documents(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    d = paths.import_dir()
    assert d == tmp_path / "Tilia Import"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert "Documents" not in d.parts and "Desktop" not in d.parts


def test_import_text_size_cap_and_merge_rules(home):
    with pytest.raises(ValueError):
        profile.import_text("x" * (100 * 1024 + 1))
    profile.import_text("first\n")
    assert profile.read() == "first\n"
    profile.import_text("  second  ", merge=True)
    assert profile.read() == "first\n\n## What my AI already knew\n\nsecond\n"
    profile.import_text("replace\n")  # no merge: verbatim replacement
    assert profile.read() == "replace\n"
