"""Setup walkthrough, driven by a scripted io. No real Keychain, network, launchd,
System Settings or `claude`. Fake people only."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tilia import config, paths
from tilia.claude import NotLoggedIn
from tilia.setup_wizard import CODEX_SIGNER, FIRST_MESSAGE, STEPS, audit_prompt, run
from tilia.store import Store
from tilia.telegram import Update
from tilia.ui import FOOTER

GOOD = "123456789:AAH" + "x" * 32
BAD = "not-a-key"
CLEAR = "\x0c<clear>"
REPO = Path(__file__).resolve().parent.parent


class FakeTG:
    def __init__(self, io, token):
        self.io, self.token = io, token

    def get_me(self):
        return {"username": "fake_tilia_bot"} if self.token == GOOD else None

    def updates(self, offset):
        self.io.offsets.append(offset)
        return self.io.tg_updates.pop(0) if self.io.tg_updates else []

    def send(self, chat_id, text):
        self.io.sent.append((chat_id, text))
        return True


class FakeIO:
    def __init__(self, inputs, **kw):
        self.inputs = list(inputs)
        self.out: list[str] = []
        self.prompts: list[str] = []
        self.runs: list[list[str]] = []
        self.opened: list[str] = []
        self.sent: list[tuple] = []
        self.offsets: list = []
        self.saved_offset = None
        self.tokens: list[str] = []
        self.t = 0.0
        self.fdesetup = kw.get("fdesetup", "FileVault is On.\n")
        self.claude_installed = kw.get("claude_installed", True)
        self.tg_updates = kw.get("tg_updates", [])
        self.claude_errors = list(kw.get("claude_errors", []))
        self.readable = list(kw.get("readable", [True]))
        self.recent = kw.get("recent", 50)
        self.chats = kw.get("chats", {})
        self.install_rc = 0
        self.color = False
        self.secret_prompts: list[str] = []
        self.probes: list = []
        self.probe_none = kw.get("probe_none", 0)  # how many probes time out first
        self.first_turns: list[str] = []
        self.installed = kw.get("installed", False)
        self.admins = kw.get("admins", "GroupMembership: root fakeuser\n")
        self.turns = kw.get("turns", 0)
        self.slow = kw.get("slow", False)  # the Mac checks time out
        # step 4, Connect your AI
        self._codex_installed = kw.get("codex_installed", False)
        self.codex_logged_in = kw.get("codex_logged_in", False)
        self.claude_logged_in = kw.get("claude_logged_in", True)
        self.codex_signed = kw.get("codex_signed", True)        # codesign --verify passes
        self.codex_signer_ok = kw.get("codex_signer_ok", True)  # the Authority is OpenAI's
        self.codex_notarized = kw.get("codex_notarized", True)  # spctl says accepted
        self.codex_check_slow = kw.get("codex_check_slow", ())  # "codesign"/"spctl" time out
        self.codex_install_problem = kw.get("codex_install_problem")  # None = download works
        self.removed_codex = 0
        self._codex_email = kw.get("codex_email", "maya@example.com")
        self._codex_sessions = list(kw.get("codex_sessions", []))
        self.installs = 0
        self.ai_cfgs: list = []
        self.envs: list = []  # the env of every codex run
        # step 5c, the import
        self.import_folder = kw.get("import_folder")
        self.downloads = kw.get("downloads")
        self.clipboard: list[str] = []
        self.opened_folders: list = []
        self.rtf = kw.get("rtf", "From RTF\nline two")
        self.rtf_calls: list = []
        self.wall = kw.get("wall", 0.0)  # wall-clock seconds when the question is copied
        self.where_froms = kw.get("where_froms", {})  # file name -> [url, ...]
        self.on_ask = kw.get("on_ask")  # (prompt) -> None, runs before an answer is given

    # terminal
    def input(self, prompt=""):
        self.prompts.append(prompt)
        self.out.append(prompt)
        if self.on_ask:
            self.on_ask(prompt)
        if not self.inputs:
            raise EOFError
        return self.inputs.pop(0)

    def secret_input(self, prompt=""):
        self.secret_prompts.append(prompt)
        self.out.append(prompt)
        if not self.inputs:
            raise EOFError
        return self.inputs.pop(0)

    def print(self, s=""):
        self.out.append(s)

    def clear(self):
        self.out.append(CLEAR)

    # system
    def run(self, cmd, **kw):
        self.runs.append(cmd)
        if self.slow and cmd[:1] in (["fdesetup"], ["dscl"]):
            raise subprocess.TimeoutExpired(cmd, 15)
        if cmd[:2] == ["fdesetup", "status"]:
            return SimpleNamespace(returncode=0, stdout=self.fdesetup, stderr="")
        if cmd[:3] == ["claude", "auth", "status"]:
            return SimpleNamespace(returncode=0 if self.claude_logged_in else 1, stdout="{}",
                                   stderr="")
        name = Path(cmd[0]).name
        if name == "codex" and cmd[0] != str(paths.codex_bin()):
            raise AssertionError(f"setup must only run Tilia's own codex: {cmd}")
        if name == "codex" and cmd[1:3] == ["login", "status"]:
            self.envs.append(kw.get("env"))
            ok = self.codex_logged_in
            return SimpleNamespace(returncode=0 if ok else 1, stdout="",
                                   stderr="Logged in using ChatGPT" if ok else "Not logged in")
        if name == "codex" and cmd[1:2] == ["login"]:
            self.envs.append(kw.get("env"))
            self.codex_logged_in = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if name == "codex":
            raise AssertionError(f"setup must not run codex itself: {cmd}")
        if cmd[0] in self.codex_check_slow:
            raise subprocess.TimeoutExpired(cmd, 60)
        if cmd[:3] == ["codesign", "--verify", "--strict"]:
            return SimpleNamespace(returncode=0 if self.codex_signed else 1, stdout="", stderr="")
        if cmd[:2] == ["codesign", "-dvv"]:
            who = CODEX_SIGNER if self.codex_signer_ok else \
                "Developer ID Application: Someone Else (ZZZ999ZZZ9)"
            return SimpleNamespace(returncode=0, stdout="",
                                   stderr=f"Identifier=codex\nAuthority={who}\n"
                                          "Authority=Apple Root CA\n")
        if cmd[:1] == ["spctl"]:
            ok = self.codex_notarized
            return SimpleNamespace(returncode=0 if ok else 3, stdout="",
                                   stderr=f"{cmd[-1]}: {'accepted' if ok else 'rejected'}\n")
        if name in ("curl", "brew", "npm"):
            raise AssertionError(f"setup must not run an installer itself: {cmd}")
        if cmd[:2] == ["claude", "--version"]:
            if not self.claude_installed:
                raise FileNotFoundError("claude")
            return SimpleNamespace(returncode=0, stdout="2.1.0 (Claude Code)", stderr="")
        if cmd[:3] == ["dscl", ".", "-read"]:
            if self.admins is None:
                raise OSError("dscl")
            return SimpleNamespace(returncode=0, stdout=self.admins, stderr="")
        if "claude.ai/install.sh" in " ".join(cmd):
            self.claude_installed = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def open_url(self, url):
        self.opened.append(url)
        return True

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    # services
    def telegram(self, token):
        return FakeTG(self, token)

    def set_token(self, token):
        self.tokens.append(token)

    def get_token(self):
        if not self.tokens:
            raise RuntimeError("missing")
        return self.tokens[-1]

    def ai_check(self, cfg):
        self.ai_cfgs.append(cfg)
        if self.claude_errors:
            raise self.claude_errors.pop(0)
        return "OK"

    def codex_installed(self):
        return self._codex_installed

    def install_codex(self):
        self.installs += 1
        if self.codex_install_problem:
            return self.codex_install_problem
        self._codex_installed = True
        return None

    def remove_codex(self):
        self.removed_codex += 1
        self._codex_installed = False

    def codex_email(self):
        return self._codex_email

    def codex_sessions(self):
        return list(self._codex_sessions)

    def import_dir(self):
        folder = self.import_folder or Path(os.environ["TILIA_HOME"]).parent / "Tilia Import"
        folder.mkdir(mode=0o700, exist_ok=True)
        return folder

    def downloads_dir(self):
        return self.downloads or Path(os.environ["TILIA_HOME"]).parent / "Downloads"

    def copy_clipboard(self, text):
        self.clipboard.append(text)
        return True

    def open_folder(self, folder):
        self.opened_folders.append(folder)
        return True

    def rtf_to_text(self, data):
        self.rtf_calls.append(data)
        return self.rtf

    def wall_time(self):
        return self.wall

    def where_from(self, path):
        import plistlib
        urls = self.where_froms.get(Path(path).name)
        return None if urls is None else plistlib.dumps(urls, fmt=plistlib.FMT_BINARY)

    def probe(self, name=None):
        """The launchd probe: can Tilia's Python read Messages, and which chats match."""
        self.probes.append(name)
        if self.probe_none:
            self.probe_none -= 1
            return None
        ok = self.readable.pop(0) if len(self.readable) > 1 else self.readable[0]
        if not ok:
            return {"can_read": False, "recent": 0, "matches": []}
        matches = self.chats.get(name.strip().lower(), []) if name else []
        return {"can_read": True, "recent": self.recent, "matches": matches}

    def python_path(self):
        return "/Users/fake/.local/share/uv/python/cpython-3.12/bin/python3.12"

    def install(self):
        self.runs.append(["scripts/install.sh"])
        return self.install_rc

    def save_offset(self, n):
        self.saved_offset = n

    def save_first_message(self, text):
        self.first_turns.append(text)

    def agent_installed(self):
        return self.installed

    def stop_agent(self):
        self.runs.append(["stop-agent"])

    def username(self):
        return "fakeuser"

    def turn_count(self):
        return self.turns

    # helpers
    def text(self):
        return "\n".join(self.out)

    def screens(self):
        blocks, cur = [], None
        for s in self.out:
            if s == CLEAR:
                cur = []
                blocks.append(cur)
            elif cur is not None:
                cur.append(s)
        return ["\n".join(b) for b in blocks]


