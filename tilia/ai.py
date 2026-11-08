"""One door to the model. cfg.provider picks the runner: "chatgpt" is codex, else claude.
Both runners share the error types, the hardening rules and the JSON reading."""
from __future__ import annotations

import subprocess

from . import claude, codex, config
from .claude import LimitError, MissingCLI, NotLoggedIn, UnsupportedCLI  # noqa: F401


def run_model(cfg, prompt: str, system: str, run=subprocess.run, timeout: int | None = None) -> str:
    """Raw answer text. Raises LimitError, NotLoggedIn, MissingCLI, or RuntimeError."""
    model = config.active_model(cfg)
    if cfg.provider == "chatgpt":
        return codex.run(prompt, model, system, run=run, timeout=timeout or codex.TIMEOUT_S)
    return claude.run_claude(prompt, model, system, run=run, timeout=timeout or claude.TIMEOUT_S)


def ask_json(cfg, prompt: str, system: str, run=subprocess.run) -> dict | None:
    return claude.extract_obj(run_model(cfg, prompt, system, run=run))
