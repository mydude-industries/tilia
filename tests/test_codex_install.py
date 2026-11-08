"""Installing the one reviewed Codex release into Tilia's data folder. No real download."""
import hashlib
import io
import json
import os
import stat
import tarfile
from types import SimpleNamespace

import pytest

from tilia import codex, codex_install, paths


def tgz(member_name: str, body: bytes, extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in [(member_name, body)] + list((extra or {}).items()):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o755
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def curl_that_writes(payload: bytes, calls=None, rc=0):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        if rc == 0:
            with open(cmd[cmd.index("-o") + 1], "wb") as f:
                f.write(payload)
        return SimpleNamespace(returncode=rc, stdout=b"", stderr=b"")
    return run


def pinned(machine, payload):
    asset, _, member = codex_install.ASSETS[machine]
    return {**codex_install.ASSETS, machine: (asset, hashlib.sha256(payload).hexdigest(), member)}


def test_pins_match_the_release_and_the_version_allowlist():
    assert codex_install.RELEASE == "rust-v0.167.0"
    assert {tuple(int(x) for x in codex_install.RELEASE[6:].split("."))} == set(codex.SUPPORTED_VERSIONS)
    assert codex_install.BASE_URL == "https://github.com/openai/codex/releases/download/rust-v0.167.0/"
    assert codex_install.RELEASE in codex_install.BASE_URL
    for machine, (asset, sha, member) in codex_install.ASSETS.items():
        assert asset.startswith("codex-") and asset.endswith("-apple-darwin.tar.gz")
        assert len(sha) == 64 and int(sha, 16) and member == asset[:-len(".tar.gz")]
    assert set(codex_install.ASSETS) == {"arm64", "x86_64"}
    # the values recorded on 2026-11-07 from the release assets
    assert codex_install.ASSETS["arm64"][1] == \
        "4b22a2a971be2791c3e31f912f3e77a88ebd64a65812a7289ccba65dd6425fe9"
    assert codex_install.ASSETS["x86_64"][1] == \
        "d8eb4a865a0b17319cde10347523e5bab0978d01725b39f4a639f40905e01221"


def test_install_downloads_the_pinned_asset_checks_it_and_unpacks_the_binary(monkeypatch):
    payload = tgz("codex-aarch64-apple-darwin", b"#!/bin/sh\necho codex-cli 0.167.0\n")
    monkeypatch.setattr(codex_install, "ASSETS", pinned("arm64", payload))
    calls = []
    assert codex_install.install(run=curl_that_writes(payload, calls), machine="arm64") is None
    cmd, kw = calls[0]
    assert cmd[0] == "curl" and "--proto" in cmd and cmd[cmd.index("--proto") + 1] == "=https"
    assert cmd[-1] == codex_install.BASE_URL + "codex-aarch64-apple-darwin.tar.gz"
    assert kw["timeout"] == codex_install.DOWNLOAD_TIMEOUT_S
    b = paths.codex_bin()
    assert b.read_bytes().startswith(b"#!/bin/sh") and stat.S_IMODE(b.stat().st_mode) == 0o700
    assert stat.S_IMODE(b.parent.stat().st_mode) == 0o700
    assert sorted(p.name for p in b.parent.iterdir()) == ["codex"]  # no .part left behind


def test_x86_64_uses_its_own_asset(monkeypatch):
    payload = tgz("codex-x86_64-apple-darwin", b"bin")
    monkeypatch.setattr(codex_install, "ASSETS", pinned("x86_64", payload))
    calls = []
    assert codex_install.install(run=curl_that_writes(payload, calls), machine="x86_64") is None
    assert calls[0][0][-1].endswith("codex-x86_64-apple-darwin.tar.gz")


def test_unknown_processor_is_refused_without_a_download():
    calls = []
    out = codex_install.install(run=curl_that_writes(b"", calls), machine="ppc")
    assert out == "isn't published for this Mac's processor" and calls == []


def test_download_failure_leaves_nothing_behind():
    out = codex_install.install(run=curl_that_writes(b"", rc=22), machine="arm64")
    assert out == "couldn't be downloaded"
    assert list(paths.codex_bin_dir().iterdir()) == []

    def missing(cmd, **kw):
        raise FileNotFoundError("curl")
    assert codex_install.install(run=missing, machine="arm64") == "couldn't be downloaded"


def test_checksum_mismatch_is_refused_and_the_file_removed():
    payload = tgz("codex-aarch64-apple-darwin", b"evil")
    out = codex_install.install(run=curl_that_writes(payload), machine="arm64")  # real pins
    assert out == "didn't match the checksum Tilia has on record for it"
    assert list(paths.codex_bin_dir().iterdir()) == []
    assert not paths.codex_bin().exists()


@pytest.mark.parametrize("member", ["other-file", "sub/codex-aarch64-apple-darwin",
                                    "../codex-aarch64-apple-darwin"])
def test_wrong_archive_contents_are_refused(monkeypatch, member):
    payload = tgz(member, b"bin")
    monkeypatch.setattr(codex_install, "ASSETS", pinned("arm64", payload))
    out = codex_install.install(run=curl_that_writes(payload), machine="arm64")
    assert out == "wasn't the archive Tilia expected"
    assert not paths.codex_bin().exists() and list(paths.codex_bin_dir().iterdir()) == []


def test_only_the_named_member_is_extracted(monkeypatch):
    payload = tgz("codex-aarch64-apple-darwin", b"bin", {"README": b"x", "evil.sh": b"rm"})
    monkeypatch.setattr(codex_install, "ASSETS", pinned("arm64", payload))
    assert codex_install.install(run=curl_that_writes(payload), machine="arm64") is None
    assert sorted(p.name for p in paths.codex_bin_dir().iterdir()) == ["codex"]


def test_a_corrupt_archive_is_refused(monkeypatch):
    payload = b"not a tarball at all"
    monkeypatch.setattr(codex_install, "ASSETS", pinned("arm64", payload))
    out = codex_install.install(run=curl_that_writes(payload), machine="arm64")
    assert out == "couldn't be unpacked" and not paths.codex_bin().exists()


def test_reinstall_replaces_the_binary_atomically(monkeypatch):
    paths.codex_bin().write_bytes(b"old")
    payload = tgz("codex-aarch64-apple-darwin", b"new")
    monkeypatch.setattr(codex_install, "ASSETS", pinned("arm64", payload))
    assert codex_install.install(run=curl_that_writes(payload), machine="arm64") is None
    assert paths.codex_bin().read_bytes() == b"new"
