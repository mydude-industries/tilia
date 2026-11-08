"""The hardened `codex exec` runner (the ChatGPT provider). No real process is ever started."""
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tilia import codex, paths
from tilia.claude import LimitError, MissingCLI, NotLoggedIn

LIMIT_MSG = ("You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
             "visit https://chatgpt.com/settings/usage to purchase more credits or try again at "
             "3:45 PM.")
AUTH_MSG = ("Your access token could not be refreshed because your refresh token has expired. "
            "Please log out and sign in again.")


def events(*items):
    """A `codex exec --json` stdout: one JSON object per line."""
    return "\n".join(json.dumps(i) for i in items) + "\n"


def ok_events(text="OK"):
    return events({"type": "thread.started", "thread_id": "t1"}, {"type": "turn.started"},
                  {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                                      "text": text}},
                  {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})


VERSION_OUT = "codex-cli 0.167.0\n"


@pytest.fixture(autouse=True)
def _fresh_version_check(monkeypatch):
    monkeypatch.setattr(codex, "_checked", None)


def fake(stdout="", stderr="", rc=0, calls=None, during=None, version=VERSION_OUT):
    """A subprocess.run stand-in. `codex --version` answers `version`; exec calls (the ones
    `calls` records) answer stdout/stderr/rc."""
    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(stdout=version, stderr="", returncode=0 if version else 1)
        if calls is not None:
            calls.append((cmd, kw))
        if during is not None:
            during(cmd, kw)
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)
    return run


def _instructions_path(cmd) -> Path:
    i = cmd.index("-c", cmd.index("-m"))
    while not cmd[i + 1].startswith("model_instructions_file="):
        i = cmd.index("-c", i + 1)
    val = cmd[i + 1].split("=", 1)[1]
    assert val.startswith('"') and val.endswith('"')
    return Path(val[1:-1])


def test_ok_returns_the_agent_message():
    assert codex.run("reply OK", "gpt-6-luna", "sys", run=fake(ok_events("OK"))) == "OK"


def test_last_agent_message_wins_and_prose_is_kept_verbatim():
    out = events({"type": "item.completed", "item": {"type": "agent_message", "text": "thinking"}},
                 {"type": "item.completed", "item": {"type": "agent_message",
                                                     "text": '{"reply": "hi", "state": "ok"}'}},
                 {"type": "turn.completed", "usage": {}})
    assert codex.run("p", "m", "s", run=fake(out)) == '{"reply": "hi", "state": "ok"}'


