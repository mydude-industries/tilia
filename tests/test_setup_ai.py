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
    assert io.removed_codex == 1  # fix 1: the unverified binary is gone


@pytest.mark.parametrize("slow", [("codesign",), ("spctl",)])
def test_a_signature_check_that_times_out_keeps_the_binary_and_asks_to_rerun(state, slow):
    """A slow Mac's codesign or spctl timing out is not a failed check: the binary stays,
    Codex is never run unverified, and she is asked to run the check again."""
    write_state(state, AT_4)
    io = FakeIO(["1", "r"], codex_installed=True, codex_logged_in=True, codex_check_slow=slow)
    run(state, io)
    t = _flat(io.text())
    assert "Apple's check of the Codex program timed out, so Tilia hasn't run it." in t
    assert "Run ./setup again to check it again" in t
    assert "won't run it" not in t
    assert io.removed_codex == 0 and io.codex_installed()
    assert codex_runs(io) == []
    assert "claude_login" not in done_steps(state)
    io2 = FakeIO(["1", "", ""], codex_installed=True, codex_logged_in=True)
    run(state, io2)  # the re-run checks the same binary again, and it passes
    assert io2.installs == 0 and config.load().provider == "chatgpt"


def test_a_rerun_after_a_bad_signature_downloads_fresh(state):
    write_state(state, AT_4)
    io = FakeIO(["1", "r"], codex_installed=True, codex_logged_in=True, codex_signed=False)
    run(state, io)
    assert io.removed_codex == 1 and io.installs == 0 and not io.codex_installed()
    io2 = FakeIO(["1", "", ""], codex_installed=io.codex_installed(), codex_logged_in=True)
    run(state, io2)
    assert io2.installs == 1 and config.load().provider == "chatgpt"


def test_real_io_remove_codex_uses_codex_install(monkeypatch):
    import tilia.setup_wizard as sw
    seen = []
    monkeypatch.setattr(sw.codex_install, "remove", lambda: seen.append(1))
    sw.RealIO().remove_codex()
    assert seen == [1]


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
    assert io.rtf_calls == [b"{\\rtf1 hello}"]  # the bytes read and shown, not the path
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
    st = os.lstat(f)
    assert sw._wipe(f, (st.st_dev, st.st_ino)) is True
    assert seen["bytes"] == b"\0" * len("very personal") and not f.exists()


# ---- the import only reads plain files she made, and shows them safely ------------------

