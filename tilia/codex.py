"""Hardened `codex exec` runner: the ChatGPT provider, with claude.run_claude's contract.

Codex CLI is OpenAI's local agent. Tilia uses it as a plain model call, so everything agentic
is switched off and nothing is kept: no shell, files, patches, web, apps, plugins, skills,
MCP or sub-agents; no session transcript (`--ephemeral`); no user config, rules or AGENTS.md
from anywhere (`--ignore-user-config`, `--ignore-rules`, `project_doc_max_bytes=0`), and its
home is a private folder of Tilia's own (CODEX_HOME), so nothing someone put in ~/.codex is
read. The system text (it carries the profile) goes in a 0600 file that lives only for the
call; the prompt goes on stdin. Nothing personal is ever in argv.

Tools are a denylist in Codex (there is no documented "no tools" switch), so two guards
make it a closed list: DISABLED_FEATURES names every feature of the checked Codex version
that can run, read, write, patch, browse or call anything (tests check it against the
version's full `codex features list`), and any Codex version not in SUPPORTED_VERSIONS,
older or newer, is refused outright (UnsupportedCLI) rather than run with features Tilia
has not seen. The check is redone whenever the binary itself changes. The read-only
sandbox (macOS Seatbelt) is kept as the floor underneath: no writes, no network for anything
the model might still get to run.
Every flag and key here is documented for Codex CLI 0.167 (see the provider report)."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile

from . import config, paths
from .claude import LimitError, MissingCLI, NotLoggedIn, UnsupportedCLI

TIMEOUT_S = 120
# Exactly the Codex versions whose full `codex features list` was reviewed (a snapshot per
# version lives in tests/fixtures). Any other version, older or newer, is refused: its flags
# may differ or not exist, which could leave a tool on.
SUPPORTED_VERSIONS = frozenset({(0, 167, 0)})

# Every feature of Codex 0.167.0 (`codex features list`, tests/fixtures) that can act:
# execute, read or write files, patch, browse, drive apps, call MCP/plugins/skills/tools,
# spawn agents, keep memories or goals, or run hooks. "removed" features are not listed
# (they no longer exist); the rest are forced off whatever their default.
DISABLED_FEATURES = (
    # command execution
    "shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "shell_snapshot_v2",
    "incremental_exec_tools", "incremental_tools", "deferred_executor",
    "executor_capability_discovery", "executor_capability_discovery_v2",
    "exec_permission_approvals", "executed_tool_call_metadata", "apply_patch_streaming_events",
    # files, images, screen, browser
    "view_image", "image_generation", "computer_use", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "browser_annotation_api", "in_app_browser",
    "in_app_local_automation", "multi_env_copy_file",
    # code mode (a sandboxed REPL that calls tools)
    "code_mode", "code_mode_host", "code_mode_host_grpc", "code_mode_host_output_truncation",
    "code_mode_interrupt", "code_mode_only", "code_mode_only_strict_3p_tools",
    "code_mode_prewarm", "code_mode_tool_search",
    # apps, MCP, plugins, skills, tool discovery
    "apps", "apps_early_resource_discovery", "enable_mcp_apps", "codex_apps_mcp_2026_07_28",
    "mcp_2026_07_28", "mcp_connection_sharing", "mcp_tool_list_changed",
    "non_prefixed_mcp_tool_names", "tool_call_mcp_elicitation", "plugins", "remote_plugin",
    "plugin_discovery_prefetch", "plugin_sharing", "recommended_plugins", "skill_search",
    "skill_mcp_dependency_install", "skill_catalog_deltas", "tool_suggest",
    "request_permissions_tool", "sleep_tool", "deferred_tool_world_state",
    "prediction_preserve_parent_tools", "retain_client_tool_outputs",
    # sub-agents and agent messaging
    "multi_agent", "multi_agent_v2", "multi_agent_v2_dynamic_tools", "runtime_agent_instances",
    "subagent_thread_environments", "agent_message_board", "wake_on_agent_messages",
    "use_agent_identity", "guardian_conversation_history_tools",
    "guardian_enhanced_node_repl_transcripts", "guardian_native_multi_agent_history",
    "guardian_node_repl_transcript_images",
    # memory, goals, hooks, workspace state, web search
    "memories", "external_agent_memory_import", "hooks", "goals", "artifact", "chronicle",
    "mentions_v2", "workspace_dependencies", "standalone_web_search", "web_search_cached",
    "web_search_request",
)

# Not tools, but switched off too: no shared background server may outlive a call.
OTHER_FEATURES_OFF = ("daemon_auto_start",)

OVERRIDES = tuple(f"features.{f}=false" for f in DISABLED_FEATURES + OTHER_FEATURES_OFF) + (
    'web_search="disabled"',            # removes the web tool (default would be "cached")
    "project_doc_max_bytes=0",          # no AGENTS.md: not the global one, not a local one
    'history.persistence="none"',       # nothing in history.jsonl
    "memories.generate_memories=false",
    "memories.use_memories=false",
    "skills.bundled.enabled=false",
    "analytics.enabled=false",
    'forced_login_method="chatgpt"',    # her ChatGPT login only, never an API key from the env
)

_LIMIT_RE = re.compile(r"hit your usage limit|usage limit|rate limit|quota exceeded|"
                       r"upgrade to plus", re.I)
_AUTH_RE = re.compile(r"sign in again|not logged in|unauthorized|\b401\b|codex login|"
                      r"could not be refreshed", re.I)
# "Try again at 3:45 PM." or "... or try again at Nov 8th, 2026 3:45 PM."
_RESET_RE = re.compile(r"try again at ([^.\n]+?)(?:\.|$)", re.I)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_PROFILE_CLAIM = "api.openai.com/profile"

_checked: tuple | None = None   # (binary identity, version) this process has accepted


def _reset_hint(text: str) -> str:
    m = _RESET_RE.search(text or "")
    return f"try again at {m.group(1).strip()}" if m else ""


def _env(home: str) -> dict:
    """The agent's environment minus anything that could redirect Codex (OPENAI_*, CODEX_*),
    with CODEX_HOME pointing at Tilia's private folder."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI_", "CODEX_"))}
    env["CODEX_HOME"] = home
    return env


