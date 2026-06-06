import sys
from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".gather"


def load_credentials(path: Path = CREDENTIALS_FILE) -> tuple[str, str]:
    """Return (email, password) from ~/.gather.

    Expected format (spaces around '=' are allowed):
        email = admin@example.com
        password = secret
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        sys.exit(
            f"Error: credentials file not found: {path}\n"
            f"Create it with:\n"
            f"    email = admin@example.com\n"
            f"    password = secret"
        )

    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        sys.exit(
            f"Error: {path} must not be readable by group or others.\n"
            f"Fix with: chmod 600 {path}"
        )

    data = {}
    for line in text.splitlines():
        line = line.strip()
        if line and "=" in line:
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()

    missing = [k for k in ("email", "password") if not data.get(k)]
    if missing:
        sys.exit(
            f"Error: {path} is missing required key(s): {', '.join(missing)}\n"
            f"Expected format:\n"
            f"    email = admin@example.com\n"
            f"    password = secret"
        )

    return data["email"], data["password"]