def test_import_folder_symlink_is_rejected(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    secret = tmp_path / "elsewhere.txt"
    secret.write_text("not for Tilia")
    (folder / "me.txt").symlink_to(secret)
    io = FakeIO(["", "", "skip"], import_folder=folder)
    run(state, io)
    assert "me.txt isn't a plain file, so it's ignored" in io.text()
    assert "not for Tilia" not in io.text() and profile.read() == ""
    assert secret.read_text() == "not for Tilia"


def test_downloads_symlink_is_rejected(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    secret = tmp_path / "elsewhere.md"
    secret.write_text("not for Tilia")
    (dl / "tilia-profile.md").symlink_to(secret)
    io = FakeIO(["", "", "skip"], import_folder=folder, downloads=dl)
    run(state, io)
    assert "tilia-profile.md isn't a plain file, so it's ignored" in io.text()
    assert "not for Tilia" not in io.text() and secret.exists()


def test_fifo_and_other_non_regular_files_are_rejected(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    os.mkfifo(folder / "me.txt")  # opening it to read would hang setup
    (folder / "notes.md").mkdir()
    io = FakeIO(["", "", "skip"], import_folder=folder)
    run(state, io)
    t = io.text()
    assert "me.txt isn't a plain file" in t and "notes.md isn't a plain file" in t
    assert "there is none" in t and profile.read() == ""


def test_read_refuses_a_file_swapped_for_a_fifo_after_listing(tmp_path):
    import tilia.setup_wizard as sw
    p = tmp_path / "me.txt"
    os.mkfifo(p)
    with pytest.raises(sw.NotPlainFile):
        sw._read_plain(p)  # O_NONBLOCK + fstat: never blocks, never reads


def test_read_refuses_a_file_owned_by_someone_else(tmp_path, monkeypatch):
    import tilia.setup_wizard as sw
    p = tmp_path / "me.txt"
    p.write_text("hi")
    monkeypatch.setattr(sw.os, "getuid", lambda: os.stat(p).st_uid + 1)
    with pytest.raises(sw.NotPlainFile):
        sw._read_plain(p)


def test_file_swapped_between_preview_and_wipe_is_left_alone(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    f = folder / "me.txt"
    f.write_text("what she saw\n")

    def swap(prompt):
        if "Use this as your starting profile?" in prompt:
            f.unlink()
            f.write_text("something else entirely\n")  # same name, new file
    io = FakeIO(["", "", "y"], import_folder=folder, on_ask=swap)
    run(state, io)
    assert profile.read() == "what she saw\n"  # what was previewed, read through the fd
    assert f.read_text() == "something else entirely\n"  # never zeroed or deleted
    assert "changed after you saw it, so I left it alone" in io.text()
    assert "overwritten and deleted" not in io.text()


def test_wipe_refuses_when_identity_differs(tmp_path):
    import tilia.setup_wizard as sw
    f = tmp_path / "me.txt"
    f.write_text("keep me")
    st = os.lstat(f)
    assert sw._wipe(f, (st.st_dev, st.st_ino + 1)) is False
    assert f.read_text() == "keep me"


def test_wipe_refuses_a_symlink_put_in_place(tmp_path):
    import tilia.setup_wizard as sw
    f = tmp_path / "me.txt"
    f.write_text("x")
    st = os.lstat(f)
    target = tmp_path / "target.txt"
    target.write_text("precious")
    f.unlink()
    f.symlink_to(target)
    assert sw._wipe(f, (st.st_dev, st.st_ino)) is False
    assert target.read_text() == "precious" and f.is_symlink()


def test_stale_downloads_file_is_ignored_with_a_note(state, tmp_path):
    import time
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    old = dl / "tilia-profile.md"
    old.write_text("from last month")
    copied = time.time()
    os.utime(old, (copied - 3600, copied - 3600))
    io = FakeIO(["", "", "skip"], import_folder=folder, downloads=dl, wall=copied)
    run(state, io)
    t = io.text()
    assert "Ignored ~/Downloads/tilia-profile.md: it's older than the question you just copied" in t
    assert "from last month" not in t and old.exists() and profile.read() == ""


def test_copy_time_is_kept_in_setup_state_so_resume_uses_it(state, tmp_path):
    import json
    import time
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    copied = time.time() - 600
    state.write_text(json.dumps({"done": AT_5C, "import_copied_at": copied}))
    f = dl / "tilia-profile.md"
    f.write_text("made before the interruption\n")
    os.utime(f, (copied + 60, copied + 60))
    io = FakeIO(["", "", "y"], import_folder=folder, downloads=dl, wall=time.time())
    run(state, io)
    assert profile.read() == "made before the interruption\n"
    saved = json.loads(state.read_text())
    assert "bring_ai" in saved["done"] and "import_copied_at" not in saved  # cleared at the end


def test_copy_time_is_recorded_when_the_question_is_copied(state, tmp_path):
    import json
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    seen = {}

    def peek(prompt):
        if "Press Enter when the file is saved" in prompt:
            seen.update(json.loads(state.read_text()))
    io = FakeIO(["", "skip"], import_folder=folder, wall=1234.5, on_ask=peek)
    run(state, io)
    assert seen["import_copied_at"] == 1234.5 and seen["done"] == AT_5C


def test_provenance_from_chatgpt_is_shown_without_a_warning(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile.md").write_text("hi\n")
    io = FakeIO(["", "", "y"], import_folder=folder, downloads=dl, where_froms={
        "tilia-profile.md": ["https://files.oaiusercontent.com/x", "https://chatgpt.com/c/1"]})
    run(state, io)
    assert "Downloaded from: files.oaiusercontent.com via chatgpt.com" in io.text()
    assert "didn't come from ChatGPT or Claude" not in io.text()
    assert profile.read() == "hi\n"


def test_trusted_referrer_does_not_vouch_for_an_untrusted_download(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile.md").write_text("hi\n")
    io = FakeIO(["", "", "n"], import_folder=folder, downloads=dl, where_froms={
        "tilia-profile.md": ["https://evil.example/tilia-profile.md", "https://chatgpt.com/c/1"]})
    run(state, io)
    t = io.text()
    assert "Downloaded from: evil.example via chatgpt.com" in t
    assert "This file didn't come from ChatGPT or Claude" in t


def test_provenance_from_claude_blob_url_is_trusted(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile.md").write_text("hi\n")
    io = FakeIO(["", "", "y"], import_folder=folder, downloads=dl, where_froms={
        "tilia-profile.md": ["blob:https://claude.ai/0b6e", "https://claude.ai/chat/1"]})
    run(state, io)
    assert "Downloaded from: claude.ai" in io.text() and " via " not in io.text()
    assert "didn't come from ChatGPT or Claude" not in io.text()


def test_provenance_from_elsewhere_warns_but_still_lets_her_choose(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    dl = _dl(tmp_path)
    (dl / "tilia-profile.md").write_text("hi\n")
    io = FakeIO(["", "", "y"], import_folder=folder, downloads=dl, where_froms={
        "tilia-profile.md": ["https://evil.example/tilia-profile.md"]})
    run(state, io)
    t = io.text()
    assert "Downloaded from: evil.example" in t
    assert "This file didn't come from ChatGPT or Claude — only use it if you made it." in t
    assert t.index("didn't come from") < t.index("Use this as your starting profile?")
    assert profile.read() == "hi\n"


def test_origin_is_not_fooled_by_userinfo_or_lookalike_hosts():
    import plistlib
    from tilia.setup_wizard import _origin

    def o(*urls):
        return _origin(plistlib.dumps(list(urls), fmt=plistlib.FMT_BINARY))
    assert o("https://chatgpt.com@evil.example/x") == ("evil.example", None, False)
    assert o("https://evil.example\\@chatgpt.com/x") == ("evil.example", None, False)
    assert o("https://chatgpt.com.evil.example/x") == ("chatgpt.com.evil.example", None, False)
    assert o("https://CLAUDE.AI:443/x") == ("claude.ai", None, True)
    assert o("blob:https://claude.ai/1", "https://claude.ai/chat") == ("claude.ai", None, True)
    assert o("blob:https://evil.example/1", "https://claude.ai/chat") == (
        "evil.example", "claude.ai", False)
    assert o("https://files.oaiusercontent.com/f", "https://chatgpt.com/c") == (
        "files.oaiusercontent.com", "chatgpt.com", True)
    assert o("https://evil.example/f", "https://chatgpt.com/c")[2] is False
    assert _origin(None) == (None, None, False)
    assert _origin(b"not a plist") == (None, None, False)


def test_no_provenance_shows_no_origin_line(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    (folder / "me.txt").write_text("hi\n")
    io = FakeIO(["", "", "y"], import_folder=folder)
    run(state, io)
    assert "Downloaded from" not in io.text() and "didn't come from" not in io.text()


def test_preview_strips_control_and_escape_characters(state, tmp_path):
    folder = tmp_path / "Tilia Import"
    _at_5c(state, folder)
    body = ("ok\x1b[2J\x1b]0;pwned\x07 line\r\n\ttabbed\x00\x9b31m\n"
            "rtl‮evil\n" + "".join(f"l{i}\n" for i in range(10)))
    (folder / "me.txt").write_text(body)
    io = FakeIO(["", "", "y"], import_folder=folder)
    run(state, io)
    t = io.text()
    assert not any(c in t for c in "\x1b\x07\x00\x9b\r‮")
    assert "| ok[2J]0;pwned line" in t and "| \ttabbed31m" in t
    assert "13 lines" in t and "the first 8" in t
    assert "rtl\u202eevil" in profile.read()  # only the preview is cleaned, not her notes


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
