import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_no_personal_data", ROOT / "scripts" / "check_no_personal_data.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
main = mod.main


def test_flags_db_file(tmp_path):
    f = tmp_path / "x.db"
    f.write_bytes(b"")
    assert main([str(f)]) == 1


def test_flags_token(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("token = 123456789:AAH" + "x" * 32)
    assert main([str(f)]) == 1


def test_flags_real_looking_phone(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("call +1801" + "5551234")  # built at runtime so the repo stays clean
    assert main([str(f)]) == 1
    g = tmp_path / "b.txt"
    g.write_text("call +15550100001")
    assert main([str(g)]) == 0


def test_flags_large_file(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("a" * (210 * 1024))
    assert main([str(f)]) == 1


def test_clean_file_passes(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("hello world, ids 1234567890")
    assert main([str(f)]) == 0


def test_gitignore_blocks_data():
    text = (ROOT / ".gitignore").read_text()
    for pat in ["*.db", "*.sqlite", "data/", "*.log", "profile*.md"]:
        assert pat in text


SIDECARS = ("x.db-wal", "x.db-shm", "x.sqlite-wal", "x.sqlite-shm", "x.db-journal")


def test_flags_sqlite_sidecar_files(tmp_path):
    for name in SIDECARS:
        f = tmp_path / name
        f.write_bytes(b"")
        assert main([str(f)]) == 1, name


def test_gitignore_blocks_sqlite_sidecars():
    text = (ROOT / ".gitignore").read_text().splitlines()
    for pat in ("*.db-wal", "*.db-shm", "*.sqlite-wal", "*.sqlite-shm", "*.db-journal"):
        assert pat in text


def test_issue_template_points_at_the_real_repo():
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text()
    assert "OWNER" not in text
    assert "https://github.com/mydude-industries/tilia/security/advisories/new" in text
