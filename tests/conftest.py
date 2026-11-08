import os
import subprocess
from pathlib import Path

import pytest

from tilia import contacts, imessage

_OS_PROCESS_FNS = ("system", "popen", "execv", "execve", "execvp", "execvpe", "execl",
                   "execle", "execlp", "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                   "spawnl", "spawnle", "spawnlp", "spawnlpe", "posix_spawn", "posix_spawnp")


def _blocked(name):
    def refuse(*a, **k):
        raise RuntimeError(f"tests may not start a real process ({name}); mark the test "
                           "@pytest.mark.real_subprocess if it truly needs one")
    return refuse


@pytest.fixture(autouse=True)
def _no_real_world(request, tmp_path_factory, monkeypatch):
    """No test starts a real process (so never the real `claude`, launchctl or Keychain)
    unless marked real_subprocess, and none can reach the real Messages or Contacts data."""
    if request.node.get_closest_marker("real_subprocess") is None:
        # subprocess.run/call/check_output all go through subprocess.Popen, including the
        # function objects captured as `run=subprocess.run` defaults.
        monkeypatch.setattr(subprocess, "Popen", _blocked("subprocess.Popen"))
        for fn in _OS_PROCESS_FNS:
            if hasattr(os, fn):
                monkeypatch.setattr(os, fn, _blocked(f"os.{fn}"))
    # Never the real data folder, even in a test that forgets the `home` fixture.
    monkeypatch.setenv("TILIA_HOME", str(tmp_path_factory.mktemp("default-home") / "Tilia"))
    nowhere = tmp_path_factory.mktemp("no-real-data") / "missing"
    monkeypatch.setattr(imessage, "CHAT_DB", nowhere / "chat.db")
    monkeypatch.setattr(contacts, "ADDRESSBOOK", nowhere / "AddressBook")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "tilia-home"
    monkeypatch.setenv("TILIA_HOME", str(h))
    return h
