# Tilia

Tilia is a private coach you text on Telegram. It runs on your own Mac, uses
your own ChatGPT or Claude login, and keeps what you tell it in a folder only
you can open. It helps you work on a goal in your own words: it remembers what
you said, follows up on what you agreed to do, checks in only when it has
something specific to say, and can help you rehearse a hard conversation.
If you choose, it can also read the iMessage threads you pick, so it knows
what was actually said.

Tilia is a coach, not a licensed clinician.

## The privacy promise

- Your data stays on your Mac. While it runs, your messages go only to `api.telegram.org` and your own local AI command (`claude` or `codex`). (`./setup` and `./update` download tools and new versions, and send none of your data.)
- Nobody else can read it, including the author, as long as the bot is made from your own Telegram, the ChatGPT or Claude login is your own, and nobody else has an administrator account on your Mac. PRIVACY.md explains each. There is no analytics, no telemetry and no shared server.
- Updates never happen on their own. You see which files would change, get the audit prompt, and type `yes`.
- You can check all of this yourself, with your own AI, using the prompt in [PRIVACY.md](PRIVACY.md).

Telegram stores bot chats on its servers, and they are not end-to-end
encrypted. Your AI provider, OpenAI (ChatGPT) or Anthropic (Claude), processes
your messages under your own account. [PRIVACY.md](PRIVACY.md) says exactly
what that means, and how Codex (the ChatGPT program) is kept from saving or
doing anything.

## Setup

```
git clone https://github.com/mydude-industries/tilia ~/tilia
cd ~/tilia
./setup
```

Setup goes one step at a time and saves its place. Run it again to resume.

To change something after setup is done, run one of these in the same folder:

- `./setup texts`: which text threads Tilia reads
- `./setup style`: your goal, and how Tilia talks to you
- `./setup ai`: ChatGPT or Claude, and which model
- `./setup import`: bring what your AI already knows about you (optional; the file you give it is deleted after)
- `./setup bot`: a new Telegram bot or phone
- `./setup all`: the whole walk-through again

## What it costs: your own ChatGPT or Claude plan

Tilia uses your own plan. A lighter model is the default for either, so a $20
plan is usually enough. There is no other fee.

## More

- [PRIVACY.md](PRIVACY.md): what is stored, who can see it, how to delete it
- [SECURITY.md](SECURITY.md): how to report a problem privately
- [CONTRIBUTING.md](CONTRIBUTING.md): how to help, and what never to commit

MIT licensed. See [LICENSE](LICENSE).
