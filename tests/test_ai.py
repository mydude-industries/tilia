"""One door to the model: ai.run_model / ai.ask_json route by cfg.provider."""
import json
from types import SimpleNamespace

import pytest

from tilia import ai, claude, codex, config
from tilia.config import Config


def claude_env(result):
    return json.dumps({"result": result, "is_error": False})


def codex_events(text):
    return json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": text}}) + "\n"


@pytest.fixture(autouse=True)
def _fresh_version_check(monkeypatch):
    monkeypatch.setattr(codex, "_checked", None)


def recorder(calls):
    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(stdout="codex-cli 0.167.0", stderr="", returncode=0)
        calls.append((cmd, kw))
        if cmd[0] == "claude":
            return SimpleNamespace(stdout=claude_env('{"a": 1}'), stderr="", returncode=0)
        return SimpleNamespace(stdout=codex_events('{"a": 2}'), stderr="", returncode=0)
    return run


def test_default_config_is_claude_sonnet_and_chatgpt_luna():
    cfg = Config()
    assert (cfg.provider, cfg.model, cfg.chatgpt_model) == ("claude", "sonnet", "gpt-6-luna")
    assert config.active_model(cfg) == "sonnet"
    assert config.active_model(Config(provider="chatgpt")) == "gpt-6-luna"
    assert config.active_model(Config(provider="chatgpt", chatgpt_model="gpt-6.1-sol")) == \
        "gpt-6.1-sol"
    assert config.provider_name(Config()) == "Claude"
    assert config.provider_name(Config(provider="chatgpt")) == "ChatGPT"


def test_config_round_trips_provider_and_both_models(home):
    cfg = Config(provider="chatgpt", model="opus", chatgpt_model="gpt-6.1-sol")
    config.save(cfg)
    back = config.load()
    assert (back.provider, back.model, back.chatgpt_model) == ("chatgpt", "opus", "gpt-6.1-sol")


def test_run_model_routes_to_claude_by_default():
    calls = []
    out = ai.run_model(Config(model="opus"), "prompt", "system", run=recorder(calls))
    assert out == '{"a": 1}'
    cmd, kw = calls[0]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "opus"
    assert kw["input"] == "prompt" and cmd[cmd.index("--system-prompt") + 1] == "system"


def test_run_model_routes_to_codex_for_chatgpt():
    calls = []
    cfg = Config(provider="chatgpt", chatgpt_model="gpt-6.1-sol")
    out = ai.run_model(cfg, "prompt", "system", run=recorder(calls))
    assert out == '{"a": 2}'
    cmd, kw = calls[0]
    assert cmd[0].endswith("/bin/codex") and cmd[1] == "exec"
    assert cmd[cmd.index("-m") + 1] == "gpt-6.1-sol"
    assert kw["input"] == "prompt"


def test_ask_json_routes_and_parses():
    calls = []
    assert ai.ask_json(Config(), "p", "s", run=recorder(calls)) == {"a": 1}
    assert ai.ask_json(Config(provider="chatgpt"), "p", "s", run=recorder(calls)) == {"a": 2}
    assert [c[0][0].rsplit("/", 1)[-1] for c in calls] == ["claude", "codex"]


def test_unknown_provider_falls_back_to_claude():
    calls = []
    ai.run_model(Config(provider="something"), "p", "s", run=recorder(calls))
    assert calls[0][0][0] == "claude"


def test_codex_module_has_no_install_or_download_code():
    """Installing is setup's job (codex_install.py); the runner never fetches anything."""
    import inspect
    src = inspect.getsource(codex)
    assert "curl" not in src and "http" not in src


def test_timeout_is_passed_through_to_either_runner():
    calls = []
    ai.run_model(Config(), "p", "s", run=recorder(calls), timeout=60)
    ai.run_model(Config(provider="chatgpt"), "p", "s", run=recorder(calls), timeout=60)
    assert [c[1]["timeout"] for c in calls] == [60, 60]


def test_error_types_are_shared():
    assert ai.LimitError is claude.LimitError and ai.NotLoggedIn is claude.NotLoggedIn
    assert ai.MissingCLI is claude.MissingCLI and ai.UnsupportedCLI is claude.UnsupportedCLI
    assert codex.LimitError is claude.LimitError


@pytest.mark.parametrize("provider", ["claude", "chatgpt"])
def test_coach_reply_and_summary_go_through_the_router(provider, home):
    from datetime import datetime
    from tilia import coach, paths
    from tilia.store import Store
    store = Store(paths.db_path())
    cfg = Config(provider=provider, chat_id=1, goal="g")
    calls = []

    def run(cmd, **kw):
        if cmd[1:2] == ["--version"]:
            return SimpleNamespace(stdout="codex-cli 0.167.0", stderr="", returncode=0)
        calls.append("codex" if cmd[0].endswith("/codex") else cmd[0])
        body = '{"reply": "hi", "state": "ok"}'
        if cmd[0] == "claude":
            return SimpleNamespace(stdout=claude_env(body), stderr="", returncode=0)
        return SimpleNamespace(stdout=codex_events(body), stderr="", returncode=0)
    now = datetime(2026, 11, 7, 20, 0).astimezone()
    assert coach.respond(store, cfg, now, ["hello"], run=run).reply == "hi"
    coach.summarize(store, cfg, now, run=run)
    want = "claude" if provider == "claude" else "codex"
    assert len(calls) == 2 and set(calls) == {want}


def test_no_module_but_ai_calls_a_runner_directly():
    """Every model call in the app goes through ai.py, so the provider switch covers all of it."""
    import ast
    from tilia import paths
    offenders = []
    for f in sorted((paths.REPO_ROOT / "tilia").glob("*.py")):
        if f.name in ("ai.py", "claude.py", "codex.py"):
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module in ("claude", "codex"):
                for a in node.names:
                    if a.name in ("run_claude", "ask_json", "run"):
                        offenders.append(f"{f.name}: {a.name}")
            if isinstance(node, ast.Attribute) and node.attr in ("run_claude",) \
                    and isinstance(node.value, ast.Name) and node.value.id in ("claude", "codex"):
                offenders.append(f"{f.name}: {node.value.id}.{node.attr}")
    assert offenders == []
