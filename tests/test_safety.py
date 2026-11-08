"""The test suite itself must never start a real process or read the real Messages/Contacts
databases (the autouse guard in conftest.py)."""
import os
import subprocess
from pathlib import Path

import pytest

from tilia import contacts, imessage


def test_subprocess_run_is_blocked_without_marker():
    with pytest.raises(RuntimeError, match="real_subprocess"):
        subprocess.run(["true"])


def test_popen_is_blocked_without_marker():
    with pytest.raises(RuntimeError, match="real_subprocess"):
        subprocess.Popen(["true"])


@pytest.mark.parametrize("fn", ["system", "execv", "execve", "execvp", "execvpe",
                                "spawnv", "spawnve", "spawnvp", "spawnvpe",
                                "spawnl", "spawnle", "spawnlp", "spawnlpe",
                                "posix_spawn", "posix_spawnp", "popen"])
def test_os_process_calls_are_blocked(fn):
    with pytest.raises(RuntimeError, match="real_subprocess"):
        getattr(os, fn)("true")


@pytest.mark.real_subprocess
def test_marker_allows_a_real_process():
    assert subprocess.run(["true"]).returncode == 0


def test_chat_db_points_nowhere_real():
    real = Path("~/Library/Messages/chat.db").expanduser()
    assert imessage.CHAT_DB != real
    assert not imessage.CHAT_DB.exists()
    assert imessage.can_read() is False


def test_addressbook_points_nowhere_real():
    real = Path("~/Library/Application Support/AddressBook").expanduser()
    assert Path(contacts.ADDRESSBOOK).expanduser() != real
    assert contacts.load_contacts() == {}


def test_commands_helper_refuses_real_claude(home):
    from tests.test_commands import h, mkcfg, FakeTG
    from tilia import paths
    from tilia.store import Store
    store = Store(paths.db_path())
    assert h("/journal", store, mkcfg(), FakeTG()) is True
    with pytest.raises(AssertionError, match="real claude called"):
        h("today was hard", store, mkcfg(), FakeTG())
