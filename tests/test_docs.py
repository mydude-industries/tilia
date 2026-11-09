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


def test_docs_say_tilia_texts_first_and_how_to_change_it():
    for doc in (README, PRIVACY):
        flat = " ".join(doc.split())
        assert "Tilia sometimes texts you first" in flat
        for needle in ("2 times a day", "22:00 to 09:00", "`/quieter`", "`/more`", "`/pause`",
                       "`quiet_start`", "`quiet_end`"):
            assert needle in flat


def test_version_is_0_2_2():
    import tomllib
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["version"] == "0.2.2"


# ---- worksheets, cards, blots and the mirror ------------------------------------------

def test_privacy_lists_the_mirror_files():
    stores = PRIVACY.split("## What it stores and where", 1)[1].split("\n## ", 1)[0]
    assert "`mirror.html`" in stores and "`~/Tilia Mirror.html`" in stores
    assert "`.metadata_never_index`" in stores and "Spotlight" in stores
    assert "Time Machine" in stores and "mirror.html" in stores.split("Time Machine", 1)[1][:400]


def test_privacy_says_what_github_telegram_and_the_fragment_see():
    assert "GitHub sees that a page was opened" in FLAT_P
    assert "never your answers or your wording" in FLAT_P
    assert "CloudStorage" in FLAT_P and "fragment" in FLAT_P
    assert "Tilia Tarot" in FLAT_P  # the public deck


def test_privacy_says_where_an_unsent_draft_stays():
    who = " ".join(PRIVACY.split("## Who can see it", 1)[1].split("\n## ", 1)[0].split())
    assert ("A draft you never send stays in Telegram's CloudStorage for your bot until a "
            "later worksheet page clears it: opening any worksheet, card or blot deletes "
            "drafts older than 7 days.") in who
    assert "`/forget` and `scripts/uninstall.sh` can't reach Telegram's storage" in who
    assert "the entry's random id" in who.split("GitHub sees that a page was opened", 1)[1][:300]
    delete = " ".join(PRIVACY.split("## Deleting everything", 1)[1].split("\n## ", 1)[0].split())
    assert "unsent worksheet draft still in Telegram's CloudStorage" in delete


def test_privacy_forget_and_export_cover_entries_and_the_mirror():
    delete = " ".join(PRIVACY.split("## Deleting everything", 1)[1].split("\n## ", 1)[0].split())
    assert "also deletes your worksheets, cards and blots and rewrites `mirror.html`" in delete
    assert "`~/Tilia Mirror.html`" in delete  # uninstall removes the alias
    assert "not in the export" in FLAT_P and "the mirror shows them" in FLAT_P


def test_readme_names_the_new_commands_and_the_page():
    for needle in ("/mirror", "/worksheets off", "/cards off", "## Worksheets, cards and blots"):
        assert needle in README
    assert ("The page is served by GitHub to your phone; your Mac still talks only to "
            "Telegram and your AI.") in FLAT_R