def write_state(path, done):
    path.write_text(json.dumps({"done": done}))


def done_steps(path):
    return json.loads(path.read_text())["done"]


@pytest.fixture
def state(home):
    paths.data_dir()
    return home / "setup_state.json"


def U(update_id, chat_id, text, kind="text", chat_type="private"):
    return Update(update_id, chat_id, text, kind, chat_type=chat_type,
                  sender_name="Alex", sender_username="alex_fake")


# ---- the seven from the brief -------------------------------------------------------

def test_resume_at_first_unfinished_step(state):
    write_state(state, ["check_mac", "make_bot", "pair_phone"])
    io = FakeIO([])  # walks away at the first prompt
    rc = run(state, io)
    assert rc == 0
    screens = io.screens()
    assert "Step 4 of 6" in screens[0]
    assert not any("Step 1 of 6" in s or "Step 3 of 6" in s for s in screens)
    assert done_steps(state) == ["check_mac", "make_bot", "pair_phone"]
    assert "Run ./setup later" in io.text()


def test_help_shows_step_help(state):
    write_state(state, ["check_mac"])
    io = FakeIO(["help"])
    run(state, io)
    t = io.text()
    # The help printed is step 2's, not a generic one.
    from tilia.setup_wizard import HELP
    assert HELP["make_bot"].splitlines()[0] in t
    assert HELP["check_mac"].splitlines()[0] not in t
    # And the same prompt is asked again afterwards.
    assert io.prompts[0] == io.prompts[1]


def test_pairing_accepts_only_exact_code(state, monkeypatch):
    write_state(state, ["check_mac", "make_bot"])
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    updates = [
        [U(10, 999, "hello")],
        [U(11, 999, "418 2070"), U(12, 999, "4182 07")],
        [U(13, 999, None, "unsupported")],
        [U(14, -100, "418 207", chat_type="group")],  # right code, but not a private chat
        [U(15, 4242, "418 207")],
    ]
    io = FakeIO(["y"], tg_updates=updates)
    io.tokens = [GOOD]
    run(state, io)
    assert "418 207" in io.text()
    assert "Alex (@alex_fake)" in io.text()  # she sees who sent it before it's saved
    assert config.load().chat_id == 4242
    assert io.saved_offset == 16
    assert "pair_phone" in done_steps(state)

    # Without the space works too.
    write_state(state, ["check_mac", "make_bot"])
    io = FakeIO(["y"], tg_updates=[[U(20, 77, "418207")]])
    io.tokens = [GOOD]
    run(state, io)
    assert config.load().chat_id == 77