def _identity() -> tuple | None:
    """The pinned codex binary as it is right now: real path, mtime and size. None if absent."""
    try:
        real = os.path.realpath(paths.codex_bin())
        st = os.stat(real)
    except OSError:
        return None
    return (real, st.st_mtime_ns, st.st_size)


def login_email(home: str | None = None) -> str | None:
    """The e-mail of the ChatGPT login in CODEX_HOME/auth.json, read from the id_token's
    payload claims only. Nothing from the file is kept or logged; None if unreadable."""
    try:
        data = json.loads((paths.codex_home() if home is None else __import__("pathlib")
                           .Path(home)).joinpath("auth.json").read_text(encoding="utf-8"))
        token = data["tokens"]["id_token"]
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    email = claims.get("email")
    if not isinstance(email, str):  # older tokens: a profile claim keyed by OpenAI's URL
        prof = next((v for k, v in claims.items() if str(k).endswith(_PROFILE_CLAIM)), None)
        email = prof.get("email") if isinstance(prof, dict) else None
    return email if isinstance(email, str) and "@" in email else None


def _sweep_instructions() -> None:
    """A crash can leave a system file behind; nothing in that folder outlives a call."""
    for leftover in paths.codex_instructions().iterdir():
        try:
            leftover.unlink()
        except OSError:
            pass


def _dotted(v: tuple) -> str:
    return ".".join(map(str, v))


