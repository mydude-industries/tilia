"""User settings, stored as TOML in the data folder (hand-rolled writer, stdlib reader)."""

import os
import tomllib
from dataclasses import dataclass, field, fields

from . import paths


@dataclass
class Config:
    provider: str = "claude"         # "claude" | "chatgpt"
    model: str = "sonnet"            # the Claude model
    chatgpt_model: str = "gpt-6-luna"
    chat_id: int | None = None
    checkin_cap: int = 2
    quiet_start: int = 22
    quiet_end: int = 9
    paused_until: str | None = None
    battery_awake: bool = False
    threads: list[dict] = field(default_factory=list)
    backfill_days: int = 365
    referrals: str = "never"
    tone: str = "very_direct"
    goal: str = ""


def active_model(cfg: Config) -> str:
    """The model for the chosen provider. Anything but "chatgpt" means Claude."""
    return cfg.chatgpt_model if cfg.provider == "chatgpt" else cfg.model


def provider_name(cfg: Config) -> str:
    return "ChatGPT" if cfg.provider == "chatgpt" else "Claude"


def _s(v: str) -> str:
    out = []
    for c in v:
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


toml_str = _s  # a TOML basic string, also used for `codex -c key="value"` overrides


def _v(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return _s(str(v))


def dumps(cfg: Config) -> str:
    lines = []
    for f in fields(cfg):
        if f.name == "threads":
            continue
        val = getattr(cfg, f.name)
        if val is not None:
            lines.append(f"{f.name} = {_v(val)}")
    for t in cfg.threads:
        lines += ["", "[[threads]]", f"name = {_s(str(t['name']))}", f"chat_id = {int(t['chat_id'])}"]
    return "\n".join(lines) + "\n"


def load() -> Config:
    p = paths.config_path()
    cfg = Config()
    if not p.exists():
        return cfg
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    for f in fields(cfg):
        if f.name != "threads" and f.name in data:
            setattr(cfg, f.name, data[f.name])
    cfg.threads = [
        {"name": t["name"], "chat_id": t["chat_id"]}
        for t in data.get("threads", [])
        if isinstance(t, dict) and "name" in t and "chat_id" in t
    ]
    return cfg


def save(cfg: Config) -> None:
    """Write a temp file and swap it in, so a crash never leaves half a config."""
    p = paths.config_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, p)
