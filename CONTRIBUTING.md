# Contributing

Tilia's value is provable privacy. Please keep it that way.

- Use fake people only in tests, fixtures, and docs. Phone numbers must be `+1555010xxxx`.
- Never commit data: databases, logs, profiles, tokens, or real conversations.
- Run `scripts/dev-setup` first. It installs a pre-commit hook that refuses personal data.
- Run `uv run pytest -q` before opening a pull request.
