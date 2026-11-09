# Contributing

Tilia's value is provable privacy. Please keep it that way.

- Use fake people only in tests, fixtures, and docs. Phone numbers must be `+1555010xxxx`.
- Never commit data: databases, logs, profiles, tokens, or real conversations.
- Run `scripts/dev-setup` first. It installs a pre-commit hook that refuses personal data.
- Run `uv run pytest -q` before opening a pull request.

## Releasing the worksheet page (maintainers)

The page is published by `.github/workflows/pages.yml` to
`https://mydude-industries.github.io/tilia/<tag>/`. One-time setup on the public repo,
before the first release tag:

1. Settings → Pages → Source: GitHub Actions.
2. Settings → Environments → `github-pages` → Deployment branches and tags: add a tag rule
   `v*`, and a branch rule `preview` (the branch used for device checks). By default only
   the default branch may deploy, so a tag push would be refused.
3. The workflow must be on the default branch (`main`) before `workflow_dispatch` can run
   it. To preview a branch, run the workflow from `main` with `preview_ref=<branch>`; it
   appears at `/preview/` until the next deploy.

Then each release: bump `version` in `pyproject.toml`, and push the tag `v<version>`. The
workflow refuses a tag that doesn't match the version, and every deploy rebuilds every
`v*` tag, so older pages stay up.
