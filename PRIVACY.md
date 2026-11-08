# Privacy

This is what Tilia stores, who can see it, and how to check all of it yourself.
Some of it is uncomfortable. It is written down anyway.

## What it stores and where

Everything Tilia keeps is in one folder on your Mac:
`~/Library/Application Support/Tilia/`

- `tilia.db`: your conversation with Tilia, your assignments and check-ins, notes it keeps about you, and (if you turn on text reading) the text messages from the threads you chose.
- `profile.md`: what Tilia has learned about you, including your goal in your own words, and anything you brought over from your AI during setup.
- `profile.prev.md`: the version of `profile.md` from before its last update, kept as a backup.
- `config.toml`: your settings, such as which AI (ChatGPT or Claude) and model, quiet hours and which text threads are read.
- `crisis.log`: a short note of when a hard moment was flagged. It exists only if that happened.
- `agent.log`: what the background program was doing. It records short events and error names, not your messages.
- `setup_state.json`: which setup steps are done.
- `claude-cwd/` and `tmp/`: empty folders only your account can open. `claude` runs inside `claude-cwd/`, and the background program keeps its temporary files in `tmp/` instead of the shared `/tmp`. Tilia refuses to call `claude` if anything named `.claude` appears in `claude-cwd/`.
- `bin/codex`, `codex-cwd/`, `codex-home/` and `codex-instructions/`: only if you chose ChatGPT. `bin/codex` is Tilia's own copy of OpenAI's Codex program, the one version Tilia has checked, downloaded from OpenAI's release page and verified; Tilia never runs a `codex` from anywhere else. It runs inside the empty `codex-cwd/`; Tilia refuses to call it if anything at all appears there. `codex-home/` is Codex's private home for Tilia: your ChatGPT login (`auth.json`) and Codex's own caches and logs live there, never in `~/.codex`. `codex-instructions/` holds Tilia's instructions to the model for the few seconds of each call, in a file only your account can read, deleted right after (and swept before every call).
- `probe.json`: only for a few seconds during setup's texts step. It holds whether Messages can be read, a count of this week's messages, and the names and handles of the contacts that match the name you typed. Setup deletes it as soon as it has read it.
- `probe_query.txt`: only for those same few seconds. It holds the name you typed, so the name never appears in the list of running programs. Setup deletes it too.

One thing is outside that folder, and only during setup: `~/Tilia Import/`, the folder
setup opens if you choose "Bring what your AI already knows". You save your AI's answer
(a file called tilia-profile.md) there, or leave it in `~/Downloads`, where setup looks
for that exact name, saved after setup copied the question, and nowhere else. Setup only
reads a plain file of yours (never a link), shows where macOS says it was downloaded from,
and warns if that wasn't ChatGPT or Claude. After you say yes to the import, the same file
you previewed is overwritten with zeros and deleted (if it changed since, it is left alone),
and the folder is removed when it is empty. It is in your home folder, not Documents or
Desktop, so it never syncs to iCloud. If you say no, the file stays where it is.

The folder is private to your Mac user account, and Tilia creates its files
so only your account can open them. The code refuses to run if that folder is
inside the code folder, so your data cannot end up in the repo.

Backups copy it too. If you use Time Machine or another backup, that backup
holds a copy of this folder. Keep the backup encrypted, or exclude the folder.

Your Telegram bot key is not in that folder. It is in your Mac's Keychain,
under the name `tilia-telegram`. It is never written to a file or a log.

In Telegram, `/privacy` shows what is stored and how much. `/export` sends
you one file with your conversation, your journal entries, your assignments
and your profile. It leaves out the copied text messages, the check-ins,
the signals Tilia worked out from your texts, and your corrections. Those are
stored, but not in the export.

## Who can see it

**You.** It is your Mac and your files.

**Whoever made the bot.** Make the bot from the Telegram app on your own
phone. Whoever creates a bot can always get a new key for it. To check it is
yours, open @BotFather in your own Telegram and send `/mybots`: your bot must
be in that list. Also send `/setjoingroups`, pick your bot, and choose
Disable, so nobody can add it to a group.

**Telegram.** You talk to Tilia through a Telegram bot. Telegram stores bot
chats on its servers. They are not end-to-end encrypted. Telegram can read
what you send and what Tilia sends back. This is how any Telegram bot works,
and it cannot be fixed from inside Tilia. If that is not acceptable to you,
do not use Tilia. Deleting a message in Telegram is a separate thing from
deleting Tilia's copy. See "Deleting everything".

**Your AI provider: Anthropic for Claude, OpenAI for ChatGPT.** To write a
reply, Tilia runs that company's own program on your Mac (`claude` or
`codex`), signed in as you. Your messages, your profile, and recent texts
from your chosen threads are sent to that company for that. They are handled
under your own account and its terms. Tilia does not use any other account,
and it does not have a key of its own. Setup asks which one you want; `./setup ai`
changes it later.

