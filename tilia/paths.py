"""Locations of Tilia's files. The data folder must never live inside the repo."""

import os
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT = "~/Library/Application Support/Tilia"


def data_dir() -> Path:
    raw = os.environ.get("TILIA_HOME") or _DEFAULT
    d = Path(raw).expanduser().resolve()
    if d == REPO_ROOT or REPO_ROOT in d.parents:
        raise RuntimeError(f"data folder {d} is inside the repo; refusing to run")
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def shown_data_dir() -> str:
    """The data folder as shown to her: ~/... when it is under her home (no username)."""
    d = data_dir()
    try:
        return "~/" + str(d.relative_to(Path.home().resolve()))
    except ValueError:
        return str(d)


def db_path() -> Path:
    return data_dir() / "tilia.db"


def profile_path() -> Path:
    return data_dir() / "profile.md"


def config_path() -> Path:
    return data_dir() / "config.toml"


def setup_state_path() -> Path:
    return data_dir() / "setup_state.json"


def crisis_log_path() -> Path:
    return data_dir() / "crisis.log"


def _private_dir(d: Path) -> Path:
    """An existing-or-new folder that is a real directory, ours, and mode 0700."""
    if not d.is_symlink():
        d.mkdir(mode=0o700, exist_ok=True)
    st = os.lstat(d)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"{d.name} in the data folder is not a private folder; refusing to run")
    os.chmod(d, 0o700)
    return d


def claude_cwd() -> Path:
    """Where every `claude` call runs: an empty folder only this user can write, so no one
    can plant project settings (.claude/) for `--setting-sources project` to load."""
    d = _private_dir(data_dir() / "claude-cwd")
    if os.path.lexists(d / ".claude"):
        raise RuntimeError("claude-cwd in the data folder contains a .claude entry that Tilia "
                           "never makes; refusing to run claude. Delete it and check who made it.")
    return d


def codex_cwd() -> Path:
    """Where every `codex` call runs: an empty private folder. Codex is told to load no
    AGENTS.md and no project config, and anything found here was put there by someone else."""
    d = _private_dir(data_dir() / "codex-cwd")
    stray = next(d.iterdir(), None)
    if stray is not None:
        raise RuntimeError(f"codex-cwd in the data folder contains {stray.name}, which Tilia "
                           "never makes; refusing to run codex. Delete it and check who made it.")
    return d


def codex_home() -> Path:
    """Tilia's own CODEX_HOME: Codex's login and its side files live here, never in ~/.codex,
    so nothing planted there (config, AGENTS.md, skills, plugins, hooks) is ever read."""
    return _private_dir(data_dir() / "codex-home")


def codex_bin_dir() -> Path:
    """Where setup unpacks the one reviewed Codex release (see codex_install.py)."""
    return _private_dir(data_dir() / "bin")


def codex_bin() -> Path:
    """The only codex Tilia ever runs: never one from PATH."""
    return codex_bin_dir() / "codex"


def codex_instructions() -> Path:
    """Holds the system text for one codex call at a time (0600; removed after the call)."""
    return _private_dir(data_dir() / "codex-instructions")


def import_dir() -> Path:
    """Where she drops the file for "Bring what your AI already knows": a private folder in
    her home, never Documents or Desktop (those may sync to iCloud)."""
    return _private_dir(Path.home() / "Tilia Import")


def tmp_dir() -> Path:
    """The agent's private TMPDIR (set in the launchd plist), instead of the shared /tmp."""
    return _private_dir(data_dir() / "tmp")
