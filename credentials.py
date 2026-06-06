from pathlib import Path

CREDENTIALS_FILE = Path.home() / ".gather"


def load_credentials(path: Path = CREDENTIALS_FILE) -> tuple[str, str]:
    """Return (email, password) from ~/.gather.

    Expected format (spaces around '=' are allowed):
        email = admin@example.com
        password = secret
    """
    data = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and "=" in line:
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    try:
        return data["email"], data["password"]
    except KeyError as e:
        raise KeyError(f"Missing key {e} in {path}")
