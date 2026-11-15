#!/usr/bin/env python3
"""
Writes maintenance_tracker/pages/maintenance-tracker-start-here.md into the
"Maintenance tracker: start here" Google Doc, with its links filled in.

The page's source is in the repo, which is public, so its links, names, and
numbers aren't: each is a placeholder, such as [[status-page]]. Their
values are in maintenance_links, a git-ignored file in the repo root, one
per line:

    coordinator = Dana S
    status-page = https://airtable.com/...

This script fills each placeholder from that file, and replaces the Doc's
whole contents with the result, so nothing is ever edited in the Doc by
hand: a change made there is lost the next time this runs. The Doc keeps
its address and its sharing. It stops, changing nothing, if a placeholder
has no value; a value no placeholder uses is listed.

The Doc is found by its title, wherever admin@ can see it (it's in the
circle's folder in the Shared Drive), so no ID is kept in the repo. Make
it once, by hand, as an empty Google Doc with that title.

Lines wrapped in the source are joined before the Doc is written, so each
paragraph and list item is one paragraph in the Doc.

Requires Google credentials, as for the repo's other Google scripts (see
README.md, "Setup").

Usage:
    python update_start_here_doc.py -n
    python update_start_here_doc.py
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "maintenance_tracker" / "pages" / "maintenance-tracker-start-here.md"
LINKS_FILE = ROOT / "maintenance_links"
DOC_TITLE = "Maintenance tracker: start here"
DOC_MIME = "application/vnd.google-apps.document"
UPLOAD_MIME = "text/markdown"
PLACEHOLDER = re.compile(r"\[\[([^\[\]]+)\]\]")


def placeholders(text: str) -> list[str]:
    """The keys of the page's placeholders, in order of first use."""
    return list(dict.fromkeys(m.strip() for m in PLACEHOLDER.findall(text)))


def read_links(text: str) -> dict[str, str]:
    """The "key = value" lines of the links file; blank lines and lines
    starting with # are skipped."""
    links = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"line {number} isn't \"key = value\": {line}")
        links[key.strip()] = value.strip()
    return links


def fill(text: str, links: dict[str, str]) -> str:
    """The page with each placeholder replaced by its value. Raises
    KeyError listing the placeholders that have none."""
    missing = [k for k in placeholders(text) if k not in links]
    if missing:
        raise KeyError(missing)
    return PLACEHOLDER.sub(lambda m: links[m.group(1).strip()], text)


def join_wrapped_lines(text: str) -> str:
    """The Markdown with each paragraph and list item on one line: a line is
    joined to the one before it unless either is blank, a heading, or a
    table row, or it starts a list item."""
    out = []
    for line in text.split("\n"):
        starts_block = (not line.strip() or line.startswith("#") or line.lstrip().startswith("|")
                        or re.match(r"\s*([-*+]|\d+\.)\s", line))
        before = out[-1] if out else ""
        if out and before.strip() and not before.startswith("#") and not before.lstrip().startswith("|") \
                and not starts_block:
            out[-1] = before.rstrip() + " " + line.strip()
        else:
            out.append(line)
    return "\n".join(out)


def find_doc(drive) -> dict:
    """The one Google Doc titled DOC_TITLE that admin@ can see."""
    title = DOC_TITLE.replace("\\", "\\\\").replace("'", "\\'")
    found = drive.files().list(
        q=f"name = '{title}' and mimeType = '{DOC_MIME}' and trashed = false",
        corpora="allDrives", includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id, name, webViewLink)").execute().get("files", [])
    if len(found) != 1:
        raise LookupError(f"Expected one Google Doc titled {DOC_TITLE!r}; found {len(found)}.")
    return found[0]


def main() -> None:
    from util.google_group_utils import DEFAULT_CLIENT_SECRETS_PATH

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="print what would be written, and change nothing")
    parser.add_argument("-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
                        help="the Google OAuth client file (default: client_secret.json)")
    args = parser.parse_args()

    source = SOURCE.read_text()
    try:
        links = read_links(LINKS_FILE.read_text())
    except FileNotFoundError:
        sys.exit(f"Error: {LINKS_FILE} not found. Create it with a line for each of:\n" +
                 "\n".join(f"    {k} = ..." for k in placeholders(source)))
    except ValueError as e:
        sys.exit(f"Error: {LINKS_FILE.name}, {e}")
    try:
        page = join_wrapped_lines(fill(source, links))
    except KeyError as e:
        sys.exit(f"Error: {LINKS_FILE.name} has no value for: " + ", ".join(e.args[0]))
    unused = sorted(set(links) - set(placeholders(source)))
    if unused:
        print(f"Not used by the page (in {LINKS_FILE.name}): " + ", ".join(unused))

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload
    from util.google_group_utils import get_credentials

    drive = build("drive", "v3", credentials=get_credentials(args.credentials))
    try:
        doc = find_doc(drive)
    except LookupError as e:
        sys.exit(f"Error: {e} Make it by hand, as an empty Google Doc in the circle's folder.")
    imports = drive.about().get(fields="importFormats").execute().get("importFormats", {})
    if DOC_MIME not in imports.get(UPLOAD_MIME, []):
        sys.exit(f"Error: Google Drive doesn't convert {UPLOAD_MIME} into Google Docs.")

    if args.dry_run:
        print(f"Would replace the contents of {doc['webViewLink']} with:\n")
        print(page)
        return
    drive.files().update(fileId=doc["id"], supportsAllDrives=True,
                         media_body=MediaInMemoryUpload(page.encode(), mimetype=UPLOAD_MIME)).execute()
    print(f"Updated {doc['webViewLink']}")


if __name__ == "__main__":
    main()
