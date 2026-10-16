"""
Keeps README.md in step with the repo's scripts: every script must be
mentioned in it, and every script it mentions must still exist.

"Scripts" are the .py files in the repo root and the script directories
(not util/ or tests/, and not __init__.py), the Apps Script .gs files, and
the shell scripts in the root.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIRS = ["data_import", "google_setup", "cleanup"]
APPS_SCRIPT_DIRS = ["groups_drive_sync", "calendar_notifications"]


def _scripts() -> list[Path]:
    paths = list(ROOT.glob("*.py")) + list(ROOT.glob("*.sh"))
    for d in SCRIPT_DIRS:
        paths += [p for p in (ROOT / d).glob("*.py") if p.name != "__init__.py"]
    for d in APPS_SCRIPT_DIRS:
        paths += list((ROOT / d).glob("*.gs"))
    return sorted(paths)


def _readme() -> str:
    return (ROOT / "README.md").read_text()


def test_every_script_is_in_readme():
    readme = _readme()
    # Named in backticks, alone or after a directory path (`dir/name.py`).
    missing = [str(p.relative_to(ROOT)) for p in _scripts()
               if not re.search(r"`(?:[\w./-]*/)?" + re.escape(p.name) + "`", readme)]
    assert not missing, (
        "Add these scripts to README.md (with a short description): " + ", ".join(missing))


def test_every_script_in_readme_exists():
    existing = {p.name for p in ROOT.rglob("*") if p.suffix in (".py", ".gs", ".sh")}
    mentioned = set(re.findall(r"`(?:[\w./-]*/)?([\w.-]+\.(?:py|gs|sh))`", _readme()))
    stale = sorted(mentioned - existing)
    assert not stale, "README.md mentions files that no longer exist: " + ", ".join(stale)
