import sys
from pathlib import Path

# Personal: each admin's own Gather login, shared by every copy of the repo.
CREDENTIALS_FILE = Path.home() / ".gather"
# The project's: the community Zoom app's credentials, the same for every
# admin, kept in the repo folder (git-ignored) beside client_secret.json.
ZOOM_CREDENTIALS_FILE = Path(__file__).parent.parent / "zoom_credentials"

_ZOOM_KEYS = ("account_id", "client_id", "client_secret")


def read_key_value_file(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Return {key: value} for keys from a private "key = value" file,
    exiting with a message showing the expected format if the file is
    missing, readable by group or others, or lacks any of the keys."""
    example = "\n".join(f"    {k} = ..." for k in keys)
    try:
        text = path.read_text()
    except FileNotFoundError:
        sys.exit(
            f"Error: credentials file not found: {path}\n"
            f"Create it with:\n{example}"
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

    missing = [k for k in keys if not data.get(k)]
    if missing:
        sys.exit(
            f"Error: {path} is missing required key(s): {', '.join(missing)}\n"
            f"Expected format:\n{example}"
        )

    return {k: data[k] for k in keys}


def load_credentials(path: Path = CREDENTIALS_FILE) -> tuple[str, str]:
    """Return (email, password) from ~/.gather.

    Expected format (spaces around '=' are allowed):
        email = admin@example.com
        password = secret
    """
    data = read_key_value_file(path, ("email", "password"))
    return data["email"], data["password"]


def load_zoom_credentials(path: Path = ZOOM_CREDENTIALS_FILE) -> dict[str, str]:
    """Return {"account_id", "client_id", "client_secret"} from the repo's
    zoom_credentials file — the Zoom Server-to-Server OAuth app's
    credentials (see README.md)."""
    return read_key_value_file(path, _ZOOM_KEYS)
