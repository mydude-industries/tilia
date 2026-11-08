"""The ./setup walkthrough: 6 calm steps (plus 5b and 5c), one screen at a time, resumable.

Every outside effect goes through `io` (see RealIO) so tests can script the whole thing.
Progress lives in setup_state.json in the data folder: {"done": [step, ...]}.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from . import codex, codex_install, config, contacts, imessage, paths, profile
from .claude import LimitError, MissingCLI, NotLoggedIn, UnsupportedCLI
from .ui import UI

TOTAL = 6
# "claude_login" is the saved name of step 4, kept so finished setups stay finished; the
# step itself connects either AI (ChatGPT through Codex, or Claude).
STEPS = ("check_mac", "make_bot", "pair_phone", "claude_login", "texts", "style", "bring_ai",
         "keep_running")
# (number shown, title, minutes)
META = {
    "check_mac": ("1", "Check your Mac", 1),
    "make_bot": ("2", "Make your private bot", 2),
    "pair_phone": ("3", "Connect your phone", 1),
    "claude_login": ("4", "Connect your AI", 2),
    "texts": ("5", "Let Tilia read your texts (optional)", 2),
    "style": ("5b", "How Tilia talks to you", 1),
    "bring_ai": ("5c", "Bring what your AI already knows (optional)", 2),
    "keep_running": ("6", "Keep it running", 1),
}

# ./setup <step>: re-run part of setup after it's finished.
REDO = {
    "texts": ("texts",),
    "style": ("style",),
    "ai": ("claude_login",),
    "model": ("claude_login",),  # the old name, still accepted
    "import": ("bring_ai",),
    "bot": ("make_bot", "pair_phone", "keep_running"),
    "all": STEPS,
}
REDO_HELP = (
    "  ./setup texts   which text threads Tilia reads",
    "  ./setup style   your goal, and how Tilia talks to you",
    "  ./setup ai      ChatGPT or Claude, and which model",
    "  ./setup import  bring what your AI already knows about you",
    "  ./setup bot     a new Telegram bot or phone",
    "  ./setup all     the whole walk-through again",
)
AGENT_LABEL = "com.tilia.agent"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"

FIRST_MESSAGE = "Hi, I'm Tilia. I'm yours, and only yours. What's on your mind?"
PAIR_TIMEOUT_S = 600
POLL_S = 2
ALL_HISTORY_DAYS = 36500

FILEVAULT_URL = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?FileVault"
PRIVACY_URL = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
BOTFATHER_URL = "https://t.me/BotFather"
MAX_WRONG_CODES = 5
PROBE_LABEL = "com.tilia.probe"
PROBE_TIMEOUT_S = 20
CLAUDE_INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"
# Codex (how Tilia talks to ChatGPT) is installed from OpenAI's pinned release by
# codex_install.py; this is the identity the binary must carry before Tilia ever runs it.
CODEX_SIGNER = "Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)"
CHATGPT_RETRY = {"r": "later", "c": "claude"}
CHATGPT_MODELS = {"1": "gpt-6-luna", "2": "gpt-6.1-sol"}
CLAUDE_MODELS = {"1": "sonnet", "2": "opus"}
CLAUDE_ACCOUNT = ("Check this is your own Claude account: run `claude`, type /status,",
                  "and confirm the email.")
CHATGPT_ACCOUNT = ("Check this is your own ChatGPT account: open chatgpt.com, click your",
                   "profile picture, and confirm the email.")
DATA_CONTROLS = ('In ChatGPT, Settings → Data Controls: turn off "Improve the model for everyone"',
                 "if you don't want your chats used for training.")
IMPORT_PROMPT = (
    "I'm setting up a private coach that will help me grow and handle hard moments better. "
    "Write a detailed summary of everything you know about me, written to that coach: my life "
    "and the people in it, my relationships, my patterns (the good ones and the ones I'm "
    "working on), what I struggle with, my goals, and what helps me when things are hard. Use "
    "plain sentences. Leave out passwords, account numbers, addresses and anything I wouldn't "
    "want written down. Give it to me as a downloadable Markdown file named tilia-profile.md.")
DOWNLOAD_NAME = re.compile(r"tilia-profile( \(\d+\))?\.md", re.IGNORECASE)
IMPORT_SUFFIXES = (".txt", ".md", ".rtf")
IMPORT_PREVIEW_LINES = 8
IMPORT_AFTER = ("The answer may still be on your clipboard (copy something else to clear it), and "
                "you may want to delete that chat in your AI's app.")

HELP = {
    "check_mac": (
        "This step checks your Mac is ready. Nothing is changed without asking.\n"
        "FileVault encrypts your disk, so only your login password unlocks Tilia's files.\n"
        "To turn it on: System Settings → Privacy & Security → FileVault → Turn On."),
    "make_bot": (
        "A Telegram bot is a private chat window that only Tilia controls.\n"
        "Already made one? Answer y and paste its key. To get the key again, open\n"
        "@BotFather, send /mybots, pick your bot, then API Token.\n"
        "Don't have one yet? Answer n.\n"
        "In Telegram, open @BotFather, send /newbot, give any name, then a username\n"
        "ending in \"bot\" (like my_tilia_bot). BotFather replies with a key that\n"
        "looks like 123456789:AAH... Copy that whole key and paste it here.\n"
        "The key stays hidden while you paste it, so nothing shows; just press Return.\n"
        "Make the bot on your own phone: whoever creates a bot can always get a new key.\n"
        "To confirm it's yours: in @BotFather send /mybots; your bot is in that list.\n"
        "Also send /setjoingroups, pick your bot, then Disable, so nobody adds it to a group."),
    "pair_phone": (
        "In Telegram on your phone, search for your bot's username and open it.\n"
        "Tap Start if you see it, then send the 6 numbers shown above.\n"
        "Only the phone that sends this code can talk to Tilia, and you confirm it here."),
    "claude_login": (
        "Tilia thinks with an AI you already pay for, running on this Mac under your login.\n"
        "ChatGPT: Tilia uses OpenAI's Codex program. Setup downloads the one version Tilia\n"
        "has checked from OpenAI's release page, verifies its checksum and signature, keeps it\n"
        "in Tilia's own folder, and logs it in through your browser. Nothing it does is saved.\n"
        "Claude: open a new Terminal window (Cmd+N), type  claude , follow the login,\n"
        "then come back here and press Enter.\n"
        "\"Lighter\" is the right choice for a $20 plan; you can change it later: ./setup ai"),
    "texts": (
        "This is optional; type n to skip it.\n"
        "Full Disk Access lets Tilia's Python open the Messages database, read-only.\n"
        "In the list, click +, press Cmd+Shift+G, paste the path shown, click Open.\n"
        "If that path is already listed and switched on, just press Enter.\n"
        "Messages in iCloud: iPhone Settings → your name → iCloud → Messages.\n"
        "Type skip at any prompt in this step to leave texts off."),
    "style": (
        "There are no wrong answers, and you can change these later.\n"
        "The goal is kept in your own words, exactly as you type it."),
    "bring_ai": (
        "Optional. Your AI (ChatGPT or Claude) may already know a lot about you; this lets\n"
        "Tilia start from that instead of from nothing.\n"
        "Setup copies a question to your clipboard. Paste it into your AI, then save its\n"
        "answer as a file called tilia-profile.md to download. Save it in the \"Tilia Import\"\n"
        "folder it opens (in your home folder, so it never syncs to iCloud), or just leave\n"
        "it in Downloads: setup looks there only for that exact name, nowhere else.\n"
        "You see the first lines before anything is kept. After the import, the file is\n"
        "overwritten and deleted. Type skip at any prompt to leave this out."),
    "keep_running": (
        "Tilia runs quietly in the background whenever your Mac is awake.\n"
        "A closed lid always sleeps; messages wait for up to a day and get answered then."),
}

_AUDIT_FALLBACK = (
    "Read this entire project. Tell me plainly: does it send my data anywhere other than "
    "api.telegram.org and the local AI command (`claude` or `codex`)? Could anyone other than "
    "me read my "
    "conversations, profile or texts? Point to the exact files and lines for every network "
    "call and every place data is written.")


class Back(Exception):
    """The person asked to leave this (optional) step."""


# ---- state ---------------------------------------------------------------------------

def _load_done(state_path: Path) -> list[str]:
    try:
        data = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return [s for s in data.get("done", []) if s in STEPS]
    except (OSError, ValueError, AttributeError):
        return []


def _mark_done(state_path: Path, step: str) -> None:
    done = _load_done(state_path)
    if step not in done:
        done.append(step)
    done = [s for s in STEPS if s in done]
    p = Path(state_path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"done": done}), encoding="utf-8")
    tmp.replace(p)


def _unmark(state_path: Path, step: str) -> None:
    done = [s for s in _load_done(state_path) if s != step]
    p = Path(state_path)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"done": done}), encoding="utf-8")
    tmp.replace(p)


# ---- the walkthrough -----------------------------------------------------------------

class Wizard:
    def __init__(self, io, state_path):
        self.io = io
        self.state_path = state_path
        self.ui = UI(io)
        self.bot_name = ""
        self.restarted = False  # keep_running (or the end of a re-run) started the agent
        self.redo: tuple = ()   # the steps a re-run asked for (run() sets it)

    def screen(self, step: str, lines: list[str]) -> None:
        n, title, minutes = META[step]
        self.ui.screen(n, TOTAL, title, minutes, lines, help=HELP[step])

    def enter(self, what: str) -> None:
        self.ui.ask(f"Press Enter {what}")

    def yes_no(self, prompt: str, ask=None) -> bool:
        ask = ask or self.ui.ask
        while True:
            ans = ask(f"{prompt} [y]/[n]: ").strip().lower()
            if ans in ("y", "yes", "n", "no"):
                return ans.startswith("y")
            self.ui.fail("Please type y or n.")

    # 1 ---------------------------------------------------------------------------------
    def check_mac(self) -> bool:
        self.screen("check_mac", ["Checking two things: Python and FileVault."])
        self.ui.ok(f"Python {sys.version_info.major}.{sys.version_info.minor} is ready")
        fv = self._filevault_off()
        if fv is None:
            self.ui.fail("Couldn't check FileVault, so make sure it's on: System Settings → "
                         "Privacy & Security → FileVault.")
        elif fv:
            self.ui.fail("FileVault is off, so anyone with this Mac's disk could read your Tilia files.")
            self.ui.say("I'll open the FileVault settings; click Turn On there.")
            self.enter("to open FileVault settings: ")
            if not self.io.open_url(FILEVAULT_URL):
                self.io.open_url(PRIVACY_URL)
            self.enter("when it's on (or to continue without it): ")
            if self._filevault_off():
                self.ui.say("Continuing without FileVault. You can turn it on any time.")
            else:
                self.ui.ok("FileVault is on")
        else:
            self.ui.ok("FileVault is on")
        others = self._other_admins()
        if others:
            self.ui.fail("These other accounts on this Mac are administrators and could read "
                         f"your Tilia files: {', '.join(others)}.")
        return True

    def _other_admins(self) -> list[str]:
        """Admin accounts other than root and the person running setup. Informs only."""
        try:
            r = self.io.run(["dscl", ".", "-read", "/Groups/admin", "GroupMembership"],
                            capture_output=True, text=True, timeout=15)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []
        out = (r.stdout or "") if getattr(r, "returncode", 1) == 0 else ""
        names = out.split(":", 1)[1].split() if ":" in out else []
        me = self.io.username()
        return [n for n in dict.fromkeys(names) if n not in ("root", me)]

    def _filevault_off(self) -> bool | None:
        """True/False, or None when the check itself didn't work (informs, never blocks)."""
        try:
            r = self.io.run(["fdesetup", "status"], capture_output=True, text=True, timeout=15)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        return "FileVault is Off" in (r.stdout or "")

    def _claude_version(self) -> str | None:
        try:
            r = self.io.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        return (r.stdout or "").strip() if r.returncode == 0 else None

    def _claude_logged_in(self) -> bool:
        """`claude auth status` exits 0 when logged in, 1 when not (no model call)."""
        try:
            r = self.io.run(["claude", "auth", "status"], capture_output=True, text=True,
                            timeout=30)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0

    def _ensure_claude(self) -> None:
        while self._claude_version() is None:
            self.ui.fail("The Claude app isn't installed yet.")
            self.enter("to install it with Claude's official installer: ")
            self.io.run(["bash", "-c", CLAUDE_INSTALL])
            if self._claude_version() is None:
                self.ui.fail("That didn't finish; check your internet connection and try again.")
        self.ui.ok("The Claude app is installed")

    # 2 ---------------------------------------------------------------------------------
    def make_bot(self) -> bool:
        self.screen("make_bot", [
            "Your bot is a private chat on Telegram that only you and Tilia use.",
            "",
            "Already made your bot?",
            "  [y] Yes, I have its key",
            "  [n] No, help me make one",
        ])
        while True:
            ans = self.ui.ask("y or n: ").strip().lower()
            if ans in ("y", "yes", "n", "no"):
                break
            self.ui.fail("Please type y or n.")
        if ans.startswith("y"):
            self.ui.say("Made it from your own Telegram? Good — whoever creates a bot "
                        "can always get a new key for it.")
        else:
            for line in (
                "Make the bot from the Telegram app on your own phone.",
                "Whoever creates a bot can always get a new key for it.",
                "",
                "1. In BotFather, send  /newbot",
                "2. Give it any name, then a username ending in \"bot\"",
                "3. Copy the key it gives you (it looks like 123456789:AAH...)",
            ):
                self.ui.say(line)
            self.enter("to open BotFather in Telegram: ")
            self.io.open_url(BOTFATHER_URL)
        while True:
            token = self.ui.ask("Paste the key here (it stays hidden): ", secret=True).strip()
            if not token:
                self.ui.fail("Nothing arrived, so paste the key and press Return.")
                continue
            self.ui.ok("Got a key")
            me = self.io.telegram(token).get_me()
            if not me:
                self.ui.fail("Telegram didn't accept that key, so copy the whole key from BotFather and paste it again.")
                continue
            try:
                self.io.set_token(token)
            except ValueError:
                self.ui.fail("That key has an unusual shape, so copy it again from BotFather.")
                continue
            except RuntimeError:
                self.ui.fail("Your Keychain didn't save the key, so unlock your Mac and paste it again.")
                continue
            self.bot_name = me.get("username") or ""
            self.ui.ok(f"Bot @{self.bot_name} saved in your Keychain" if self.bot_name
                       else "Bot saved in your Keychain")
            return True

    # 3 ---------------------------------------------------------------------------------
    def pair_phone(self) -> bool:
        try:
            token = self.io.get_token()
        except RuntimeError:
            self.screen("pair_phone", [])
            self.ui.fail("Your bot key is missing from Keychain, so run ./setup again to add it.")
            _unmark(self.state_path, "make_bot")
            return False
        tg = self.io.telegram(token)
        if not self.bot_name:
            me = tg.get_me() or {}
            self.bot_name = me.get("username") or ""
        self._tg_offset = None
        warned = warn_now = False
        while True:
            code = f"{secrets.randbelow(1_000_000):06d}"
            shown = f"{code[:3]} {code[3:]}"
            bot = f"@{self.bot_name}" if self.bot_name else "your bot"
            self.screen("pair_phone", [
                f"On your phone, open Telegram, find {bot}, and send it this code:",
                "",
                f"      {shown}",
                "",
                "I'll wait here for up to 10 minutes.",
            ])
            if warn_now:  # shown under the fresh code, so the clear doesn't hide it
                warn_now = False
                self.ui.fail("Several wrong codes arrived, so here's a fresh one; send it only from your own phone.")
            result, u = self._wait_for_code(tg, {code, shown})
            if result == "match":
                who = u.sender_name or "someone with no name"
                if u.sender_username:
                    who += f" (@{u.sender_username})"
                self.ui.say(f"The code came from Telegram user {who}.")
                if not self.yes_no("Is that you?"):
                    continue  # a fresh code
                cfg = config.load()
                cfg.chat_id = u.chat_id
                config.save(cfg)
                # The loop must not treat the code as her first message.
                self.io.save_offset(self._tg_offset)
                self.ui.ok("Your phone is connected")
                return True
            if result == "too_many":
                warn_now, warned = not warned, True
                continue
            self.ui.fail("No code arrived in 10 minutes, so check you sent it to the right bot.")
            self.enter("for a new code: ")

    def _wait_for_code(self, tg, accepted: set[str]):
        """("match", update) | ("too_many", None) | ("timeout", None).
        Only a private chat can pair; every other text (except a /command, which is
        ignored) counts as a wrong code."""
        deadline = self.io.now() + PAIR_TIMEOUT_S
        wrong = 0
        while self.io.now() < deadline:
            for u in tg.updates(self._tg_offset):
                self._tg_offset = u.update_id + 1
                if u.kind != "text" or u.text is None or u.text.strip().startswith("/"):
                    continue  # /start and other commands are not codes
                if u.chat_type == "private" and u.text.strip() in accepted:
                    return "match", u
                wrong += 1
                if wrong >= MAX_WRONG_CODES:
                    return "too_many", None
            self.io.sleep(POLL_S)
        return "timeout", None

    # 4 ---------------------------------------------------------------------------------
    def claude_login(self) -> bool:  # the step's saved name; it connects either AI
        return self.connect_ai()

    def connect_ai(self) -> bool:
        cfg = config.load()
        self.screen("claude_login", [
            "Tilia thinks with an AI you already use, on this Mac, under your own login.",
            "[1] ChatGPT (through OpenAI's Codex app)   [2] Claude (through the Claude app)",
            "A $20 plan of either is enough.",
        ])
        default = self._ai_default(cfg)
        provider = self.ui.choose("Choose 1 or 2" + (f" (Enter = {default})" if default else "")
                                  + ": ", {"1": "chatgpt", "2": "claude"}, default=default)
        if provider == "chatgpt":
            outcome = self._connect_chatgpt()
            if outcome == "later":
                self.ui.say("Okay. Run ./setup again when you want to try ChatGPT again; "
                            "you'll be right here.")
                return False
            if outcome == "claude":
                self.ui.say("Using Claude instead.")
                provider = "claude"
        if provider == "claude":
            self._connect_claude()
        cfg = config.load()
        cfg.provider = provider
        config.save(cfg)
        return True

    def _ai_default(self, cfg) -> str | None:
        """A re-run offers the current choice; a first run offers whichever one is already
        logged in. Both or neither: she has to choose."""
        if "claude_login" in self.redo:
            return "1" if cfg.provider == "chatgpt" else "2"
        # ChatGPT is judged by Tilia's own CODEX_HOME having a login, not by running codex:
        # the binary is never run before its signature is checked (see _ensure_codex).
        chatgpt, claude = self._codex_auth_present(), self._claude_logged_in()
        if chatgpt != claude:
            return "1" if chatgpt else "2"
        return None

    # -- ChatGPT (Codex) --
    def _codex_env(self) -> dict:
        return codex._env(str(paths.codex_home()))

    def _codex_auth_present(self) -> bool:
        return (paths.codex_home() / "auth.json").exists()

    def _codex_logged_in(self) -> bool:
        if not self.io.codex_installed():
            return False
        try:
            r = self.io.run([str(paths.codex_bin()), "login", "status"], capture_output=True,
                            text=True, timeout=30, env=self._codex_env())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0

    def _codex_login(self) -> None:
        """OpenAI's own login (it opens the browser), into Tilia's private CODEX_HOME."""
        try:
            self.io.run([str(paths.codex_bin()), "login"], env=self._codex_env())
        except (OSError, ValueError):
            pass

    def _chatgpt_failed(self, sentence: str) -> str:
        """One plain sentence, then her choice: "later" or "claude". Never forced."""
        self.ui.fail(sentence)
        return self.ui.choose("[r] try ChatGPT again later   [c] use Claude instead: ",
                              CHATGPT_RETRY)

    def _ensure_codex(self) -> str:
        """Tilia's own copy of the reviewed Codex release, verified as OpenAI's signed,
        notarized binary before Tilia runs it even once. "ok", "later" or "claude"."""
        if not self.io.codex_installed():
            self.ui.say("Tilia talks to ChatGPT through Codex, OpenAI's program. It downloads")
            self.ui.say(f"the one version it has checked ({codex_install.RELEASE[6:]}) from "
                        "OpenAI's release page, checks")
            self.ui.say("it against a recorded checksum, and keeps it in Tilia's own folder.")
            ans = self.ui.ask("Press Enter to download it (or type claude to use Claude instead): ")
            if ans.strip().lower() == "claude":
                return "claude"
            self.ui.say("Downloading (about 100 MB)...")
            problem = self.io.install_codex()
            if problem:
                return self._chatgpt_failed(f"The Codex download {problem}, so Tilia hasn't "
                                            "installed it; check your internet connection.")
        problem = self._codex_signature_problem(str(paths.codex_bin()))
        if problem:
            return self._chatgpt_failed(f"The downloaded Codex program {problem}, so Tilia "
                                        "won't run it.")
        self.ui.ok(f"Codex {codex_install.RELEASE[6:]} is installed, signed by OpenAI and "
                   "notarized by Apple")
        return "ok"

    def _codex_signature_problem(self, path) -> str | None:
        if not path:
            return "can't be found"

        def run(cmd):
            try:
                return self.io.run(cmd, capture_output=True, text=True, timeout=60)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return None

        def text(r):
            return (r.stdout or "") + (r.stderr or "")
        r = run(["codesign", "--verify", "--strict", path])
        if r is None or r.returncode != 0:
            return "fails Apple's signature check"
        r = run(["codesign", "-dvv", path])
        if r is None or r.returncode != 0 or f"Authority={CODEX_SIGNER}" not in text(r):
            return "isn't signed by OpenAI"
        r = run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature",
                 "-v", path])
        if r is None or r.returncode != 0 or "accepted" not in text(r):
            return "isn't notarized by Apple"
        return None

    def _connect_chatgpt(self) -> str:
        """"ok", "later" or "claude"."""
        outcome = self._ensure_codex()
        if outcome != "ok":
            return outcome
        while not self._codex_logged_in():
            self.ui.say("Press Enter to log in to ChatGPT: your browser opens, you log in there,")
            self.ui.say("then come back here. (Type claude to use Claude instead.)")
            if self.ui.ask("Press Enter to log in: ").strip().lower() == "claude":
                return "claude"
            self._codex_login()
            if not self._codex_logged_in():
                return self._chatgpt_failed("That login didn't finish.")
        email = self.io.codex_email()
        self.ui.ok(f"Logged in to ChatGPT as {email}" if email else "You're logged in to ChatGPT")
        cfg = config.load()
        while True:
            try:
                self.io.ai_check(config.Config(provider="chatgpt", chatgpt_model=cfg.chatgpt_model))
                self.ui.ok("ChatGPT answered")
            except LimitError:
                self.ui.ok("ChatGPT is at your usage limit right now, which is fine")
            except NotLoggedIn:
                self.ui.fail("ChatGPT says you're not logged in, so let's log in again.")
                self._codex_login()
                continue
            except UnsupportedCLI:
                return self._chatgpt_failed(
                    "This Codex is a version Tilia hasn't checked (it supports "
                    f"{', '.join('.'.join(map(str, v)) for v in sorted(codex.SUPPORTED_VERSIONS))}), "
                    "so Tilia won't run it; ./update gets a Tilia that supports it.")
            except (MissingCLI, RuntimeError):
                return self._chatgpt_failed("ChatGPT didn't answer; check your internet "
                                            "connection.")
            # The privacy promise, checked for real: Codex must have saved no session.
            left = self.io.codex_sessions()
            if left:
                return self._chatgpt_failed(f"Codex saved a session transcript ({len(left)} "
                                            "file(s) under codex-home/sessions), which it must "
                                            "never do; Tilia won't use ChatGPT until that is "
                                            "understood.")
            self.ui.ok("Codex saved nothing (no session file)")
            break
        self.screen("claude_login", [
            *CHATGPT_ACCOUNT, "", *DATA_CONTROLS, "",
            "Which ChatGPT model should Tilia use?",
            "[1] lighter (gpt-6-luna), fits the $20 plan (recommended)",
            "[2] stronger (gpt-6.1-sol), uses up your plan's limit much faster",
        ])
        default = "2" if cfg.chatgpt_model == CHATGPT_MODELS["2"] else "1"
        model = self.ui.choose(f"Choose 1 or 2 (Enter = {default}): ", CHATGPT_MODELS,
                               default=default)
        cfg = config.load()
        cfg.chatgpt_model = model
        config.save(cfg)
        self.ui.ok(f"Tilia will use ChatGPT ({model})")
        return "ok"

    # -- Claude --
    def _connect_claude(self) -> None:
        self._ensure_claude()
        cfg = config.load()
        while True:
            try:
                self.io.ai_check(config.Config(provider="claude", model=cfg.model))
                self.ui.ok("You're logged in to Claude")
                break
            except LimitError:
                self.ui.ok("You're logged in to Claude (at your usage limit right now, which is fine)")
                break
            except NotLoggedIn:
                self.ui.fail("You're not logged in to Claude yet.")
                self.ui.say("Open a new Terminal window, type  claude  and follow the login.")
                self.enter("when you've logged in: ")
            except MissingCLI:
                self._ensure_claude()
            except RuntimeError:
                self.ui.fail("Claude didn't answer, so check your internet connection.")
                self.enter("to try again: ")
        self.screen("claude_login", [
            *CLAUDE_ACCOUNT, "",
            "Which Claude model should Tilia use?",
            "[1] lighter (sonnet), fits the $20 plan (recommended)",
            "[2] strongest (opus)",
        ])
        default = "2" if cfg.model == "opus" else "1"  # a re-run keeps the current choice
        model = self.ui.choose(f"Choose 1 or 2 (Enter = {default}): ", CLAUDE_MODELS,
                               default=default)
        cfg = config.load()
        cfg.model = model
        config.save(cfg)
        self.ui.ok(f"Tilia will use Claude ({model})")

    # 5 ---------------------------------------------------------------------------------
    def texts(self) -> bool:
        self.screen("texts", [
            "Tilia can read a few text threads you choose, like the one with",
            "your partner, so it sees what was actually said, not a memory of it.",
            "",
            "It only reads. It never sends a text. Only the threads you pick.",
            "Those texts are shared with Claude only, under your own account.",
        ])
        try:
            if not self.yes_no("Turn this on?"):
                return self._texts_off()
            self._grant_disk_access()
            threads = self._pick_threads()
            if not threads:
                return self._texts_off()
            self.ui.say("")
            self.ui.say("How far back should Tilia read these threads the first time?")
            self.ui.say("[1] last 12 months (recommended)")
            self.ui.say("[2] all history")
            days = self._choose_t("Choose 1 or 2 (Enter = 1): ", {"1": 365, "2": ALL_HISTORY_DAYS}, "1")
        except Back:
            return self._texts_off()
        cfg = config.load()
        cfg.threads = threads
        cfg.backfill_days = days
        config.save(cfg)
        names = ", ".join(t["name"] for t in threads)
        self.ui.ok(f"Tilia will read your texts with {names}")
        return True

    def _ask_t(self, prompt: str) -> str:
        ans = self.ui.ask(prompt)
        if ans.strip().lower() == "skip":
            raise Back
        return ans

    def _choose_t(self, prompt, options, default=None):
        while True:
            ans = self._ask_t(prompt).strip().lower() or (default or "")
            if ans in options:
                return options[ans]
            keys = list(options)
            self.ui.fail(f"Please type {', '.join(keys[:-1])} or {keys[-1]}.")

    def _texts_off(self) -> bool:
        cfg = config.load()
        cfg.threads = []
        config.save(cfg)
        self.ui.ok("Skipped: Tilia won't read any texts")
        return True

    def _probe(self, name=None) -> dict:
        """Ask the launchd probe (which runs as Tilia's own Python, like the agent).
        Retries on timeout; raises Back if she types skip."""
        while True:
            self.ui.say("Checking from the background (a few seconds)...")
            res = self.io.probe(name)
            if res is not None:
                return res
            self.ui.fail("The check didn't finish, so press Enter to try again.")
            self._ask_t("Press Enter to try again (or type skip): ")

    def _grant_disk_access(self) -> None:
        path = self.io.python_path()
        self.screen("texts", [
            "Your Mac needs to let Tilia's Python open Messages (read-only).",
            "",
            "1. Press Enter: System Settings opens at Full Disk Access.",
            "2. Click +, press Cmd+Shift+G, paste this path, click Open:",
            f"   {path}",
            "3. Make sure its switch is on.",
        ])
        self._ask_t("Press Enter to open Full Disk Access: ")
        self.io.open_url(FDA_URL)
        self._ask_t("Press Enter when the switch is on: ")
        while True:
            res = self._probe()
            if res.get("can_read"):
                self.ui.ok("Tilia can open Messages")
                break
            self.ui.fail("Tilia's Python still can't open Messages, so add this path and switch it on:")
            self.ui.say(f"   {path}")
            self._ask_t("Press Enter to check again (or type skip): ")
        if not res.get("recent"):
            self.ui.fail("No texts from this week are on this Mac, so turn on Messages in iCloud "
                         "on your iPhone and here (Messages → Settings → iMessage).")

    def _pick_threads(self) -> list[dict]:
        self.screen("texts", [
            "Whose texts should Tilia read? Type one name at a time,",
            "the way they're saved in your Contacts. First name is fine.",
        ])
        chosen: list[dict] = []
        while True:
            name = self._ask_t("Name (or Enter when done): ").strip()
            if not name:
                return chosen
            res = self._probe(name)
            if not res.get("can_read") or res.get("error"):
                self.ui.fail("Messages or Contacts couldn't be read, so check Full Disk Access is on.")
                continue
            matches = [m for m in res.get("matches", [])
                       if m["chat_id"] not in {c["chat_id"] for c in chosen}]
            if not matches:
                self.ui.fail("No text thread matches that name, so try how it's saved in Contacts.")
                continue
            matches = matches[:9]
            for i, m in enumerate(matches, 1):
                self.ui.say(f"[{i}] {m['name']} ({m['handle']})")
            if len(matches) == 1:
                picks = [matches[0]] if self.yes_no("Add this thread?", self._ask_t) else []
            else:
                picks = self._pick_numbers(matches)
            for m in picks:
                chosen.append({"name": m["name"], "chat_id": m["chat_id"]})
                self.ui.ok(f"Added {m['name']}")

    def _pick_numbers(self, matches):
        while True:
            raw = self._ask_t("Type the numbers to add, like 1 2 (Enter = none): ")
            nums = re.findall(r"\d+", raw)
            if all(1 <= int(n) <= len(matches) for n in nums):
                return [matches[int(n) - 1] for n in dict.fromkeys(nums)]
            self.ui.fail(f"Please use numbers from 1 to {len(matches)}.")

    # 5b --------------------------------------------------------------------------------
    def style(self) -> bool:
        self.screen("style", [
            "Three quick questions, so Tilia talks to you the way you want.",
        ])
        cfg = config.load()
        had = bool(cfg.goal)  # a re-run: Enter keeps each answer
        if had:
            self.ui.say("Press Enter at any question to keep your current answer.")
        while True:
            goal = self.ui.ask("In six months, what would you like to be different? ")
            if goal.strip():
                break
            if had:
                goal = cfg.goal
                break
            self.ui.fail("A few words is plenty, so type whatever comes to mind.")
        tones = {"1": "very_direct", "2": "direct_soft", "3": "gentle"}
        refs = {"1": "never", "2": "gently"}
        tone_d = next((k for k, v in tones.items() if v == cfg.tone), None) if had else None
        ref_d = next((k for k, v in refs.items() if v == cfg.referrals), None) if had else None
        self.ui.say("")
        self.ui.say("How direct should Tilia be?")
        self.ui.say("[1] very direct")
        self.ui.say("[2] direct, softer when I'm struggling")
        self.ui.say("[3] gentle")
        tone = self.ui.choose("Choose 1, 2 or 3" + (f" (Enter = {tone_d})" if tone_d else "")
                              + ": ", tones, default=tone_d)
        self.ui.say("")
        self.ui.say("When things get really hard, should Tilia ever suggest talking to a person?")
        self.ui.say("[1] never")
        self.ui.say("[2] gently, if it seems needed")
        referrals = self.ui.choose("Choose 1 or 2" + (f" (Enter = {ref_d})" if ref_d else "")
                                   + ": ", refs, default=ref_d)
        cfg = config.load()
        cfg.goal, cfg.tone, cfg.referrals = goal, tone, referrals
        config.save(cfg)
        self.ui.ok("Got it")
        return True

    # 5c --------------------------------------------------------------------------------
    def bring_ai(self) -> bool:
        cfg = config.load()
        who = config.provider_name(cfg)
        self.screen("bring_ai", [
            f"{who} may already know you. Press Enter to copy the question below.",
            f"Paste it into {who}, which will give you a file called tilia-profile.md to",
            "download. Save it in the folder that opens, or leave it in Downloads.",
            "Type skip to leave this out.",
        ])
        for line in _wrap(IMPORT_PROMPT, 76):
            self.ui.say(line)
        self.ui.say("")
        try:
            self._ask_t("Press Enter to copy the question and open the folder (or type skip): ")
            folder = self.io.import_dir()
            shown = "~/" + folder.name if folder.parent == Path.home() else str(folder)
            if self.io.copy_clipboard(IMPORT_PROMPT):
                self.ui.ok("Copied to your clipboard")
            else:
                self.ui.fail("The clipboard didn't take it, so copy the question above by hand.")
            self.io.open_folder(folder)
            self.ui.say(f"Save the file in {shown} (that folder just opened), or leave it in Downloads.")
            while True:
                self._ask_t("Press Enter when the file is saved (or type skip): ")
                found = sorted(f for f in folder.iterdir()
                               if f.is_file() and f.suffix.lower() in IMPORT_SUFFIXES)
                in_downloads = False
                if not found:  # nothing in the Import folder: look for the one file we asked for
                    dl = self.io.downloads_dir()
                    cands = sorted(f for f in dl.iterdir()
                                   if f.is_file() and DOWNLOAD_NAME.fullmatch(f.name)
                                   ) if dl.is_dir() else []
                    if len(cands) > 1:
                        self.ui.fail(f"There are {len(cands)} tilia-profile files in Downloads, so "
                                     "keep just the one you want there (delete the others).")
                        continue
                    if cands:
                        found, in_downloads = cands, True
                if len(found) != 1:
                    self.ui.fail(f"I need exactly one .txt, .md or .rtf file in {shown}, and "
                                 f"there {'are ' + str(len(found)) if found else 'is none'}.")
                    continue
                f = found[0]
                size = f.stat().st_size
                if size > profile.MAX_IMPORT_BYTES:
                    self.ui.fail("That file is over 100 KB, so trim it to the notes that matter.")
                    continue
                text = (self.io.rtf_to_text(f) if f.suffix.lower() == ".rtf"
                        else f.read_text(encoding="utf-8", errors="replace"))
                if not text.strip():
                    self.ui.fail("That file is empty, so save the answer into it and try again.")
                    continue
                self.ui.say(f"{f.name} ({_kb(size)}), the first lines:")
                for line in text.strip().splitlines()[:IMPORT_PREVIEW_LINES]:
                    self.ui.say(f"  | {line[:74]}")
                self.ui.say("")
                if self.yes_no("Use this as your starting profile?", self._ask_t):
                    merged = bool(profile.read().strip())
                    try:
                        profile.import_text(text, merge=True)
                    except ValueError:
                        self.ui.fail("That file is over 100 KB, so trim it to the notes that matter.")
                        continue
                    _wipe(f)
                    try:
                        folder.rmdir()  # only if nothing else is in it
                    except OSError:
                        pass
                    self.ui.ok("Added to Tilia's notes on you" if merged
                               else "Tilia's notes on you start from this")
                    self.ui.ok("The file was overwritten and deleted"
                               + (", and the folder removed" if not folder.exists() else ""))
                else:
                    self.ui.say(f"Left alone: {'~/Downloads' if in_downloads else shown}/{f.name}")
                self.ui.say(IMPORT_AFTER)
                return True
        except Back:
            self.ui.ok("Skipped: Tilia starts from what you tell it")
            return True

    # 6 ---------------------------------------------------------------------------------
    def keep_running(self) -> bool:
        self.screen("keep_running", [
            "Tilia can only answer while your Mac is awake.",
            "",
            "Stay awake on battery while the lid is open? It uses a little more",
            "charge; the screen still turns off as usual.",
        ])
        awake = self.yes_no("Stay awake on battery?")
        cfg = config.load()
        cfg.battery_awake = awake
        config.save(cfg)
        while self.io.install() != 0:
            self.ui.fail("Tilia couldn't start in the background, so restart your Mac and run ./setup.")
            self.enter("to try again: ")
        self.restarted = True
        self.ui.ok("Tilia is running in the background")
        try:
            token = self.io.get_token()
        except RuntimeError:
            self.ui.fail("Your bot key is missing from Keychain, so run ./setup again to add it.")
            _unmark(self.state_path, "make_bot")
            return False
        turns = self.io.turn_count()
        if turns > 1:  # a re-run (./setup bot): she and Tilia are already talking
            self.ui.ok("Your conversation is kept. Message your bot any time.")
            return True
        while not self.io.telegram(token).send(cfg.chat_id, FIRST_MESSAGE):
            self.ui.fail("Telegram didn't take the message, so check your internet connection.")
            self.enter("to try again: ")
        if turns == 0:
            self.io.save_first_message(FIRST_MESSAGE)  # so the coach knows it said hello
        self.ui.ok("Tilia just sent you a message. Check your phone.")
        return True


