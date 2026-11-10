import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "webapp"
VENDOR = WEB / "vendor" / "telegram-web-app.js"
FORBIDDEN = ("http://", "https://", 'src="//', 'href="//', "url(//", "@import")
SVG_NS = "http://www.w3.org/2000/svg"


def _own_files():
    return [p for p in WEB.rglob("*")
            if p.suffix in {".html", ".js", ".css", ".json"} and "vendor" not in p.relative_to(WEB).parts]


def test_webapp_references_no_other_origins():
    files = _own_files()
    assert any(p.name == "index.html" for p in files)
    for p in files:
        # The SVG namespace is an identifier, never fetched; everything else must be ours.
        text = p.read_text(encoding="utf-8").replace(SVG_NS, "")
        for bad in FORBIDDEN:
            assert bad not in text, f"{p.relative_to(ROOT)} contains {bad}"


def test_vendor_header():
    head = "".join(VENDOR.read_text(encoding="utf-8").splitlines(keepends=True)[:5])
    assert "https://telegram.org/js/telegram-web-app.js" in head
    assert re.search(r"\d{4}-\d{2}-\d{2}", head)
    assert VENDOR.stat().st_size < 200 * 1024


def test_index_has_csp_and_relative_scripts():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert ('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; img-src \'self\' '
            'data:; style-src \'self\' \'unsafe-inline\'; script-src \'self\'">') in html
    srcs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    assert srcs == ["vendor/telegram-web-app.js", "core.js", "blot.js", "flourish.js", "app.js"]
    assert "<script>" not in html  # CSP forbids inline scripts


def test_help_nudges_copy():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    nudges = ["Look at the white space.", "What's moving?", "Turn your phone upside down.",
              "Is it one thing or two?"]
    for n in nudges:
        assert n in app, n
    assert [app.index(n) for n in nudges] == sorted(app.index(n) for n in nudges)
    assert "Help me see it" in app


def test_card_nudges_copy():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    nudges = ["Look at the hands.", "What's in the background?",
              "Where is the light coming from?", "Who is looking where?"]
    for n in nudges:
        assert n in app, n
    assert [app.index(n) for n in nudges] == sorted(app.index(n) for n in nudges)
    assert "Which one grabs you?" in app
    assert "This card couldn't load." in app  # a card id the deck doesn't list
    assert app.count('addEventListener("error"') >= 2  # the back and the face both recover
    assert "tarot/manifest.json" in app and "card-back" in app
    # the page shows art, name and symbols only: never a scene or a meaning
    for field in (".scene", ".meaning", "\"meaning\"", ".number"):
        assert field not in app, field


def test_spread_layout_markup():
    from tilia import entries
    app = (WEB / "app.js").read_text(encoding="utf-8")
    css = (WEB / "app.css").read_text(encoding="utf-8")
    layouts = re.search(r"var LAYOUTS = \{(.*?)\};", app, re.S).group(1)
    for key in entries.SPREADS:
        assert re.search(rf"\b{key}:", layouts), key
        assert f".spread-{key.removeprefix('spread_')}" in css, key
        sheet = entries.load_sheet(key)
        assert [c["prompt"] for c in sheet["cards"] if c["kind"] == "reveal"] \
            == entries.SPREADS[key]  # the page names positions from the sheet
    assert "parseLaunch" in app and "launch.cards" in app
    assert "@media (max-width: 359px)" in css  # the cross collapses to a list
    # a single card takes exactly one id; a dotted value is a broken link
    assert 'raw.type === "tarot" && (launch.cards.length !== 1' in app


def test_no_vibe_tells_in_css():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert "linear-gradient" not in css
    assert "border-left" not in css


@pytest.mark.real_subprocess
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_suite():
    # Node 21+ no longer takes a directory after --test, so list the files.
    files = sorted(str(p.relative_to(ROOT)) for p in (WEB / "test").glob("*.test.js"))
    assert files
    r = subprocess.run(["node", "--test", *files], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dark_mode_follows_telegram_and_the_browser():
    """D16: Telegram's colorScheme (and its changes) sets data-theme; a plain browser falls
    to prefers-color-scheme. Both carry the same dark palette, and card paper never turns."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'tg.onEvent("themeChanged", applyTheme)' in js and "tg.colorScheme" in js
    media = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\}\s*\}', css, re.S)
    tg = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', css, re.S)
    assert media and tg
    norm = lambda block: sorted(line.strip() for line in block.strip().splitlines())  # noqa: E731
    assert norm(media.group(1)) == norm(tg.group(1))
    assert "--paper: #23211d" in tg.group(1) and "--ink: #ebe5d6" in tg.group(1)
    assert "--card-paper" not in tg.group(1)  # cards and blots keep their paper
    assert "--card-paper: #f6f1e6;" in css    # blot.js's own paper
    for name in re.findall(r"^\s*(--[a-z0-9-]+):", css, re.M):  # no token defined by itself
        assert not re.search(rf"{name}:\s*var\({name}\)", css), name


def test_the_reveal_is_teased_from_the_first_screen():
    """D17, in the owner's words."""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    for line in ("One quick check-in, then flip your tarot card. We'll both share our readings!",
                 "One quick check-in, then flip your cards. We'll both share our readings!",
                 "A fresh blot, made only for you. One quick check-in, then open it."):
        assert f'"{line}"' in js
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert ".fold.open .flap { transform: none; }" in css
    assert re.search(r"prefers-reduced-motion: reduce\) \{\s*\.fold \.sheet-in, \.fold \.flap \{ transition: none; \}", css)


def test_blot_first_card_why_promises_no_hidden_meaning():
    import json
    sheet = json.loads((WEB / "worksheets" / "blot.json").read_text(encoding="utf-8"))
    first = next(c for c in sheet["cards"] if c["id"] == "first")
    assert first["why"] == "Whatever you see first is a good place to start talking from."
    assert all("options" not in c and "option_mode" not in c for c in sheet["cards"])
