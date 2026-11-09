"""The GitHub Pages workflow: every v* tag's webapp/ at /<tag>/, rebuilt on every deploy."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "pages.yml"


def _text() -> str:
    return WF.read_text(encoding="utf-8")


def _step_run(name: str) -> str:
    """The `run: |` block of the step called `name` (plain text parsing, no YAML lib)."""
    lines = _text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip() == f"- name: {name}")
    j = next(n for n in range(i + 1, len(lines)) if lines[n].strip() == "run: |")
    indent = len(lines[j]) - len(lines[j].lstrip()) + 2
    out = []
    for l in lines[j + 1:]:
        if l.strip() and len(l) - len(l.lstrip()) < indent:
            break
        out.append(l[indent:])
    return "\n".join(out) + "\n"


def test_workflow_shape():
    t = _text()
    for needle in ("tags:", "'v*'", "workflow_dispatch", "preview_ref", "git archive",
                   "actions/checkout@v4", "fetch-depth: 0", "upload-pages-artifact@v3",
                   "deploy-pages@v4", "pages: write", "id-token: write", "contents: read",
                   "group: pages", "_site/.nojekyll", "path: _site"):
        assert needle in t, needle
    assert "rm -rf _site/v" not in t
    # the tag must equal "v" + pyproject's version
    assert "GITHUB_REF_NAME" in t and "pyproject.toml" in t
    # the dispatch input reaches the script only through env, never pasted into it
    assert "${{ inputs.preview_ref }}" not in _step_run("Build _site")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})


def _commit(repo, version, page):
    (repo / "pyproject.toml").write_text(f'[project]\nname = "tilia"\nversion = "{version}"\n')
    if page is not None:
        (repo / "webapp").mkdir(exist_ok=True)
        (repo / "webapp" / "index.html").write_text(page)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", version)


def _bash(script, cwd, **env):
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=30)


@pytest.mark.real_subprocess  # bash + git in a temp repo
def test_build_keeps_every_tag_and_preview(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "0.1.4", None)            # before webapp/ existed: no path for it
    _git(repo, "tag", "v0.1.4")
    _commit(repo, "0.2.0", "page two")
    _git(repo, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
    _commit(repo, "0.2.1", "page three")
    _git(repo, "tag", "v0.2.1")
    _commit(repo, "0.2.1", "work in progress")
    _git(repo, "branch", "preview-me")

    r = _bash(_step_run("Build _site"), repo, PREVIEW_REF="")
    assert r.returncode == 0, r.stderr
    site = repo / "_site"
    assert (site / "v0.2.0" / "index.html").read_text() == "page two"
    assert (site / "v0.2.1" / "index.html").read_text() == "page three"
    assert not (site / "v0.1.4").exists()
    assert not (site / "preview").exists()
    assert (site / ".nojekyll").is_file()

    shutil.rmtree(site)  # a runner starts clean
    r = _bash(_step_run("Build _site"), repo, PREVIEW_REF="preview-me")
    assert r.returncode == 0, r.stderr
    assert (site / "preview" / "index.html").read_text() == "work in progress"
    assert (site / "v0.2.0" / "index.html").read_text() == "page two"

    shutil.rmtree(site)
    r = _bash(_step_run("Build _site"), repo, PREVIEW_REF="no-such-ref")
    assert r.returncode != 0


@pytest.mark.real_subprocess  # bash in a temp folder
def test_tag_must_match_the_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "tilia"\nversion = "0.2.0"\n')
    check = _step_run("Check the tag matches pyproject.toml")
    assert _bash(check, tmp_path, GITHUB_REF_NAME="v0.2.0").returncode == 0
    bad = _bash(check, tmp_path, GITHUB_REF_NAME="v0.2.1")
    assert bad.returncode != 0 and "v0.2.0" in bad.stdout + bad.stderr


def test_contributing_names_the_one_time_pages_setup():
    flat = " ".join((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())
    for needle in ("github-pages", "Deployment branches and tags", "`v*`", "`preview`",
                   "default branch", "workflow_dispatch"):
        assert needle in flat, needle
