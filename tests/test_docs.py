from pathlib import Path

from tilia import setup_wizard

ROOT = Path(__file__).resolve().parent.parent
PRIVACY = (ROOT / "PRIVACY.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_privacy_doc_has_audit_prompt_and_disclosures():
    assert setup_wizard._AUDIT_FALLBACK in " ".join(PRIVACY.split())
    for needle in ("api.telegram.org", "end-to-end", "Anthropic", "/forget all",
                   "tilia-telegram", "FileVault", "BotFather", "GitHub", "profile.prev.md"):
        assert needle in PRIVACY


def test_audit_prompt_extracted_from_real_privacy_md_is_the_builtin():
    assert setup_wizard.audit_prompt(PRIVACY) == setup_wizard._AUDIT_FALLBACK


def test_audit_markers_on_their_own_lines():
    lines = PRIVACY.splitlines()
    assert lines.count("<!-- audit-prompt -->") == 1
    assert lines.count("<!-- /audit-prompt -->") == 1


def test_readme_basics():
    for needle in ("git clone https://github.com/mydude-industries/tilia ~/tilia",
                   "./setup", "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md", "MIT",
                   "not a licensed clinician"):
        assert needle in README
    assert "substitute for" not in README.lower()


def test_docs_name_the_setup_reentry_commands():
    for cmd in ("./setup texts", "./setup style", "./setup ai", "./setup import", "./setup bot",
                "./setup all"):
        assert cmd in README
    assert "Run `./setup texts`." in PRIVACY
    assert "Run `./setup` again. It checks" not in PRIVACY


def test_privacy_is_plain_about_other_admins():
    flat = " ".join(PRIVACY.split())
    assert "and nobody else has an administrator account on that Mac" in flat
    assert ("Another administrator on the same Mac can read your files while the Mac is "
            "unlocked.") in flat


def test_privacy_lists_every_file_it_writes():
    stores = PRIVACY.split("## What it stores and where", 1)[1].split("##", 1)[0]
    for f in ("tilia.db", "profile.md", "profile.prev.md", "config.toml", "crisis.log",
              "agent.log", "setup_state.json", "probe.json", "probe_query.txt", "claude-cwd/",
              "codex-cwd/", "codex-home/", "codex-instructions/", "bin/codex", "tmp/"):
        assert f"`{f}`" in stores


# ---- privacy audit wording ----------------------------------------------------------

FLAT_P = " ".join(PRIVACY.split())
FLAT_R = " ".join(README.split())


def test_readme_promise_names_its_conditions():
    assert ("Nobody else can read it, including the author, as long as the bot is made from "
            "your own Telegram, the ChatGPT or Claude login is your own, and nobody else has an "
            "administrator account on your Mac. PRIVACY.md explains each.") in FLAT_R
    assert "You see which files would change, get the audit prompt, and type `yes`." in FLAT_R
    assert "You see what changed" not in FLAT_R


def test_privacy_bot_ownership_and_claude_account():
    who = " ".join(PRIVACY.split("## Who can see it", 1)[1].split("\n## ", 1)[0].split())
    assert ("Make the bot from the Telegram app on your own phone. Whoever creates a bot can "
            "always get a new key for it.") in who
    assert "/mybots" in who and "/setjoingroups" in who
    assert ("Check this is your own Claude account: run `claude`, type /status, and confirm "
            "the email.") in who
    assert "--setting-sources project" in who and "~/.claude/settings.json" in who
    assert "DISABLE_AUTOUPDATER" in who
    assert "Claude Code makes its own network calls" in who
    assert "Time Machine" in FLAT_P
    assert "names any other administrator accounts" in who


def test_privacy_how_to_check_covers_provenance_and_shared_macs():
    check = " ".join(PRIVACY.split("## How to check this yourself", 1)[1]
                     .split("\n## ", 1)[0].split())
    assert "git clone https://github.com/mydude-industries/tilia" in check
    assert "git remote -v" in check and "git status" in check
    assert "separate macOS user account" in check
    assert ("Every network call Tilia's own code makes while it runs goes through "
            "`tilia/net.py`; setup and update use curl/git as listed above.") in check
    assert "scans every file in `tilia/`" in check
    assert "exactly two lines" in check and "test_net.py" in check


def test_privacy_updates_and_disk_access_are_plain():
    assert ("Asks the git remote your copy was cloned from (check it with `git remote -v`) "
            "for new tags. Tags aren't cryptographically verified; your audit before typing "
            "`yes` is the check.") in FLAT_P
    assert ("Full Disk Access is given to that Python program, so anything run with it under "
            "your account can read Messages too.") in FLAT_P
    assert "`probe_query.txt`" in PRIVACY.split("## Who can see it", 1)[0]


def test_privacy_is_provider_aware():
    assert "the local AI command (`claude` or `codex`)" in setup_wizard._AUDIT_FALLBACK
    assert "the local `claude` command" not in setup_wizard._AUDIT_FALLBACK
    who = " ".join(PRIVACY.split("## Who can see it", 1)[1].split("\n## ", 1)[0].split())
    assert "OpenAI" in who and "Anthropic" in who
    assert "Check this is your own ChatGPT account" in who
    assert "Improve the model for everyone" in who
    assert "--ephemeral" in who and "CODEX_HOME" in who
    assert "signed by OpenAI" in who and "notarized" in who
    assert "SHA-256" in who and "rust-v0.167.0" in who and "never decides for you" in who
    assert "looks inside `codex-home/sessions`" in who
    assert "never asks for approval" in who and "told never" not in who
    assert "other than the ones it has checked (0.167.0 today), newer or older" in who
    stores = PRIVACY.split("## What it stores and where", 1)[1].split("##", 1)[0]
    assert "`~/Tilia Import/`" in stores and "deleted" in stores
    assert "OpenAI (ChatGPT)" in FLAT_R and "Anthropic (Claude)" in FLAT_R
