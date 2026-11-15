"""No organisational address is written out whole in the repo, which is
public (see "Keep real data out of the repo" in CLAUDE.md)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHOLE_ADDRESS = re.compile(r"[\w.+-]+@(?:berkeleymoshav|urbanmoshav)\.org")


def test_no_whole_organisational_addresses():
    found = []
    for name in subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.splitlines():
        try:
            text = (ROOT / name).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if WHOLE_ADDRESS.search(line):
                found.append(f"{name}:{number}: {line.strip()}")
    assert not found, "Write these as name@ (or build them from DOMAIN):\n" + "\n".join(found)