def test_pairing_times_out_after_ten_minutes(state, monkeypatch):
    write_state(state, ["check_mac", "make_bot"])
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    io = FakeIO([], tg_updates=[])
    io.tokens = [GOOD]
    run(state, io)
    assert 600 <= io.t <= 610
    assert config.load().chat_id is None
    assert "pair_phone" not in done_steps(state)


def test_bad_token_one_sentence_error_and_retry(state):
    write_state(state, ["check_mac"])
    io = FakeIO(["n", "", BAD, GOOD])
    run(state, io)
    step2 = io.text().split("Step 3 of 6")[0].splitlines()
    errs = [s.strip() for s in step2 if s.strip().startswith("✗")]
    assert len(errs) == 1
    sentence = errs[0][1:].strip()
    assert sentence.endswith(".") and sentence.count(". ") == 0
    assert io.tokens == [GOOD]  # only the good key ever reaches Keychain
    assert BAD not in io.text() and GOOD not in io.text()  # the key is never echoed
    # Both pastes went through the hidden reader, never the echoing one.
    assert len(io.secret_prompts) == 2 and not any("key" in p for p in io.prompts)
    assert "Got a key" in io.text()
    assert "make_bot" in done_steps(state)
    assert "https://t.me/BotFather" in io.opened


def test_texts_step_skippable(state):
    write_state(state, ["check_mac", "make_bot", "pair_phone", "claude_login"])
    io = FakeIO(["n"])
    run(state, io)
    assert "texts" in done_steps(state)
    assert config.load().threads == []
    assert not any("Privacy_AllFiles" in u for u in io.opened)


def test_style_goal_stored_verbatim(state):
    write_state(state, ["check_mac", "make_bot", "pair_phone", "claude_login", "texts"])
    goal = '  Fight less with Alex — and "repair" faster.\tReally. '
    io = FakeIO([goal, "2", "1"])
    run(state, io)
    cfg = config.load()
    assert cfg.goal == goal
    assert cfg.tone == "direct_soft"
    assert cfg.referrals == "never"
    assert "style" in done_steps(state)


def _full_run_io():
    inputs = [
        # 1 check_mac: FileVault off -> Enter, Enter
        "", "",
        # 2 make_bot: n (no bot yet), Enter opens BotFather, paste key (hidden)
        "n", "", GOOD,
        # 3 pair_phone: polls, then "is this you?"
        "y",
        # 4 connect_ai: Enter = Claude (the one logged in); claude missing -> Enter to
        # install; not logged in -> Enter after login; model choice
        "", "", "", "1",
        # 5 texts: yes; Enter opens settings; Enter when on; probe says no -> Enter to
        # check again; name; confirm; done; backfill
        "y", "", "", "", "alex", "y", "", "1",
        # 5b style
        "Be kinder to myself.", "3", "2",
        # 5c bring_ai: skipped
        "skip",
        # 6 keep_running: battery yes
        "y",
    ]
    return FakeIO(
        inputs, fdesetup="FileVault is Off.\n", claude_installed=False,
        tg_updates=[[U(5, 4242, "418207")]],
        claude_errors=[NotLoggedIn("please /login")], readable=[False, True],
        chats={"alex": [{"name": "Alex Doe", "chat_id": 31, "handle": "+15550101234"}]},
    )