def run(state_path, io, redo: str | None = None) -> int:
    """Run (or resume) setup. Returns an exit code; walking away is never an error.
    redo (a REDO key) un-marks those steps first, so they run again; the others are kept.
    Afterwards a running agent is restarted so it picks up the change."""
    w = Wizard(io, state_path)
    steps = REDO[redo] if redo else ()
    w.redo = steps
    # A re-run stops the running agent first, so it never races setup (pairing polls the
    # same bot) or reads half-changed settings. It is started again at the end, always.
    stopped = bool(steps) and io.agent_installed()
    if stopped:
        io.stop_agent()
    for step in steps:
        _unmark(state_path, step)
    try:
        return _run_steps(w, state_path, io, steps, stopped)
    finally:
        if stopped and not w.restarted and io.install() != 0:
            w.ui.fail("Tilia couldn't restart, so restart your Mac to start it again.")


def _run_steps(w, state_path, io, steps, stopped) -> int:
    try:
        for step in STEPS:
            if step in _load_done(state_path):
                continue
            if step == "make_bot":
                _unmark(state_path, "pair_phone")  # a new bot needs the phone paired again
            if not getattr(w, step)():
                return 1
            _mark_done(state_path, step)
        if stopped and "keep_running" not in steps:
            w.restarted = True
            if io.install() == 0:
                w.ui.ok("Tilia restarted with your changes")
            else:
                w.ui.fail("Tilia couldn't restart, so restart your Mac to pick up the change.")
        io.clear()
        io.print("")
        io.print("  ✓ All set. Tilia is yours now; talk to it on Telegram any time.")
        io.print("")
        io.print("  To change something later, run one of these in this folder:")
        for line in REDO_HELP:
            io.print(line)
        io.print("")
        return 0
    except (EOFError, KeyboardInterrupt):
        io.print("")
        io.print("  Paused. Run ./setup later and you'll be right here.")
        return 0
    except Exception as e:  # never a traceback wall; progress is already saved
        io.print("")
        w.ui.fail(f"Something unexpected went wrong ({type(e).__name__}), so run ./setup "
                  "again and you'll be right here.")
        return 1