If you chose Claude. Check this is your own Claude account: run `claude`, type /status, and confirm the email.
Tilia runs `claude` from an empty private folder (`claude-cwd`, see above) with `--setting-sources project`,
so it does not load `~/.claude/settings.json`. Settings there (such as `env`,
`apiKeyHelper` or hooks) could otherwise send your messages somewhere else.
The background program also sets `DISABLE_AUTOUPDATER=1`, so `claude` does not
update itself in the middle of a conversation. Update it yourself with
`claude update`. Claude Code makes its own network calls (its updates, signing
in) to Anthropic, separately from Tilia.

If you chose ChatGPT: Tilia uses OpenAI's Codex program, which is normally a
coding agent that can run commands and edit files. Tilia uses it only as a way
to ask ChatGPT a question, and switches the rest off:

- Check this is your own ChatGPT account: open chatgpt.com, click your profile picture, and confirm the email. Tilia's Codex login lives in `codex-home/` (see above), so it is separate from any Codex you use yourself.
- In ChatGPT, Settings → Data Controls: turn off "Improve the model for everyone" if you don't want your chats used for training. That is OpenAI's setting, not Tilia's.
- Nothing is saved by Codex: every call runs with `--ephemeral` (no session transcript), with its history, memories, analytics and background server turned off, and from Tilia's own `CODEX_HOME`, so the usual `~/.codex` folder is never read or written. After setup's "reply OK" call, setup looks inside `codex-home/sessions` and refuses to use ChatGPT if any file appeared there; Tilia's tests pin every flag.
- No tools: Codex is run read-only, in its non-interactive mode, which never asks for approval, and every one of its features that could run a command, read or change a file, browse, open apps, call plugins or skills, or start other agents is switched off by name, from the full feature list of the Codex version Tilia has checked. Tilia also refuses to run any Codex version other than the ones it has checked (0.167.0 today), newer or older, and tells you to run `./update`, rather than run one with features it hasn't seen; it re-checks whenever the program on disk changes. Underneath that, Codex's own read-only sandbox (macOS Seatbelt) forbids writes.
- No planted settings: `--ignore-user-config` and `--ignore-rules` skip every config and rules file, and Codex is told to read no `AGENTS.md`, anywhere. Anything someone puts in `~/.codex` is never loaded. The `OPENAI_*` and `CODEX_*` environment variables are removed before Codex starts, and it is told to accept only a ChatGPT login, never an API key.
- Setup installs Codex itself: it downloads the one reviewed version from OpenAI's GitHub release page (`github.com/openai/codex/releases/download/rust-v0.167.0/`), checks the file against a SHA-256 checksum recorded in Tilia's code, and unpacks it into `bin/`. Before Tilia runs it for the first time, setup also checks that the program is signed by OpenAI (Developer ID "OpenAI OpCo, LLC") and notarized by Apple (`codesign`, `spctl`). If any of that fails, Tilia does not run it and asks whether to try again later or use Claude; it never decides for you.
- Setup shows the e-mail of the ChatGPT account it logged in as, read from the login's own identity token on your Mac.
- Your messages never appear in the command line of a running program: the prompt goes in on standard input, and Tilia's instructions (which include your profile) go in a private file that exists only for the call.

Codex makes its own network calls (signing in, checking its model list) to
OpenAI, separately from Tilia.

**The person who set this up for you, including the author.** They cannot see
your data. While Tilia is running, your messages, profile and texts go
nowhere except `api.telegram.org` and your local AI command (`claude` or
`codex`). There is no analytics and no telemetry. Tilia on one Mac never
connects to Tilia on another.

Two other times Tilia's tools touch the internet. Neither sends your data:

- `./setup` downloads tools: the `uv` installer from astral.sh, Python packages from PyPI, and, if you don't have it, the `claude` program from claude.ai or the `codex` program from OpenAI's GitHub release page.
- `./update` downloads new versions of Tilia from GitHub.

**Anyone who shares your home or your Mac.** They cannot read your files if
FileVault is on, they do not have your login password, and nobody else has an
administrator account on that Mac. Another administrator on the same Mac can
read your files while the Mac is unlocked. Setup checks FileVault and helps you
turn it on, and names any other administrator accounts on this Mac. If someone knows your Mac login password, they can read everything
above. Keep it to yourself.

Two things this does not protect against: someone who can see your phone's
Telegram app, and anyone with your Mac unlocked in front of them.

## Reading your texts

This is optional. If you skip it, Tilia reads no texts.

If you turn it on:

- Tilia reads only the iMessage threads you pick, by contact name. No other thread is opened.
- It reads them read-only from the Messages database on your Mac. It never sends, edits or deletes a text.
- By default it goes back 12 months the first time (setup also offers all history). After that it adds new messages as they arrive.
- Recent messages from those threads go into what Tilia sends to your AI provider when it replies (see above).
- To do this, macOS needs Full Disk Access for Tilia's Python program. Setup walks you through it. Only that program gets it, not Terminal. Full Disk Access is given to that Python program, so anything run with it under your account can read Messages too.

**The people you text wrote those words too.** Their messages sit in Tilia's
database on your Mac, and recent ones are sent to your AI provider. Tell them
before you turn this on. It is their privacy as well as yours.

Tilia is told not to quote someone's texts back as evidence against them or
to help build a case. That is a rule in its instructions, not a lock.

## How to check this yourself

You do not have to take this document's word for it. This repo is public, and
the code is small.

First, make sure the copy is your own. It must be one you made yourself with
`git clone https://github.com/mydude-industries/tilia`. In the Tilia folder,
`git remote -v` must show that address, and `git status` must say there is
nothing to commit. If two people use one Mac, each needs a separate macOS user
account: Tilia's Keychain item and data folder have the same names for
everyone, so a shared account means shared data.

Then give your own AI this prompt, in this folder, on your own Mac:

<!-- audit-prompt -->
Read this entire project. Tell me plainly: does it send my data anywhere other than api.telegram.org and the local AI command (`claude` or `codex`)? Could anyone other than me read my conversations, profile or texts? Point to the exact files and lines for every network call and every place data is written.
<!-- /audit-prompt -->

Run it with your own Claude or ChatGPT, on your own Mac. Do not ask the author
or anyone else to run it for you.

You can also search it by hand. Every network call Tilia's own code makes while it runs goes through `tilia/net.py`; setup and update use curl/git as listed above.
A test fails if any other file imports `urllib`, `http`, `socket`, `ssl` or
`requests`. Another test, `tests/test_net.py`, scans every file in `tilia/`
and fails if the code mentions `curl`, `wget`, `nc`, `ssh` or `osascript`, or
any web address other than `api.telegram.org`, setup's links to BotFather
and Claude, and OpenAI's release page for the one Codex version. It allows
exactly two lines, named in that test: setup's command that runs Claude's
official installer, and the download of that Codex release. The `claude` and `codex` programs talk
to their own companies themselves; `tests/test_claude.py` and
`tests/test_codex.py` pin down every flag Tilia starts them with.

## Updates

Updates are never automatic. Nothing changes on your Mac until you run
`./update`. It then:

1. Asks the git remote your copy was cloned from (check it with `git remote -v`) for new tags. Tags aren't cryptographically verified; your audit before typing `yes` is the check. This is a download. It sends none of your data.
2. Shows which files would change.
3. Prints the audit prompt above, taken from the copy you already have, so a changed version cannot reword it.
4. Changes nothing unless you type `yes`.

It will not move you back to an older version.

## Deleting everything

1. In Telegram, send `/forget all` and then type `FORGET` when asked. This deletes every row in Tilia's database, and the deleted data is overwritten and the database file is compacted, so it is not left behind in the file. It also clears `profile.md`, deletes its backup copy `profile.prev.md`, and deletes `crisis.log`. If Tilia reads your texts, it then asks "Keep reading your texts from now on? y/n". Answer `n` and it stops reading them and forgets which threads you chose. Answer `y` and it keeps reading them, so it copies those threads again from scratch, going back as far as setup said (12 months by default).
2. Stop Tilia by running `scripts/uninstall.sh` in the Tilia folder. This only stops it and removes the small launch file that setup put in `~/Library/LaunchAgents/` (it holds paths, no personal data). It leaves your data alone. Then delete the data folder `~/Library/Application Support/Tilia/`. Deleting that folder is what removes your settings and logs, and, if you chose ChatGPT, the Codex login Tilia made (`codex-home/`).
3. In Telegram, open @BotFather, send `/deletebot`, and pick your bot. Then delete the chat. Telegram's own copy is theirs to keep or remove, which Tilia cannot control.
4. Remove the bot key from your Keychain:
   `security delete-generic-password -s tilia-telegram`

Smaller steps: `/forget last` removes the last exchange. `/forget day`
removes today's.

Anthropic or OpenAI keeps its own records under your account. Manage those in
your Claude or ChatGPT account settings.

## Troubleshooting note

If a Python upgrade changes where Tilia's Python lives, macOS may stop
letting it read your texts. Full Disk Access then needs to be granted again.
Run `./setup texts`. It checks and walks you through it. Tilia also tells you
in Telegram when it can't read your texts.

To change other settings later: `./setup style` (your goal and how Tilia
talks to you), `./setup ai` (ChatGPT or Claude, and which model), `./setup import`
(bring what your AI already knows about you), `./setup bot` (a new bot or
phone), or `./setup all` for the whole walk-through.