def test_every_screen_has_footer_and_counter(state, monkeypatch):
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    io = _full_run_io()
    assert run(state, io) == 0
    screens = io.screens()
    labels = []
    for s in screens[:-1]:  # the last is the "all done" screen
        flat = " ".join(s.split())
        assert FOOTER in flat, s
        import re
        m = re.search(r"Step (\d)([bc]?) of (\d)", s)
        assert m, s
        assert m.group(3) == "6" and 1 <= int(m.group(1)) <= 6
        labels.append(int(m.group(1)) + {"": 0, "b": 0.5, "c": 0.7}[m.group(2)])
        assert "min" in s  # time estimate
        rows = sum(max(1, -(-len(line) // 80)) for line in s.split("\n"))
        assert rows <= 24, f"{rows} rows:\n{s}"
    assert labels == sorted(labels)  # never jumps backward
    assert {int(x) for x in labels} == {1, 2, 3, 4, 5, 6}
    assert io.text().count("✓") >= 7
    # Effects of a complete run.
    assert done_steps(state) == list(STEPS)
    cfg = config.load()
    assert cfg.chat_id == 4242 and cfg.model == "sonnet" and cfg.battery_awake is True
    assert cfg.provider == "claude"
    assert cfg.threads == [{"name": "Alex Doe", "chat_id": 31}] and cfg.backfill_days == 365
    assert cfg.tone == "gentle" and cfg.referrals == "gently"
    assert io.sent == [(4242, FIRST_MESSAGE)]
    assert io.first_turns == [FIRST_MESSAGE]  # her first message is part of the conversation
    assert ["scripts/install.sh"] in io.runs
    assert any("FileVault" in u for u in io.opened)
    assert any("Privacy_AllFiles" in u for u in io.opened)
    assert any("claude.ai/install.sh" in " ".join(c) for c in io.runs)


# ---- extra coverage ----------------------------------------------------------------

def test_rerun_after_finish_does_not_resend(state):
    write_state(state, list(STEPS))
    io = FakeIO([])
    assert run(state, io) == 0
    assert io.sent == [] and io.runs == []


def test_audit_prompt_from_privacy_doc_or_fallback():
    builtin = audit_prompt("")
    assert builtin.startswith("Read this entire project.")
    assert builtin.endswith("every place data is written.")
    doc = ("# Privacy\n\nAsk your Claude:\n\n> *\"Read this entire project. Tell me plainly: "
           "custom words. Point to every place data is written.\"*\n\nMore.\n")
    got = audit_prompt(doc)
    assert got == ("Read this entire project. Tell me plainly: custom words. "
                   "Point to every place data is written.")
    marked = "x\n<!-- audit-prompt -->\nAudit this, please.\n<!-- /audit-prompt -->\ny"
    assert audit_prompt(marked) == "Audit this, please."


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


@pytest.mark.real_subprocess  # bash + git in a temp repo, uv/install faked
def test_update_with_no_tags_says_so_and_exits_0(tmp_path):
    repo = tmp_path / "my tilia copy"
    repo.mkdir()
    shutil.copy(REPO / "update", repo / "update")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    r = subprocess.run(["bash", str(repo / "update")], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "no released versions yet" in r.stdout.lower()


@pytest.mark.real_subprocess  # bash + git in a temp repo, uv/install faked
def test_setup_bootstrap_handles_spaces_and_installs_hook(tmp_path):
    repo = tmp_path / "Ann's folder" / "tilia app"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "setup", repo / "setup")
    shutil.copy(REPO / "scripts" / "pre-commit", repo / "scripts" / "pre-commit")
    _git(repo, "init", "-q")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "uv.log"
    (fakebin / "uv").write_text(f'#!/bin/bash\necho "$PWD|$*" >> "{log}"\n')
    (fakebin / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{fakebin}:/usr/bin:/bin"}
    r = subprocess.run(["bash", str(repo / "setup")], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert calls[0].startswith(f"{repo}|sync")
    assert calls[-1].endswith("python -m tilia.setup_wizard")
    assert (repo / ".git" / "hooks" / "pre-commit").exists()
    r = subprocess.run(["bash", str(repo / "setup"), "texts"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    assert log.read_text().splitlines()[-1].endswith("python -m tilia.setup_wizard texts")


def _tagged_repo(tmp_path, real_audit=False, new_install_fails=False):
    repo = tmp_path / "tilia copy"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "update", repo / "update")
    inst = repo / "scripts" / "install.sh"
    inst.write_text('#!/bin/bash\necho "fake install"\n')
    inst.chmod(0o755)
    (repo / "PRIVACY.md").write_text("> Read this entire project. INSTALLED. "
                                     "Point to every place data is written.\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    _git(repo, "tag", "v0.1.0")
    (repo / "PRIVACY.md").write_text("> Read this entire project. INCOMING. "
                                     "Point to every place data is written.\n")
    if new_install_fails:
        inst.write_text('#!/bin/bash\necho "broken install"; exit 3\n')
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "two")
    _git(repo, "tag", "v0.2.0")
    _git(repo, "checkout", "-q", "v0.1.0")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    if real_audit:  # run the real extractor with this repo's python
        (fakebin / "uv").write_text(
            f'#!/bin/bash\n[ "$1" = run ] && cd "{REPO}" && exec "{REPO}/.venv/bin/python" '
            '-m tilia.setup_wizard audit-prompt\nexit 0\n')
    else:
        (fakebin / "uv").write_text(
            '#!/bin/bash\ncase "$*" in *restart-agent*) echo "fake restart"; exit 0;; esac\n'
            '[ "$1" = run ] && cat\nexit 0\n')
    (fakebin / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return repo, env


def _head_tag(repo):
    return subprocess.run(["git", "describe", "--tags", "--exact-match"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.real_subprocess  # bash + git in a temp repo, uv/install faked
@pytest.mark.parametrize("answer,expect", [("yes\n", "v0.2.0"), ("y\n", "v0.1.0")])
def test_update_needs_typed_yes(tmp_path, answer, expect):
    repo, env = _tagged_repo(tmp_path)
    r = subprocess.run(["bash", str(repo / "update")], input=answer, capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "v0.1.0" in r.stdout and "v0.2.0" in r.stdout
    assert "PRIVACY.md" in r.stdout  # the diff --stat
    assert "Read this entire project" in r.stdout  # the audit prompt
    assert _head_tag(repo) == expect
    assert ("fake install" in r.stdout) == (expect == "v0.2.0")
    if expect == "v0.2.0":  # install, then an explicit restart on the new code, then say so
        out = r.stdout
        assert out.index("fake install") < out.index("fake restart")
        assert out.rstrip().splitlines()[-1].strip().endswith("Tilia restarted on v0.2.0.")
    else:
        assert "fake restart" not in r.stdout


def test_missing_token_sends_her_back_to_step_2(state):
    write_state(state, ["check_mac", "make_bot"])
    io = FakeIO([])  # no token in the fake Keychain
    assert run(state, io) == 1
    assert done_steps(state) == ["check_mac"]
    assert "missing from Keychain" in io.text()


# ---- fix round 1 -------------------------------------------------------------------

def test_real_io_reads_the_key_with_getpass(monkeypatch):
    import tilia.setup_wizard as sw
    seen = {}
    monkeypatch.setattr(sw.getpass, "getpass", lambda prompt="": seen.setdefault("p", prompt) and GOOD)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("input() used for the key"))
    assert sw.RealIO().secret_input("Paste the key here: ") == GOOD
    assert seen["p"] == "Paste the key here: "


def test_help_works_at_the_hidden_key_prompt(state):
    write_state(state, ["check_mac"])
    from tilia.setup_wizard import HELP
    io = FakeIO(["n", "", "help", GOOD])
    run(state, io)
    assert HELP["make_bot"].splitlines()[0] in io.text()
    assert io.tokens == [GOOD]


def test_pairing_wrong_codes_end_the_window(state, monkeypatch):
    write_state(state, ["check_mac", "make_bot"])
    import tilia.setup_wizard as sw
    codes = iter([418207, 555123])
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: next(codes))
    wrong = [[U(i, 999, f"{100000 + i}")] for i in range(1, 6)]
    # The old code arriving after the window closed must not pair.
    updates = wrong + [[U(6, 999, "418207")], [U(7, 4242, "555 123")]]
    io = FakeIO(["y"], tg_updates=updates)
    io.tokens = [GOOD]
    run(state, io)
    t = io.text()
    assert t.count("wrong codes") == 1
    assert "418 207" in t and "555 123" in t
    assert config.load().chat_id == 4242


def test_pairing_no_means_fresh_code(state, monkeypatch):
    write_state(state, ["check_mac", "make_bot"])
    import tilia.setup_wizard as sw
    codes = iter([418207, 555123])
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: next(codes))
    io = FakeIO(["n", "y"], tg_updates=[[U(1, 999, "418207")], [U(2, 4242, "555123")]])
    io.tokens = [GOOD]
    run(state, io)
    assert config.load().chat_id == 4242
    assert io.saved_offset == 3


def test_texts_always_shows_python_path_and_checks_from_launchd(state):
    write_state(state, ["check_mac", "make_bot", "pair_phone", "claude_login"])
    io = FakeIO(["y", "", "", "alex", "y", "", "2"], readable=[True],
                chats={"alex": [{"name": "Alex Doe", "chat_id": 31, "handle": "+15550101234"}]})
    run(state, io)
    t = io.text()
    assert io.python_path() in t  # shown even though access already works
    assert any("Privacy_AllFiles" in u for u in io.opened)
    assert io.probes == [None, "alex"]  # access check and the name search both via the probe
    assert "Terminal" not in t
    cfg = config.load()
    assert cfg.threads == [{"name": "Alex Doe", "chat_id": 31}]
    assert cfg.backfill_days == sw_all_history()


def sw_all_history():
    from tilia.setup_wizard import ALL_HISTORY_DAYS
    return ALL_HISTORY_DAYS


def test_texts_probe_no_access_shows_path_again_and_skip_works(state):
    write_state(state, ["check_mac", "make_bot", "pair_phone", "claude_login"])
    io = FakeIO(["y", "", "", "skip"], readable=[False])
    run(state, io)
    t = io.text()
    assert t.count(io.python_path()) == 2
    assert "Terminal" not in t
    assert config.load().threads == []
    assert "texts" in done_steps(state)


def test_texts_probe_timeout_retries(state):
    write_state(state, ["check_mac", "make_bot", "pair_phone", "claude_login"])
    io = FakeIO(["y", "", "", "", "", "n"], probe_none=1, readable=[True])
    run(state, io)
    assert io.probes[:2] == [None, None]
    assert "didn't finish" in io.text()


def test_probe_texts_writes_names_only(tmp_path, monkeypatch):
    import sqlite3
    import tilia.setup_wizard as sw
    db = tmp_path / "chat.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, date INTEGER)")
    conn.execute("INSERT INTO message (text, date) VALUES ('secret words', 999999999999999999)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(sw.imessage, "CHAT_DB", db)
    monkeypatch.setattr(sw.contacts, "find_chats", lambda q: [
        {"name": "Alex Doe", "chat_id": 31, "handle": "+15550101234"}])
    out = tmp_path / "probe.json"
    sw.probe_texts(out, "alex")
    data = json.loads(out.read_text())
    assert data["can_read"] is True and data["recent"] == 1
    assert data["matches"] == [{"name": "Alex Doe", "chat_id": 31, "handle": "+15550101234"}]
    assert "secret" not in out.read_text()


def test_real_probe_uses_launchd_and_cleans_up(home, monkeypatch):
    import tilia.setup_wizard as sw
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["launchctl", "submit"]:
            out = Path(cmd[cmd.index("--out") + 1])
            out.write_text(json.dumps({"can_read": True, "recent": 3, "matches": []}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    io = sw.RealIO()
    monkeypatch.setattr(io, "run", fake_run)
    monkeypatch.setattr(io, "sleep", lambda s: None)
    assert io.probe("alex") == {"can_read": True, "recent": 3, "matches": []}
    submit = next(c for c in calls if c[:2] == ["launchctl", "submit"])
    assert "-l" in submit and sw.PROBE_LABEL in submit
    assert submit[submit.index("--") + 1].endswith(".venv/bin/python")
    assert calls[-1] == ["launchctl", "remove", sw.PROBE_LABEL]


@pytest.mark.real_subprocess  # bash + git in a temp repo, uv/install faked
def test_update_audit_prompt_comes_from_installed_version(tmp_path):
    repo, env = _tagged_repo(tmp_path, real_audit=True)
    r = subprocess.run(["bash", str(repo / "update")], input="no\n", capture_output=True,
                       text=True, env=env, timeout=30)
    assert "INSTALLED" in r.stdout and "INCOMING" not in r.stdout


@pytest.mark.real_subprocess  # bash + git in a temp repo, uv/install faked
def test_update_never_moves_backward(tmp_path):
    repo, env = _tagged_repo(tmp_path)
    _git(repo, "checkout", "-q", "v0.2.0")
    (repo / "later.txt").write_text("newer than the release\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "three")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True).stdout
    r = subprocess.run(["bash", str(repo / "update")], input="yes\n", capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode == 0
    assert "past the newest" in r.stdout
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True).stdout == head


@pytest.mark.real_subprocess  # bash + git in a temp repo, uv/install faked
def test_failed_update_rolls_back(tmp_path):
    repo, env = _tagged_repo(tmp_path, new_install_fails=True)
    r = subprocess.run(["bash", str(repo / "update")], input="yes\n", capture_output=True,
                       text=True, env=env, timeout=30)
    assert r.returncode != 0
    assert "Tilia is back on v0.1.0" in r.stdout + r.stderr
    assert _head_tag(repo) == "v0.1.0"
    assert "fake install" in r.stdout  # the old version was restarted


# ---- final fix: re-entry, pairing, first message --------------------------------

def _screens_for(io):
    import re
    return [m.group(0) for sc in io.screens() for m in [re.search(r"Step \S+ of 6", sc)] if m]


def _finished(state):
    write_state(state, list(STEPS))
    cfg = config.Config(chat_id=4242, model="opus", goal="Be kinder to myself.",
                        tone="gentle", referrals="gently",
                        threads=[{"name": "Alex Doe", "chat_id": 31}])
    config.save(cfg)


def test_all_set_screen_lists_the_reentry_commands(state):
    write_state(state, list(STEPS))
    io = FakeIO([])
    run(state, io)
    for cmd in ("./setup texts", "./setup style", "./setup ai", "./setup import", "./setup bot",
                "./setup all"):
        assert cmd in io.text()


def test_setup_texts_reruns_only_texts_and_restarts_the_agent(state):
    _finished(state)
    io = FakeIO([""], installed=True)
    assert run(state, io, redo="texts") == 0
    assert set(_screens_for(io)) == {"Step 5 of 6"}
    assert config.load().threads == [{"name": "Alex Doe", "chat_id": 31}]
    assert done_steps(state) == list(STEPS)
    assert io.runs.count(["scripts/install.sh"]) == 1
    assert io.sent == [] and io.tokens == []


def test_reentry_without_an_installed_agent_does_not_install(state):
    _finished(state)
    io = FakeIO(["n"], installed=False)
    assert run(state, io, redo="texts") == 0
    assert ["scripts/install.sh"] not in io.runs


def test_setup_style_keeps_current_answers_on_enter(state):
    _finished(state)
    io = FakeIO(["", "", ""], installed=True)
    assert run(state, io, redo="style") == 0
    assert set(_screens_for(io)) == {"Step 5b of 6"}
    cfg = config.load()
    assert (cfg.goal, cfg.tone, cfg.referrals) == ("Be kinder to myself.", "gentle", "gently")
    io = FakeIO(["Sleep before midnight.", "1", "1"], installed=True)
    run(state, io, redo="style")
    cfg = config.load()
    assert (cfg.goal, cfg.tone, cfg.referrals) == ("Sleep before midnight.", "very_direct", "never")


def test_setup_model_reruns_login_and_keeps_model_on_enter(state):
    _finished(state)
    io = FakeIO(["", ""], installed=True)  # Enter keeps Claude, Enter keeps opus
    assert run(state, io, redo="model") == 0
    assert set(_screens_for(io)) == {"Step 4 of 6"}
    assert config.load().model == "opus" and config.load().provider == "claude"
    assert io.runs.count(["scripts/install.sh"]) == 1


def test_setup_bot_redoes_bot_pairing_and_first_message(state, monkeypatch):
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    _finished(state)
    io = FakeIO(["n", "", GOOD, "y", "n"], installed=True, tg_updates=[[U(9, 4343, "418207")]])
    assert run(state, io, redo="bot") == 0
    assert set(_screens_for(io)) == {"Step 2 of 6", "Step 3 of 6", "Step 6 of 6"}
    assert io.tokens == [GOOD] and config.load().chat_id == 4343
    assert io.sent == [(4343, FIRST_MESSAGE)]
    assert io.runs.count(["scripts/install.sh"]) == 1  # keep_running's own install only
    assert config.load().goal == "Be kinder to myself."


def test_setup_all_walks_every_step(state, monkeypatch):
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    _finished(state)
    io = FakeIO(["n", "", GOOD, "y", "", "", "", "", "", "", "skip", "n"], installed=True,
                tg_updates=[[U(9, 4242, "418207")]])
    assert run(state, io, redo="all") == 0
    labels = set(_screens_for(io))
    assert {f"Step {n} of 6" for n in ("1", "2", "3", "4", "5", "5b", "5c", "6")} == labels
    cfg = config.load()
    assert cfg.model == "opus" and cfg.goal == "Be kinder to myself."
    assert done_steps(state) == list(STEPS)


def test_redoing_make_bot_unmarks_pairing(state, monkeypatch):
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    write_state(state, ["check_mac", "pair_phone", "claude_login"])
    io = FakeIO(["n", "", GOOD, "y"], tg_updates=[[U(9, 4242, "418207")]])
    run(state, io)  # walks away at texts
    assert "Step 3 of 6" in _screens_for(io)
    assert config.load().chat_id == 4242


def test_main_passes_the_step_and_rejects_unknown(monkeypatch, capsys, home):
    import tilia.setup_wizard as sw
    seen = []
    monkeypatch.setattr(sw, "run", lambda state, io, redo=None: seen.append(redo) or 0)
    assert sw.main(["texts"]) == 0 and seen == ["texts"]
    assert sw.main([]) == 0 and seen[-1] is None
    assert sw.main(["bogus"]) == 2
    assert "./setup texts" in capsys.readouterr().out


def test_pairing_ignores_slash_commands(state, monkeypatch):
    write_state(state, ["check_mac", "make_bot"])
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    starts = [[U(i, 4242, "/start")] for i in range(1, 8)]
    io = FakeIO(["y"], tg_updates=starts + [[U(8, 4242, "418207")]])
    io.tokens = [GOOD]
    run(state, io)
    assert "wrong codes" not in io.text()
    assert config.load().chat_id == 4242


def test_real_io_stores_the_first_message_as_a_coach_turn(home):
    import tilia.setup_wizard as sw
    paths.data_dir()
    sw.RealIO().save_first_message(FIRST_MESSAGE)
    t = Store(paths.db_path()).recent_turns(5)
    assert [(x.role, x.text) for x in t] == [("coach", FIRST_MESSAGE)]


def test_config_save_is_atomic(home, monkeypatch):
    import os
    paths.data_dir()
    seen = []
    real = os.replace
    monkeypatch.setattr(config.os, "replace", lambda a, b: seen.append((a, b)) or real(a, b))
    config.save(config.Config(goal="x"))
    assert len(seen) == 1 and Path(seen[0][1]) == paths.config_path()
    assert config.load().goal == "x"
    assert oct(paths.config_path().stat().st_mode & 0o777) == "0o600"
    assert [f.name for f in paths.data_dir().iterdir() if "tmp" in f.name] == []


# ---- privacy audit: ownership, account, admins, re-entry, probe -------------------

def _flat(s):
    return " ".join(s.split())


def test_make_bot_says_make_it_from_your_own_phone(state):
    write_state(state, ["check_mac"])
    io = FakeIO(["n"])
    run(state, io)
    assert ("Make the bot from the Telegram app on your own phone. Whoever creates a bot "
            "can always get a new key for it.") in _flat(io.screens()[0])
    from tilia.setup_wizard import HELP
    help_ = _flat(HELP["make_bot"])
    assert "/mybots" in help_ and "/setjoingroups" in help_ and "Disable" in help_


CLAUDE_STATUS = ("Check this is your own Claude account: run `claude`, type /status, "
                 "and confirm the email.")


@pytest.mark.parametrize("errors", [[], [__import__("tilia.claude").claude.LimitError("x")]])
def test_claude_login_asks_her_to_confirm_the_account(state, errors):
    write_state(state, ["check_mac", "make_bot", "pair_phone"])
    io = FakeIO(["", ""], claude_errors=errors)
    run(state, io)
    assert CLAUDE_STATUS in _flat(io.text())


def test_check_mac_names_other_admins(state):
    io = FakeIO([], admins="GroupMembership: root fakeuser jordan casey\n")
    run(state, io)
    assert ["dscl", ".", "-read", "/Groups/admin", "GroupMembership"] in io.runs
    assert ("These other accounts on this Mac are administrators and could read your "
            "Tilia files: jordan, casey.") in io.text()
    assert "check_mac" in done_steps(state)  # informs, never blocks


@pytest.mark.parametrize("admins", ["GroupMembership: root fakeuser\n", "", None])
def test_check_mac_quiet_when_only_you_are_admin(state, admins):
    io = FakeIO([], admins=admins)
    run(state, io)
    assert "administrators" not in io.text()
    assert "check_mac" in done_steps(state)


def test_reentry_stops_the_agent_first_then_restarts_it(state):
    _finished(state)
    io = FakeIO(["n"], installed=True)
    assert run(state, io, redo="texts") == 0
    assert io.runs[0] == ["stop-agent"]
    assert io.runs[-1] == ["scripts/install.sh"]


def test_reentry_restarts_the_agent_even_if_she_walks_away(state):
    _finished(state)
    io = FakeIO([], installed=True)  # EOF at the first prompt
    assert run(state, io, redo="texts") == 0
    assert io.runs == [["stop-agent"], ["scripts/install.sh"]]


OLD_KEY = "987654321:AAH" + "y" * 32


def test_quitting_setup_bot_before_pairing_keeps_the_old_bot(state):
    """v0.2.1 fix 3: a new key with the old chat must never run. Walking away after the
    new key is saved but before the phone is paired puts the old key back and restarts
    Tilia exactly as it was."""
    _finished(state)
    io = FakeIO(["n", "", GOOD], installed=True)  # no code arrives; EOF at "for a new code"
    io.tokens = [OLD_KEY]
    assert run(state, io, redo="bot") == 0
    assert io.tokens == [OLD_KEY, GOOD, OLD_KEY]  # the new key was saved, then undone
    assert config.load().chat_id == 4242
    assert done_steps(state) == list(STEPS)  # as before ./setup bot
    assert io.runs[0] == ["stop-agent"] and io.runs[-1] == ["scripts/install.sh"]
    assert io.runs.count(["scripts/install.sh"]) == 1
    t = _flat(io.text())
    assert "Tilia kept your old bot, since your phone isn't paired with the new one yet." in t
    assert "Run ./setup bot to switch." in t and "Paused." not in t


def test_quitting_setup_bot_with_no_old_key_leaves_tilia_stopped(state):
    _finished(state)
    io = FakeIO(["n", "", GOOD], installed=True)  # Keychain had no key to go back to
    assert run(state, io, redo="bot") == 0
    assert io.tokens == [GOOD]
    assert ["scripts/install.sh"] not in io.runs  # never the new key with the old chat
    assert "make_bot" in done_steps(state) and "pair_phone" not in done_steps(state)
    assert "Tilia stays stopped until your phone is paired with the new bot, so run " \
           "./setup to finish." in _flat(io.text())


def test_quitting_setup_bot_before_a_new_key_restarts_as_it_was(state):
    _finished(state)
    io = FakeIO(["n", ""], installed=True)  # EOF at the key prompt
    io.tokens = [OLD_KEY]
    assert run(state, io, redo="bot") == 0
    assert io.tokens == [OLD_KEY] and io.runs[-1] == ["scripts/install.sh"]
    assert "kept your old bot" not in io.text()


def test_quitting_setup_bot_after_pairing_restarts_with_the_new_bot(state, monkeypatch):
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    _finished(state)
    io = FakeIO(["n", "", GOOD, "y"], installed=True, tg_updates=[[U(9, 4343, "418207")]])
    io.tokens = [OLD_KEY]
    assert run(state, io, redo="bot") == 0  # EOF at keep_running's battery question
    assert io.tokens == [OLD_KEY, GOOD] and config.load().chat_id == 4343
    assert io.runs[-1] == ["scripts/install.sh"]


def test_first_setup_never_stops_an_agent(state):
    io = FakeIO([], installed=True)
    run(state, io)
    assert ["stop-agent"] not in io.runs


def _redo_bot(state, monkeypatch, turns):
    import tilia.setup_wizard as sw
    monkeypatch.setattr(sw.secrets, "randbelow", lambda n: 418207)
    _finished(state)
    io = FakeIO(["n", "", GOOD, "y", "n"], installed=True, turns=turns,
                tg_updates=[[U(9, 4343, "418207")]])
    assert run(state, io, redo="bot") == 0
    assert io.runs[0] == ["stop-agent"]
    return io


def test_setup_bot_keeps_quiet_when_a_conversation_exists(state, monkeypatch):
    io = _redo_bot(state, monkeypatch, turns=7)
    assert io.sent == [] and io.first_turns == []
    assert config.load().chat_id == 4343


def test_setup_bot_resends_hello_but_does_not_store_it_twice(state, monkeypatch):
    io = _redo_bot(state, monkeypatch, turns=1)
    assert io.sent == [(4343, FIRST_MESSAGE)] and io.first_turns == []


def test_real_io_counts_turns(home):
    import tilia.setup_wizard as sw
    io = sw.RealIO()
    assert io.turn_count() == 0
    io.save_first_message(FIRST_MESSAGE)
    assert io.turn_count() == 1


def test_real_io_stop_agent_boots_out_without_removing_the_plist(monkeypatch):
    import tilia.setup_wizard as sw
    calls = []
    io = sw.RealIO()
    monkeypatch.setattr(io, "run", lambda cmd, **kw: calls.append(cmd)
                        or SimpleNamespace(returncode=0, stdout="", stderr=""))
    io.stop_agent()
    assert len(calls) == 1
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[0][2].endswith("/com.tilia.agent")


def test_real_probe_passes_the_name_in_a_private_file_not_argv(home, monkeypatch):
    import stat
    import tilia.setup_wizard as sw
    calls, seen = [], {}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["launchctl", "submit"]:
            q = Path(cmd[cmd.index("--name-file") + 1])
            seen["text"] = q.read_text(encoding="utf-8")
            seen["mode"] = stat.S_IMODE(q.stat().st_mode)
            seen["dir"] = q.parent
            Path(cmd[cmd.index("--out") + 1]).write_text(json.dumps(
                {"can_read": True, "recent": 0, "matches": []}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    io = sw.RealIO()
    monkeypatch.setattr(io, "run", fake_run)
    monkeypatch.setattr(io, "sleep", lambda s: None)
    io.probe("Jordan Q")
    assert not any("Jordan Q" in part for c in calls for part in c)
    assert seen == {"text": "Jordan Q", "mode": 0o600, "dir": paths.data_dir()}
    assert not any("query" in f.name for f in paths.data_dir().iterdir())


def test_probe_cli_reads_the_name_file(tmp_path, monkeypatch):
    import tilia.__main__ as m
    import tilia.setup_wizard as sw
    got = []
    monkeypatch.setattr(sw, "probe_texts", lambda out, name=None: got.append((out, name)))
    q = tmp_path / "q.txt"
    q.write_text("Jordan Q\n", encoding="utf-8")
    assert m.main(["probe-texts", "--out", str(tmp_path / "o.json"), "--name-file", str(q)]) == 0
    assert got == [(str(tmp_path / "o.json"), "Jordan Q")]


# ---- a bot she already made ---------------------------------------------------------

def test_already_made_bot_skips_botfather_and_goes_to_the_key(state):
    write_state(state, ["check_mac"])
    io = FakeIO(["y", GOOD])
    run(state, io)
    assert io.opened == []
    assert not any("Press Enter to open BotFather" in p for p in io.prompts)
    assert "/newbot" not in io.screens()[0]
    assert "whoever creates a bot can always get a new key for it" in io.text()
    assert len(io.secret_prompts) == 1 and io.tokens == [GOOD]
    assert "make_bot" in done_steps(state)


def test_no_bot_yet_opens_botfather(state):
    write_state(state, ["check_mac"])
    io = FakeIO(["n", "", GOOD])
    run(state, io)
    assert io.opened == ["https://t.me/BotFather"]
    assert "/newbot" in io.text()
    assert io.tokens == [GOOD]


def test_already_made_question_is_on_the_screen_and_fits(state):
    write_state(state, ["check_mac"])
    io = FakeIO([])
    run(state, io)
    scr = io.screens()[0]
    assert "[y] Yes, I have its key" in scr and "[n] No, help me make one" in scr
    assert len(scr.splitlines()) + 1 <= 24  # plus the prompt row


def test_already_made_invalid_answer_reasks(state):
    write_state(state, ["check_mac"])
    io = FakeIO(["maybe", "y", GOOD])
    run(state, io)
    assert "Please type y or n." in io.text()
    assert io.opened == [] and io.tokens == [GOOD]
    assert io.prompts[0] == io.prompts[1]


def test_help_at_the_already_made_question(state):
    write_state(state, ["check_mac"])
    from tilia.setup_wizard import HELP
    io = FakeIO(["help", "skip", "y", GOOD])
    run(state, io)
    assert HELP["make_bot"].splitlines()[0] in io.text()
    assert "Please type y or n." in io.text()  # skip is not offered
    assert io.tokens == [GOOD]



# ---- v0.1.4: ./setup texts shows and edits the current list --------------------------

ALEX = {"name": "Alex Doe", "chat_id": 31}
SAM = {"name": "Sam Roe", "chat_id": 32}
CHATS = {"sam": [{"name": "Sam Roe", "chat_id": 32, "handle": "+15550104321"}],
         "alex": [{"name": "Alex Doe", "chat_id": 31, "handle": "+15550101234"}]}


def _edit(state, inputs, threads=(ALEX,), backfill=365):
    _finished(state)
    cfg = config.load()
    cfg.threads, cfg.backfill_days = [dict(t) for t in threads], backfill
    config.save(cfg)
    io = FakeIO(inputs, installed=True, chats=CHATS)
    assert run(state, io, redo="texts") == 0
    return io, config.load()


def test_texts_rerun_shows_the_list_and_keeps_it_on_enter(state):
    io, cfg = _edit(state, [""], threads=(ALEX, SAM))
    assert "Tilia reads your texts with: Alex Doe, Sam Roe." in io.text()
    assert "[1] Add someone" in io.text() and "[3] Keep as is" in io.text()
    assert cfg.threads == [ALEX, SAM]
    assert "How far back" not in io.text()  # nothing added, no backfill question
    assert not any("Privacy_AllFiles" in u for u in io.opened)


def test_texts_add_keeps_existing_and_asks_backfill_for_new(state):
    io, cfg = _edit(state, ["1", "sam", "y", "", "", "2"])
    assert cfg.threads == [ALEX, SAM]
    assert cfg.backfill_days == sw_all_history()
    assert "Tilia reads your texts with: Alex Doe, Sam Roe." in io.text()  # menu shown again


def test_texts_add_never_duplicates(state):
    io, cfg = _edit(state, ["1", "alex", "", ""])
    assert cfg.threads == [ALEX]
    assert "How far back" not in io.text()


def test_texts_remove(state):
    io, cfg = _edit(state, ["2", "1", ""], threads=(ALEX, SAM))
    assert "[1] Alex Doe" in io.text() and "[2] Sam Roe" in io.text()
    assert cfg.threads == [SAM]


@pytest.mark.parametrize("inputs", [["skip"], ["1", "skip"], ["2", "skip"], ["1", "sam", "skip"],
                                    ["4", "skip"]])
def test_texts_skip_keeps_the_list(state, inputs):
    io, cfg = _edit(state, inputs, threads=(ALEX, SAM))
    assert cfg.threads == [ALEX, SAM]


def test_texts_skip_after_an_add_keeps_the_add(state):
    io, cfg = _edit(state, ["1", "sam", "y", "", "", "skip"], backfill=200)
    assert cfg.threads == [ALEX, SAM]
    assert cfg.backfill_days == 200  # skip at the backfill question keeps the current one


def test_texts_turn_off_needs_confirm(state):
    io, cfg = _edit(state, ["4", "n", ""], threads=(ALEX, SAM))
    assert cfg.threads == [ALEX, SAM]
    io, cfg = _edit(state, ["4", "y"], threads=(ALEX, SAM))
    assert cfg.threads == []


# ---- v0.1.4: ./update restarts the running agent ------------------------------------

def _launchctl(loaded, rc=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=0 if loaded else 113, stdout="", stderr="")
        return SimpleNamespace(returncode=rc, stdout="", stderr="")
    return run, calls


def test_restart_agent_kickstarts_when_loaded(tmp_path):
    import tilia.setup_wizard as sw
    run, calls = _launchctl(loaded=True)
    assert sw.restart_agent(run=run, uid=501, plist=tmp_path / "a.plist") == 0
    assert calls == [["launchctl", "print", "gui/501/com.tilia.agent"],
                     ["launchctl", "kickstart", "-k", "gui/501/com.tilia.agent"]]


def test_restart_agent_bootstraps_when_not_loaded(tmp_path):
    import tilia.setup_wizard as sw
    run, calls = _launchctl(loaded=False)
    plist = tmp_path / "a.plist"
    assert sw.restart_agent(run=run, uid=501, plist=plist) == 0
    assert calls == [["launchctl", "print", "gui/501/com.tilia.agent"],
                     ["launchctl", "bootstrap", "gui/501", str(plist)]]


def test_restart_agent_reports_failure(tmp_path):
    import tilia.setup_wizard as sw
    run, _ = _launchctl(loaded=True, rc=5)
    assert sw.restart_agent(run=run, uid=501, plist=tmp_path / "a.plist") == 5


def test_main_restart_agent_subcommand(monkeypatch):
    import tilia.setup_wizard as sw
    seen = []
    monkeypatch.setattr(sw, "restart_agent", lambda **kw: seen.append(kw) or 0)
    monkeypatch.setattr(sw, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert sw.main(["restart-agent"]) == 0 and seen == [{}]
