"""
Tests for util.credentials: the private key = value files (~/.gather,
zoom_credentials).

All values are fictional.
"""

from pathlib import Path

import pytest

from util.credentials import ZOOM_CREDENTIALS_FILE, load_credentials, load_zoom_credentials

ROOT = Path(__file__).resolve().parent.parent


def test_zoom_credentials_file_is_in_repo_root_and_git_ignored():
    assert ZOOM_CREDENTIALS_FILE.resolve().parent == ROOT
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ZOOM_CREDENTIALS_FILE.name in ignored


def _write(tmp_path, text, mode=0o600):
    path = tmp_path / "creds"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_gather_credentials(tmp_path):
    path = _write(tmp_path, "email = a@b.org\npassword = pw = x\n")
    assert load_credentials(path) == ("a@b.org", "pw = x")


def test_zoom_credentials(tmp_path):
    path = _write(tmp_path, "account_id=A\nclient_id = C\nclient_secret = S\n")
    assert load_zoom_credentials(path) == {
        "account_id": "A", "client_id": "C", "client_secret": "S"}


def test_missing_key(tmp_path):
    path = _write(tmp_path, "account_id = A\nclient_id = C\n")
    with pytest.raises(SystemExit, match="client_secret"):
        load_zoom_credentials(path)


def test_readable_by_others(tmp_path):
    path = _write(tmp_path, "email = a\npassword = b\n", mode=0o644)
    with pytest.raises(SystemExit, match="chmod 600"):
        load_credentials(path)


def test_missing_file(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        load_zoom_credentials(tmp_path / "nope")
