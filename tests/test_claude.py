import json
import subprocess
import stat
import tempfile
from types import SimpleNamespace

import pytest

from tilia.claude import (LimitError, MissingCLI, NotLoggedIn, ask_json,
                          parse_coach, run_claude)


def fake(stdout="", stderr="", rc=0, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)
    return run


def env(result, is_error=False, **extra):
    return json.dumps({"result": result, "is_error": is_error, **extra})


def test_ok_returns_result():
    assert run_claude("hi", "sonnet", "sys", run=fake(env("hello"))) == "hello"


def test_limit_error_raised_with_reset_hint():
    out = env("You've hit your usage limit. Your limit resets 3pm.", is_error=True)
    with pytest.raises(LimitError) as e:
        run_claude("hi", "sonnet", "sys", run=fake(out, rc=1))
    assert "3pm" in e.value.reset_hint


def test_limit_error_429_no_hint():
    out = env("slow down", is_error=True, api_error_status=429)
    with pytest.raises(LimitError) as e:
        run_claude("hi", "sonnet", "sys", run=fake(out, rc=1))
    assert e.value.reset_hint == ""


def test_not_logged_in_is_distinct_error():
    calls = []
    out = env("Please run /login", is_error=True)
    with pytest.raises(NotLoggedIn):
        run_claude("hi", "sonnet", "sys", run=fake(out, rc=1, calls=calls))
    assert len(calls) == 1  # not retried


def test_missing_cli_raises_missing_cli():
    calls = []

    def run(cmd, **kw):
        calls.append(1)
        raise FileNotFoundError("claude")
    with pytest.raises(MissingCLI):
        run_claude("hi", "sonnet", "sys", run=run)
    assert len(calls) == 1


def test_retry_once_on_runtime_error():
    calls = []
    with pytest.raises(RuntimeError):
        run_claude("hi", "sonnet", "sys", run=fake("boom", rc=1, calls=calls))
    assert len(calls) == 2


def test_timeout_becomes_runtime_error():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(RuntimeError):
        run_claude("hi", "sonnet", "sys", run=run)


def test_command_line_matches_constraint():
    calls = []
    run_claude("hi", "sonnet", "SYS", run=fake(env("x"), calls=calls))
    cmd, kw = calls[0]
    assert cmd[:2] == ["claude", "-p"]
    i = cmd.index("--tools")
    assert cmd[i + 1] == ""
    for f in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert f in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    # only project settings, from an empty private folder: ~/.claude/settings.json (env such as
    # ANTHROPIC_BASE_URL, apiKeyHelper, hooks) is never loaded
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    from tilia import paths
    cwd = paths.data_dir() / "claude-cwd"
    assert kw["cwd"] == str(cwd) and kw["cwd"] != tempfile.gettempdir()
    assert stat.S_IMODE(cwd.stat().st_mode) == 0o700 and list(cwd.iterdir()) == []
    assert kw["input"] == "hi"
    assert kw["timeout"] == 120


def test_parse_coach_fenced_json():
    t = 'Sure\n```json\n{"reply": "Hi there", "state": "struggling", ' \
        '"new_assignment": {"kind": "challenge", "text": "walk"}, ' \
        '"close_assignment": 3, "remember": "likes mornings"}\n```'
    c = parse_coach(t)
    assert c.reply == "Hi there" and c.state == "struggling"
    assert c.new_assignment == {"kind": "challenge", "text": "walk"}
    assert c.close_assignment == 3 and c.remember == "likes mornings"


def test_parse_coach_unknown_state_is_ok():
    assert parse_coach('{"reply": "x", "state": "weird"}').state == "ok"


def test_parse_coach_rejects_empty_reply():
    assert parse_coach('{"reply": "  "}') is None
    assert parse_coach('{"state": "ok"}') is None
    assert parse_coach("not json") is None


def test_parse_coach_sanitizes_optionals():
    c = parse_coach(json.dumps({"reply": "x", "new_assignment": {"kind": "bad", "text": "t"},
                                "close_assignment": "3", "remember": ""}))
    assert c.new_assignment is None and c.close_assignment is None and c.remember is None
    c = parse_coach(json.dumps({"reply": "x", "new_assignment": {"kind": "rehearsal", "text": ""}}))
    assert c.new_assignment is None


def test_ask_json_fenced_and_unparseable():
    assert ask_json("p", "m", "s", run=fake(env('```json\n{"a": 1}\n```'))) == {"a": 1}
    assert ask_json("p", "m", "s", run=fake(env("nope"))) is None
    assert ask_json("p", "m", "s", run=fake(env("[1]"))) is None


# ---- claude runs in a private, empty folder: nobody can plant project settings ----------

@pytest.mark.parametrize("plant", ["dir", "file", "symlink-cwd"])
def test_refuses_a_claude_cwd_with_planted_settings(plant, tmp_path):
    from tilia import paths
    cwd = paths.data_dir() / "claude-cwd"
    if plant == "symlink-cwd":
        (tmp_path / "elsewhere").mkdir()
        cwd.symlink_to(tmp_path / "elsewhere")
    else:
        cwd.mkdir(mode=0o700)
        if plant == "dir":
            (cwd / ".claude").mkdir()
            (cwd / ".claude" / "settings.json").write_text('{"env": {"ANTHROPIC_BASE_URL": "x"}}')
        else:
            (cwd / ".claude").write_text("x")
    calls = []
    with pytest.raises(RuntimeError, match="claude-cwd"):
        run_claude("hi", "sonnet", "SYS", run=fake(env("x"), calls=calls))
    assert calls == []


def test_claude_cwd_mode_is_tightened(tmp_path):
    from tilia import paths
    cwd = paths.data_dir() / "claude-cwd"
    cwd.mkdir(mode=0o755)
    cwd.chmod(0o755)
    assert paths.claude_cwd() == cwd
    assert stat.S_IMODE(cwd.stat().st_mode) == 0o700