def test_command_line_is_the_hardened_invocation():
    calls, seen = [], {}

    def during(cmd, kw):
        p = _instructions_path(cmd)
        seen["exists"] = p.exists()
        seen["mode"] = stat.S_IMODE(p.stat().st_mode)
        seen["text"] = p.read_text(encoding="utf-8")
        seen["parent_mode"] = stat.S_IMODE(p.parent.stat().st_mode)
    codex.run("the prompt", "gpt-6-luna", "SYSTEM TEXT", run=fake(ok_events(), calls=calls,
                                                                 during=during))
    cmd, kw = calls[0]
    assert cmd[0] == str(paths.data_dir() / "bin" / "codex") and cmd[1] == "exec"  # never PATH
    assert stat.S_IMODE((paths.data_dir() / "bin").stat().st_mode) == 0o700
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                 "--json"):
        assert flag in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert cmd[cmd.index("-m") + 1] == "gpt-6-luna"
    assert cmd[-1] == "-"  # the prompt comes on stdin, never in argv
    assert "-a" not in cmd and "--ask-for-approval" not in cmd  # exec has no such flag
    overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    for o in ("features.shell_tool=false", "features.view_image=false",
              "features.multi_agent=false", "features.apps=false", "features.memories=false",
              "features.remote_plugin=false", "features.plugins=false", "features.hooks=false",
              "features.shell_snapshot=false", 'web_search="disabled"', "project_doc_max_bytes=0",
              'history.persistence="none"', "memories.generate_memories=false",
              "memories.use_memories=false", "skills.bundled.enabled=false",
              "analytics.enabled=false", 'forced_login_method="chatgpt"'):
        assert o in overrides, o
    # the system text travels in a private file, never in argv (it holds the profile)
    assert all("SYSTEM TEXT" not in a for a in cmd)
    assert kw["input"] == "the prompt" and kw["timeout"] == 120
    data = paths.data_dir()
    p = _instructions_path(cmd)
    assert p.parent == data / "codex-instructions" and seen["parent_mode"] == 0o700
    assert seen == {"exists": True, "mode": 0o600, "text": "SYSTEM TEXT", "parent_mode": 0o700}
    assert not p.exists() and list(p.parent.iterdir()) == []  # gone right after the call
    cwd = data / "codex-cwd"
    assert kw["cwd"] == str(cwd) and cmd[cmd.index("-C") + 1] == str(cwd)
    assert stat.S_IMODE(cwd.stat().st_mode) == 0o700 and list(cwd.iterdir()) == []
    home = data / "codex-home"
    assert kw["env"]["CODEX_HOME"] == str(home)
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_env_drops_planted_openai_and_codex_variables(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-planted")
    monkeypatch.setenv("CODEX_API_KEY", "planted")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example")
    monkeypatch.setenv("CODEX_HOME", "/somewhere/else")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    calls = []
    codex.run("p", "m", "s", run=fake(ok_events(), calls=calls))
    env = calls[0][1]["env"]
    assert not any(k.startswith(("OPENAI_", "CODEX_")) for k in env if k != "CODEX_HOME")
    assert env["CODEX_HOME"] == str(paths.data_dir() / "codex-home")
    assert env["PATH"] == "/usr/bin:/bin"


def test_instructions_file_is_removed_even_when_the_call_fails():
    calls = []
    with pytest.raises(RuntimeError):
        codex.run("p", "m", "s", run=fake("", "boom", rc=1, calls=calls))
    assert len(calls) == 2  # retried once, like claude
    assert list((paths.data_dir() / "codex-instructions").iterdir()) == []


def test_instructions_file_is_removed_when_the_runner_itself_raises():
    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(stdout=VERSION_OUT, stderr="", returncode=0)
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(RuntimeError):
        codex.run("p", "m", "s", run=run)
    assert list((paths.data_dir() / "codex-instructions").iterdir()) == []


def test_usage_limit_from_turn_failed_is_limit_error_with_hint():
    calls = []
    out = events({"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
                 {"type": "turn.failed", "error": {"message": LIMIT_MSG}})
    with pytest.raises(LimitError) as e:
        codex.run("p", "m", "s", run=fake(out, rc=1, calls=calls))
    assert "3:45 PM" in e.value.reset_hint and e.value.reset_hint.startswith("try again at")
    assert len(calls) == 1  # never retried


@pytest.mark.parametrize("msg", [
    "Quota exceeded. Check your plan and billing details.",
    "rate limit exceeded: slow down",
    "To use Codex with your ChatGPT plan, upgrade to Plus: https://chatgpt.com/explore/plus.",
    "You’ve hit your usage limit for gpt-6-luna. Switch to another model now, or try again "
    "at Nov 8th, 2026 3:45 PM.",
])
def test_other_limit_wordings_are_limit_errors(msg):
    with pytest.raises(LimitError):
        codex.run("p", "m", "s", run=fake("", msg, rc=1))


def test_limit_hint_handles_the_dated_form():
    out = events({"type": "error", "message": "You’ve hit your usage limit. Try again at "
                                              "Nov 8th, 2026 3:45 PM."})
    with pytest.raises(LimitError) as e:
        codex.run("p", "m", "s", run=fake(out, rc=1))
    assert e.value.reset_hint == "try again at Nov 8th, 2026 3:45 PM"


@pytest.mark.parametrize("msg", [AUTH_MSG, "Not logged in", "api error 401: Unauthorized",
                                 "Please run `codex login`"])
def test_not_logged_in_is_distinct_and_not_retried(msg):
    calls = []
    out = events({"type": "error", "message": msg}, {"type": "turn.failed",
                                                     "error": {"message": msg}})
    with pytest.raises(NotLoggedIn):
        codex.run("p", "m", "s", run=fake(out, rc=1, calls=calls))
    assert len(calls) == 1


def test_missing_cli_raises_missing_cli():
    calls = []

    def run(cmd, **kw):
        calls.append(1)
        raise FileNotFoundError("codex")
    with pytest.raises(MissingCLI):
        codex.run("p", "m", "s", run=run)
    assert len(calls) == 1  # the version probe; nothing after it


def test_timeout_in_the_runner_is_wrapped():
    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(stdout=VERSION_OUT, stderr="", returncode=0)
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(RuntimeError, match="TimeoutExpired"):
        codex.run("p", "m", "s", run=run)


# ---- a closed list of tools, and a version pin behind it -------------------------------

FEATURES_SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "codex_features_0.167.0.txt"
# Anything in `codex features list` whose name says it can act: run, read, write, patch,
# browse, drive apps, call tools/MCP/plugins/skills, spawn agents, remember, or hook.
ACTING = re.compile(r"exec|shell|patch|repl|tool|browser|computer|code_mode|apps|mcp|plugin|"
                    r"skill|agent|memor|hook|search|image|goal|artifact|automation|sleep|"
                    r"workspace|mention|chronicle|message_board|environment|file|executor|"
                    r"guardian_conversation")
# Matched by the pattern, but not a capability (each reviewed against `codex features list`):
NOT_TOOLS = {
    "compaction_image_budget", "image_resize_notice", "unified_image_budget",  # size budgets
    "login_shell_package_path", "powershell_shell_version",                     # path/version lookups
    "mcp_oauth_refresh_coordination",                                           # token refresh timing
    "subagent_default_context_limits",                                          # a limit, not a tool
    "omit_skill_descriptions",                 # trims the prompt; skills are off anyway
    "skip_host_skill_discovery",               # inverted: true would skip discovery (off by default)
    "untrusted_as_tool_output", "output_token_replay",  # how content is labelled / replayed
    "prevent_idle_sleep",                      # keeps the Mac awake; no access to anything
}


def _snapshot():
    rows = [ln.split() for ln in FEATURES_SNAPSHOT.read_text().splitlines() if ln.strip()]
    return {r[0]: r[1] for r in rows}


def _snapshot_for(version):
    path = FEATURES_SNAPSHOT.parent / f"codex_features_{'.'.join(map(str, version))}.txt"
    rows = [ln.split() for ln in path.read_text().splitlines() if ln.strip()]
    return {r[0]: r[1] for r in rows}


@pytest.mark.parametrize("version", sorted(codex.SUPPORTED_VERSIONS))
def test_every_acting_feature_of_each_checked_version_is_disabled(version):
    feats = _snapshot_for(version)
    assert len(feats) > 150  # the real list, not a stub
    live = {n for n, stage in feats.items() if stage != "removed"}
    acting = {n for n in live if ACTING.search(n)} - NOT_TOOLS
    missing = acting - set(codex.DISABLED_FEATURES)
    assert missing == set(), f"features that can act but are not switched off: {sorted(missing)}"
    unknown = set(codex.DISABLED_FEATURES) - set(feats)
    assert unknown == set(), f"not a feature of codex {version}: {sorted(unknown)}"
    assert len(set(codex.DISABLED_FEATURES)) == len(codex.DISABLED_FEATURES)  # no duplicates


def test_the_allowlist_is_exactly_the_reviewed_snapshots():
    snaps = {tuple(int(x) for x in p.stem.split("_")[-1].split("."))
             for p in FEATURES_SNAPSHOT.parent.glob("codex_features_*.txt")}
    assert snaps == set(codex.SUPPORTED_VERSIONS) == {(0, 167, 0)}


def test_command_line_switches_off_every_disabled_feature():
    calls = []
    codex.run("p", "m", "s", run=fake(ok_events(), calls=calls))
    cmd = calls[0][0]
    overrides = {cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"}
    for f in codex.DISABLED_FEATURES:
        assert f"features.{f}=false" in overrides, f
    assert cmd[cmd.index("-s") + 1] == "read-only"  # the sandbox floor: no writes, no network


@pytest.mark.parametrize("out", ["codex-cli 0.167.0\n", "0.167.0", "codex-cli 0.167.0 (brew)"])
def test_exactly_a_checked_version_runs(out):
    calls = []
    assert codex.run("p", "m", "s", run=fake(ok_events(), calls=calls, version=out)) == "OK"
    assert len(calls) == 1


@pytest.mark.parametrize("out", ["codex-cli 0.168.0\n", "codex-cli 1.0.0\n", "codex-cli 0.167.1",
                                 "codex-cli 0.166.9\n", "codex-cli 0.100.0", "codex-cli 0.167"])
def test_any_other_version_older_or_newer_is_refused_before_any_call(out):
    calls = []
    with pytest.raises(codex.UnsupportedCLI, match="not a version Tilia has checked|cannot|can read"):
        codex.run("p", "m", "s", run=fake(ok_events(), calls=calls, version=out))
    assert calls == []
    d = paths.data_dir() / "codex-instructions"
    assert not d.exists() or list(d.iterdir()) == []


def test_unreadable_version_is_refused():
    with pytest.raises(codex.UnsupportedCLI):
        codex.run("p", "m", "s", run=fake(ok_events(), version="codex-cli dev"))
    with pytest.raises(codex.UnsupportedCLI):
        codex.run("p", "m", "s", run=fake(ok_events(), version=""))  # rc 1


def test_version_is_cached_per_binary_and_failures_are_not_cached(monkeypatch):
    probes, ident = [], {"v": ("/opt/homebrew/bin/codex", 1000, 5000)}
    monkeypatch.setattr(codex, "_identity", lambda: ident["v"])

    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            probes.append(1)
            return SimpleNamespace(stdout=outs.pop(0), stderr="", returncode=0)
        return SimpleNamespace(stdout=ok_events(), stderr="", returncode=0)
    outs = ["codex-cli 9.0.0", "codex-cli 0.167.0", "codex-cli 0.168.0"]
    with pytest.raises(codex.UnsupportedCLI):
        codex.run("p", "m", "s", run=run)   # refused, and not cached
    codex.run("p", "m", "s", run=run)       # re-checked: fine
    codex.run("p", "m", "s", run=run)       # same binary: cached, no probe
    assert probes == [1, 1]
    ident["v"] = ("/opt/homebrew/bin/codex", 2000, 5100)  # Codex upgraded mid-run (new mtime)
    with pytest.raises(codex.UnsupportedCLI):
        codex.run("p", "m", "s", run=run)   # re-checked, now 0.168.0: refused
    assert probes == [1, 1, 1]


def test_unknown_binary_identity_means_a_probe_every_call(monkeypatch):
    monkeypatch.setattr(codex, "_identity", lambda: None)
    probes = []

    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            probes.append(1)
            return SimpleNamespace(stdout=VERSION_OUT, stderr="", returncode=0)
        return SimpleNamespace(stdout=ok_events(), stderr="", returncode=0)
    codex.run("p", "m", "s", run=run)
    codex.run("p", "m", "s", run=run)
    assert probes == [1, 1]


def test_identity_is_tilias_binary_its_mtime_and_size(tmp_path):
    assert codex._identity() is None  # not installed yet
    real = paths.codex_bin_dir() / "codex-real"
    real.write_bytes(b"x" * 10)
    paths.codex_bin().symlink_to(real)
    ident = codex._identity()
    assert ident[0] == str(real.resolve()) and ident[2] == 10
    real.write_bytes(b"y" * 11)
    assert codex._identity()[2] == 11 and codex._identity() != ident


def test_version_probe_and_exec_run_the_same_pinned_binary():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd[0])
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(stdout=VERSION_OUT, stderr="", returncode=0)
        return SimpleNamespace(stdout=ok_events(), stderr="", returncode=0)
    codex.run("p", "m", "s", run=run)
    assert seen == [str(paths.codex_bin())] * 2


def test_background_server_is_switched_off():
    calls = []
    codex.run("p", "m", "s", run=fake(ok_events(), calls=calls))
    cmd = calls[0][0]
    assert "features.daemon_auto_start=false" in {cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"}


def test_leftover_instruction_files_are_swept_before_a_call():
    d = paths.codex_instructions()
    (d / "system-stale.md").write_text("an old profile")
    (d / "junk").write_text("x")
    seen = {}

    def during(cmd, kw):
        seen["files"] = sorted(p.name for p in d.iterdir())
    codex.run("p", "m", "s", run=fake(ok_events(), during=during))
    assert len(seen["files"]) == 1 and seen["files"][0].startswith("system-")  # only this call's
    assert list(d.iterdir()) == []


def _jwt(claims: dict) -> str:
    import base64
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"{b({'alg': 'RS256'})}.{b(claims)}.signature"


def test_login_email_reads_only_the_id_token_claims():
    home = paths.codex_home()
    (home / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {"id_token": _jwt({"email": "maya@example.com", "sub": "u1"}),
                   "access_token": "SECRET-ACCESS", "refresh_token": "SECRET-REFRESH"}}))
    assert codex.login_email() == "maya@example.com"
    (home / "auth.json").write_text(json.dumps({"tokens": {"id_token": _jwt(
        {"https://api.openai.com/profile": {"email": "p@example.com"}})}}))
    assert codex.login_email() == "p@example.com"
    for bad in ('{"tokens": {}}', "not json", json.dumps({"tokens": {"id_token": "x.y"}}),
                json.dumps({"tokens": {"id_token": _jwt({"email": 5})}})):
        (home / "auth.json").write_text(bad)
        assert codex.login_email() is None
    (home / "auth.json").unlink()
    assert codex.login_email() is None
