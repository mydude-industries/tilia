"""The coach's working notes on the person: `profile.md` in the data folder."""

import os
from pathlib import Path

from . import paths

MAX_IMPORT_BYTES = 100 * 1024
IMPORT_HEADING = "## What my AI already knew"


def read() -> str:
    p = paths.profile_path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write(text: str) -> None:
    """Atomic write; the previous version is kept as profile.prev.md (summarizer drift guard)."""
    p = paths.profile_path()
    if p.exists():
        prev = p.with_name("profile.prev.md")
        prev.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        prev.chmod(0o600)
    tmp = p.with_name("profile.md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, p)


def import_text(text: str, merge: bool = False) -> None:
    """Seed profile.md from text (at most 100 KB). With merge, an existing profile keeps its
    content and the text goes under "## What my AI already knew"."""
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("that is over 100 KB; trim it to the notes that matter")
    current = read().strip() if merge else ""
    if not current:
        write(text)  # verbatim
        return
    write(f"{current}\n\n{IMPORT_HEADING}\n\n{text.strip()}\n")


def import_file(path, merge: bool = False) -> None:
    """Seed profile.md from a file. Refuses files inside the repo and files over 100 KB."""
    src = Path(path).expanduser().resolve()
    if src == paths.REPO_ROOT or paths.REPO_ROOT in src.parents:
        raise ValueError("that file is inside the Tilia code folder; keep personal notes "
                         "outside it and import from there")
    if not src.is_file():
        raise ValueError(f"no such file: {src}")
    if src.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("that file is over 100 KB; trim it to the notes that matter")
    import_text(src.read_text(encoding="utf-8"), merge=merge)
