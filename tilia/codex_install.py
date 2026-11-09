"""Install the one Codex version Tilia has reviewed, from OpenAI's GitHub release, into
Tilia's data folder.

Why not Homebrew or npm: Homebrew installs the newest Codex, which Tilia refuses until it has
reviewed that version's feature list; npm installs a JavaScript launcher that can never pass
Apple's signature check. So setup downloads the exact release asset for this Mac's processor,
checks its SHA-256 against the value recorded here, unpacks the single binary into
`<data>/bin/codex` (a 0700 folder), and setup then runs the codesign/notarization checks
before Tilia runs it even once. Tilia only ever runs that binary, never one from PATH.

The checksums below were computed from the release assets on 2026-11-07; the unpacked
arm64 binary was byte-identical to the one Homebrew's cask installs for 0.167.0."""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import tarfile

from . import paths

RELEASE = "rust-v0.167.0"            # the version in codex.SUPPORTED_VERSIONS
# The only web address setup downloads Codex from (allow-listed, with this reason, in
# tests/test_net.py): OpenAI's own release of that exact version.
BASE_URL = "https://github.com/openai/codex/releases/download/rust-v0.167.0/"
ASSETS = {
    # machine: (asset, sha256 of the asset, the one file inside it)
    "arm64": ("codex-aarch64-apple-darwin.tar.gz",
              "4b22a2a971be2791c3e31f912f3e77a88ebd64a65812a7289ccba65dd6425fe9",
              "codex-aarch64-apple-darwin"),
    "x86_64": ("codex-x86_64-apple-darwin.tar.gz",
               "d8eb4a865a0b17319cde10347523e5bab0978d01725b39f4a639f40905e01221",
               "codex-x86_64-apple-darwin"),
}
DOWNLOAD_TIMEOUT_S = 900
MAX_BINARY_BYTES = 1024 * 1024 * 1024


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def install(run=subprocess.run, machine: str | None = None) -> str | None:
    """Download, check and unpack Codex into paths.codex_bin(). Returns None when done, or
    one plain clause saying what went wrong ("couldn't be downloaded", ...)."""
    machine = machine or platform.machine()
    if machine not in ASSETS:
        return "isn't published for this Mac's processor"
    asset, want, member = ASSETS[machine]
    bin_dir = paths.codex_bin_dir()
    part = bin_dir / (asset + ".part")
    final = paths.codex_bin()
    part.unlink(missing_ok=True)
    try:
        r = run(["curl", "-fsSL", "--proto", "=https", "--tlsv1.2", "-o", str(part),
                 BASE_URL + asset], capture_output=True, timeout=DOWNLOAD_TIMEOUT_S)
        ok = getattr(r, "returncode", 1) == 0 and part.is_file()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        part.unlink(missing_ok=True)
        return "couldn't be downloaded"
    try:
        if _sha256(part) != want:
            return "didn't match the checksum Tilia has on record for it"
        with tarfile.open(part, "r:gz") as tar:
            m = next((x for x in tar.getmembers() if x.isfile()
                      and os.path.basename(x.name) == member and "/" not in x.name.strip("./")
                      and ".." not in x.name), None)
            if m is None or m.size > MAX_BINARY_BYTES:
                return "wasn't the archive Tilia expected"
            src = tar.extractfile(m)
            tmp = bin_dir / "codex.new"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
            with os.fdopen(fd, "wb") as out, src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    out.write(chunk)
            os.chmod(tmp, 0o700)
            os.replace(tmp, final)
    except (OSError, tarfile.TarError):
        return "couldn't be unpacked"
    finally:
        part.unlink(missing_ok=True)
    return None


def remove() -> None:
    """Delete the installed binary (and any half-written copy). Setup calls this when the
    binary fails its signature or notarization check, so it is never kept, never run, and
    the next run of setup downloads a fresh one."""
    for f in (paths.codex_bin(), paths.codex_bin_dir() / "codex.new"):
        f.unlink(missing_ok=True)