def check_version(run=subprocess.run, timeout: int = 30) -> tuple:
    """Refuse any Codex version Tilia has not reviewed (SUPPORTED_VERSIONS): its feature
    list may hold tools Tilia has not switched off. The accepted answer is cached for this
    process only while the binary on PATH is the same file (path, mtime, size), so an
    upgrade mid-run is caught; failures are never cached."""
    global _checked
    ident = _identity()
    if _checked is not None and ident is not None and _checked[0] == ident:
        return _checked[1]
    try:
        r = run([str(paths.codex_bin()), "--version"], capture_output=True, text=True,
                timeout=timeout)
    except FileNotFoundError as e:
        raise MissingCLI("Tilia's codex binary is missing (run ./setup ai)") from e
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"codex --version failed: {type(e).__name__}") from e
    m = _VERSION_RE.search(r.stdout or "") if r.returncode == 0 else None
    if m is None:
        raise UnsupportedCLI("codex did not report a version Tilia can read")
    v = tuple(int(x) for x in m.groups())
    if v not in SUPPORTED_VERSIONS:
        raise UnsupportedCLI(f"codex {_dotted(v)} is not a version Tilia has checked "
                             f"({', '.join(_dotted(x) for x in sorted(SUPPORTED_VERSIONS))})")
    _checked = (ident, v) if ident is not None else None
    return v


def _write_instructions(system: str) -> str:
    fd, path = tempfile.mkstemp(prefix="system-", suffix=".md",
                                dir=str(paths.codex_instructions()))  # mkstemp: 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(system)
    except BaseException:
        _remove(path)
        raise
    return path


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _command(model: str, cwd: str, instructions: str) -> list[str]:
    cmd = [str(paths.codex_bin()), "exec", "--ephemeral", "--ignore-user-config",
           "--ignore-rules", "--skip-git-repo-check", "-s", "read-only", "-C", cwd, "-m", model]
    for o in OVERRIDES:
        cmd += ["-c", o]
    cmd += ["-c", f"model_instructions_file={config.toml_str(instructions)}", "--json", "-"]
    return cmd


def _parse(stdout: str) -> tuple[str | None, list[str]]:
    """(last agent message, error messages) from `--json` output; unreadable lines are skipped."""
    text, errors = None, []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "item.completed":
            item = ev.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" \
                    and isinstance(item.get("text"), str):
                text = item["text"]
        elif kind == "error" and isinstance(ev.get("message"), str):
            errors.append(ev["message"])
        elif kind == "turn.failed":
            err = ev.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                errors.append(err["message"])
    return text, errors


def _attempt(prompt: str, model: str, system: str, run, timeout: int, cwd: str, home: str) -> str:
    instructions = _write_instructions(system)
    try:
        try:
            proc = run(_command(model, cwd, instructions), input=prompt, capture_output=True,
                       text=True, timeout=timeout, cwd=cwd, env=_env(home))
        except FileNotFoundError as e:
            raise MissingCLI("Tilia's codex binary is missing (run ./setup ai)") from e
    finally:
        _remove(instructions)
    text, errors = _parse(proc.stdout or "")
    if proc.returncode != 0 or errors:
        blob = " ".join(errors + [proc.stderr or ""]).strip()
        if _LIMIT_RE.search(blob):
            raise LimitError(blob[:300], _reset_hint(blob))
        if _AUTH_RE.search(blob):
            raise NotLoggedIn(blob[:300])
        raise RuntimeError(f"codex failed (rc={proc.returncode}): {blob[:300]}")
    if text is None:
        raise RuntimeError("codex gave no answer")
    return text


def _call_with_retry(prompt, model, system, run, timeout, cwd, home) -> str:
    """Retry once on timeout/failure; limit, auth and missing-CLI errors propagate at once."""
    for attempt in range(2):
        try:
            return _attempt(prompt, model, system, run, timeout, cwd, home)
        except (LimitError, NotLoggedIn, MissingCLI):
            raise
        except (subprocess.TimeoutExpired, RuntimeError, OSError):
            if attempt == 1:
                raise
    raise RuntimeError("unreachable")


def run(prompt: str, model: str, system: str, run=subprocess.run,
        timeout: int = TIMEOUT_S) -> str:
    """The final answer's text. Raises LimitError, NotLoggedIn, MissingCLI, UnsupportedCLI,
    or RuntimeError."""
    cwd = str(paths.codex_cwd())    # raise before any call if a folder isn't private/empty
    home = str(paths.codex_home())
    _sweep_instructions()
    check_version(run)
    try:
        return _call_with_retry(prompt, model, system, run, timeout, cwd, home)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"codex failed: {type(e).__name__}") from e
