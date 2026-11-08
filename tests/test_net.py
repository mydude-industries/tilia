import ast
import re
import io
import json
from pathlib import Path

import pytest

from tilia import net

ROOT = Path(__file__).resolve().parent.parent
BANNED = {"urllib", "http", "socket", "ssl", "requests"}


def test_only_net_imports_network_libs():
    offenders = []
    for f in (ROOT / "tilia").glob("*.py"):
        if f.name == "net.py":
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if n.split(".")[0] in BANNED:
                    offenders.append(f"{f.name}: {n}")
    assert offenders == []


@pytest.mark.parametrize("url", [
    "https://example.com/bot1/getMe",
    "http://api.telegram.org/bot1/getMe",
    "https://api.telegram.org.evil.com/bot1/getMe",
    "https://evil.com@api.telegram.org.evil.com/x",
])
def test_post_rejects_other_hosts(url):
    called = []
    with pytest.raises(ValueError):
        net.post(url, {}, opener=lambda *a, **k: called.append(1))
    with pytest.raises(ValueError):
        net.post_multipart(url, {}, {}, opener=lambda *a, **k: called.append(1))
    assert called == []


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_post_returns_json():
    seen = {}

    def opener(req, timeout=None):
        seen["url"] = req.full_url
        seen["data"] = req.data
        return _Resp(json.dumps({"ok": True}).encode())

    assert net.post("https://api.telegram.org/botX/getMe", {"a": 1}, opener=opener) == {"ok": True}
    assert seen["data"] == b"a=1"


def test_post_error_redacts_token():
    def opener(req, timeout=None):
        raise OSError(f"boom {req.full_url}")

    with pytest.raises(net.NetError) as ei:
        net.post("https://api.telegram.org/botSECRET-tok/getMe", {}, opener=opener)
    assert "SECRET-tok" not in str(ei.value)
    assert "SECRET-tok" not in repr(ei.value)
    assert ei.value.__cause__ is None


def test_multipart_body():
    seen = {}

    def opener(req, timeout=None):
        seen["req"] = req
        return _Resp(b'{"ok": true}')

    out = net.post_multipart("https://api.telegram.org/botX/sendDocument",
                             {"chat_id": "5"}, {"document": ("a.txt", b"hello", "text/plain")},
                             opener=opener)
    assert out == {"ok": True}
    req = seen["req"]
    assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in req.data and b"hello" in req.data
    assert b'filename="a.txt"' in req.data


# ---- final fix: no other way out, by shell tool or by URL -------------------------

# Matched as whole words: each string is split on whitespace (so an exact list element
# like "nc" is one token), trailing shell punctuation is dropped, and a path like
# /usr/bin/curl counts as curl. "curly" or "ssh-free" are not matches.
SHELL_NET = {"curl", "wget", "nc", "osascript", "ssh"}
# Setup's only outside links: BotFather, Claude's own installer/login, api.telegram.org
# (net.py), and OpenAI's GitHub release of the one Codex version Tilia has reviewed
# (codex_install.py downloads exactly that pinned asset and checks its SHA-256 before use).
# System Settings links (x-apple.systempreferences:) are not web URLs.
ALLOWED_URLS = ("https://api.telegram.org", "https://t.me/BotFather",
                "https://claude.ai/install.sh", "https://claude.ai/login",
                "https://github.com/openai/codex/releases/download/rust-v0.167.0")
# The two allow-listed lines: setup's Claude installer (tilia/setup_wizard.py), and the
# `curl` that codex_install.py runs for the pinned Codex release (argv list, so the tool
# name is its own constant). Nothing else in tilia/ may name a shell network tool.
ALLOWED_STRINGS = {("setup_wizard.py", "curl -fsSL https://claude.ai/install.sh | bash"),
                   ("codex_install.py", "curl")}
URL_RE = re.compile(r"https?://[^\s\"'`)<>]*")


def _strings(src: str):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _tools(s: str):
    for tok in s.split():
        name = tok.strip("\"'`;|&()<>,").rsplit("/", 1)[-1]
        if name in SHELL_NET:
            yield name


def network_claims(name: str, src: str) -> list[str]:
    bad = []
    for s in _strings(src):
        if (name, s) in ALLOWED_STRINGS:
            continue
        bad += [f"{name}: {tool!r} in {s[:60]!r}" for tool in _tools(s)]
        # Any URL, even a bare "https://" whose host is filled in at run time (f-strings).
        bad += [f"{name}: URL {u!r}" for u in URL_RE.findall(s)
                if not any(u == a or u.startswith(a + "/") for a in ALLOWED_URLS)]
    return bad


@pytest.mark.parametrize("snippet", [
    'run(["curl", "-s", "https://example.com"])',
    'subprocess.run(["open", "https://example.com/x"])',
    'cmd = "osascript -e x"',
    'x = "ssh me@host"',
    'x = "echo hi | nc host 80"',
    'x = "wget -q"',
    'u = f"https://api.telegram.org.evil.com/{t}"',
    'u = f"https://{host}/x"',
    'u = "http://" + host',
    'run(["nc", "host", "80"])',
    'run(["/usr/bin/curl", "-s", url])',
    'x = "ssh\tme@host"',
    'run(["osascript"])',
])
def test_scanner_catches_other_ways_out(snippet):
    assert network_claims("x.py", snippet)


@pytest.mark.parametrize("snippet", [
    'x = "curly hair and sync"',
    'x = "once"',
    'x = "the ssh-free path"',
    'x = "https://t.me/BotFather"',
    'u = f"https://api.telegram.org/bot{t}/getMe"',
])
def test_scanner_does_not_flag_innocent_words(snippet):
    assert network_claims("x.py", snippet) == []


def test_no_shell_network_tools_or_other_urls_in_tilia():
    offenders = []
    files = sorted((ROOT / "tilia").rglob("*.py"))
    assert ROOT / "tilia" / "net.py" in files
    for f in files:
        offenders += network_claims(f.name, f.read_text(encoding="utf-8"))
    assert offenders == []