# ---- the real io -----------------------------------------------------------------------

class RealIO:
    def __init__(self):
        import subprocess
        import time
        self._sp, self._time = subprocess, time
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def input(self, prompt=""):
        return input(prompt)

    def secret_input(self, prompt=""):
        # No echo: the bot key never lands on screen or in Terminal's scrollback.
        return getpass.getpass(prompt)

    def print(self, s=""):
        print(s, flush=True)

    def clear(self):
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="", flush=True)

    def run(self, cmd, **kw):
        return self._sp.run(cmd, **kw)

    def open_url(self, url):
        return self._sp.run(["open", url], capture_output=True).returncode == 0

    def now(self):
        return self._time.monotonic()

    def sleep(self, s):
        self._time.sleep(s)

    def telegram(self, token):
        from .telegram import Telegram
        return Telegram(token)

    def set_token(self, token):
        from . import keychain
        keychain.set_token(token)

    def get_token(self):
        from . import keychain
        return keychain.get_token()

    def ai_check(self, cfg):
        """One tiny call through the real runner for cfg's provider (all hardening applies)."""
        from . import ai
        return ai.run_model(cfg, "Reply with just: OK", "Reply with just OK.", timeout=60)

    def codex_installed(self):
        return paths.codex_bin().is_file()

    def install_codex(self):
        return codex_install.install(self.run)

    def codex_email(self):
        return codex.login_email()

    def codex_sessions(self):
        d = paths.codex_home() / "sessions"
        return [str(p) for p in d.rglob("*") if p.is_file()] if d.exists() else []

    def import_dir(self):
        return paths.import_dir()

    def downloads_dir(self):
        return Path.home() / "Downloads"

    def copy_clipboard(self, text):
        return self._sp.run(["pbcopy"], input=text.encode("utf-8")).returncode == 0

    def open_folder(self, folder):
        return self._sp.run(["open", str(folder)], capture_output=True).returncode == 0

    def rtf_to_text(self, path):
        r = self._sp.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                         capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ""

    def probe(self, name=None):
        """Run `python -m tilia probe-texts` as a one-shot launchd job, so Full Disk Access
        is judged for Tilia's own Python (as the agent will be), never for Terminal."""
        out = paths.data_dir() / "probe.json"
        query = paths.data_dir() / "probe_query.txt"
        py = str(paths.REPO_ROOT / ".venv" / "bin" / "python")
        cmd = ["launchctl", "submit", "-l", PROBE_LABEL, "--", py, "-m", "tilia", "probe-texts",
               "--out", str(out)]
        self.run(["launchctl", "remove", PROBE_LABEL], capture_output=True)
        out.unlink(missing_ok=True)
        query.unlink(missing_ok=True)
        if name:
            # The name goes in a private file, never in launchctl's argv (which `ps` and
            # `launchctl list` show to anyone on this Mac).
            fd = os.open(query, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(name)
            cmd += ["--name-file", str(query)]
        try:
            if self.run(cmd, capture_output=True).returncode != 0:
                return None
            deadline = self.now() + PROBE_TIMEOUT_S
            while self.now() < deadline:
                try:
                    return json.loads(out.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self.sleep(0.5)
            return None
        finally:
            # submit jobs restart when they exit; remove it now that we have the answer.
            self.run(["launchctl", "remove", PROBE_LABEL], capture_output=True)
            out.unlink(missing_ok=True)
            query.unlink(missing_ok=True)

    def python_path(self):
        return os.path.realpath(sys.executable)

    def install(self):
        return self._sp.run([str(paths.REPO_ROOT / "scripts" / "install.sh")]).returncode

    def save_offset(self, n):
        from .store import Store
        Store(paths.db_path()).set_meta("tg_offset", str(n))

    def save_first_message(self, text):
        from .store import Store
        Store(paths.db_path()).add_turn("coach", text, "ok")

    def agent_installed(self):
        return AGENT_PLIST.exists()

    def stop_agent(self):
        """Stop the agent like scripts/uninstall.sh, but keep its plist (setup restarts it)."""
        self.run(["launchctl", "bootout", f"gui/{os.getuid()}/{AGENT_LABEL}"],
                 capture_output=True)

    def username(self):
        return getpass.getuser()

    def turn_count(self):
        from .store import Store
        return Store(paths.db_path()).stats()["turns"]


def probe_texts(out: Path, name: str | None = None) -> None:
    """Write {"can_read", "recent", "matches"[, "error"]} to `out`. Run by launchd (see
    RealIO.probe). No message content: a count, and names/chat ids/handles of matches only."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    res: dict = {"can_read": imessage.can_read(imessage.CHAT_DB), "recent": 0, "matches": []}
    if res["can_read"]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        ns = int((cutoff - imessage.APPLE_EPOCH).total_seconds() * 1e9)
        try:
            conn = imessage.open_ro(imessage.CHAT_DB)
            try:
                res["recent"] = conn.execute(
                    "SELECT COUNT(*) FROM message WHERE date > ?", (ns,)).fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error:
            pass
        if name:
            try:
                res["matches"] = [{"name": m["name"], "chat_id": m["chat_id"], "handle": m["handle"]}
                                  for m in contacts.find_chats(name)]
            except Exception as e:
                res["error"] = type(e).__name__
    out = Path(out)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(res), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(out)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width, break_on_hyphens=False)


def _kb(size: int) -> str:
    return f"{size} bytes" if size < 1024 else f"{size / 1024:.0f} KB"


def _wipe(f: Path) -> None:
    """Overwrite the file's bytes with zeros, then delete it."""
    try:
        size = f.stat().st_size
        with open(f, "r+b") as fh:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    f.unlink(missing_ok=True)


def audit_prompt(privacy_text: str) -> str:
    """The audit prompt as PRIVACY.md words it, else the built-in copy.
    Prefers text between <!-- audit-prompt --> markers; otherwise the sentence run from
    "Read this entire project" to "every place data is written."."""
    text = privacy_text or ""
    m = re.search(r"<!--\s*audit-prompt\s*-->(.*?)<!--\s*/audit-prompt\s*-->", text, re.S)
    if m:
        found = m.group(1)
    else:
        m = re.search(r"Read this entire project\..*?every place data is written\.", text, re.S)
        found = m.group(0) if m else ""
    found = " ".join(line.strip().lstrip(">").strip() for line in found.splitlines())
    found = " ".join(found.split()).strip(' *"_')
    return found or _AUDIT_FALLBACK


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    os.umask(0o077)  # files setup makes (config, state, the database) are private
    if argv[:1] == ["audit-prompt"]:
        src = argv[1] if len(argv) > 1 else None
        text = Path(src).read_text(encoding="utf-8") if src else sys.stdin.read()
        print(audit_prompt(text))
        return 0
    redo = argv[0] if argv else None
    if redo is not None and redo not in REDO:
        print("Run ./setup to set up or resume, or one of these to change something:")
        for line in REDO_HELP:
            print(line)
        return 2
    # The Claude installer puts `claude` in ~/.local/bin and Homebrew puts `codex` in
    # /opt/homebrew/bin; neither may be on PATH in this window yet.
    for extra in ("/opt/homebrew/bin", str(Path.home() / ".local" / "bin")):
        if extra not in os.environ.get("PATH", "").split(":"):
            os.environ["PATH"] = f"{extra}:{os.environ.get('PATH', '')}"
    return run(paths.setup_state_path(), RealIO(), redo)


if __name__ == "__main__":
    raise SystemExit(main())
