#!/usr/bin/env python3
"""
Writes maintenance_tracker/pages/maintenance-tracker-start-here.md into the
"Maintenance tracker: start here" Google Doc, with its links filled in.

The page's source is in the repo, which is public, so its links, names, and
numbers aren't: each is a placeholder, such as [[status-page]], filled in
from the maintenance tracker's base:

    [[base]]          the base's own link
    [[coordinator]]   the first name ("Dana", from the Name "Dana S") of
                      the person ticked as Coordinator in People
    [[update-links]]  a list of the circle's members (Circle member ticked
                      in People), each name a link to that person's own
                      "Log an update" form: their Update form link, which
                      the maintenance_tracker Apps Script writes
    [[status-page]]   and every other key: the Value of the row in the
                      Links table whose Name makes that key, lowercased with
                      hyphens for spaces ("Status page")

The Links table is also where the maintenance_tracker Apps Script finds the
forms' and the status page's links, so each link is kept in one place.

This script replaces the Doc's whole contents with the result, so nothing
is ever edited in the Doc by hand: a change made there is lost the next
time this runs. The Doc keeps its address and its sharing. It stops,
changing nothing, if a placeholder has no value, if People has no
coordinator, or more than one, or if no circle member has an Update form
link yet. Run it again when the circle's members change.

The Doc is found by its title, wherever admin@ can see it (it's in the
circle's folder in the Shared Drive), so no ID is kept in the repo. Make
it once, by hand, as an empty Google Doc with that title.

Lines wrapped in the source are joined before the Doc is written, so each
paragraph and list item is one paragraph in the Doc. The Doc is then set to
pageless (continuous scroll, with no page breaks), which needs the Google
Docs API enabled as well as Drive.

It reads the test base, "Building Maintenance (test)", which holds the
demo data (see maintenance_demo.py), unless given --real, which reads
"Building Maintenance". Until the real base is set up, the Doc links to the
test base, and says so: text the source wraps in {{trial: …}} (a notice
under the title that the links open a test copy, and a few words elsewhere)
is kept when the test base is read, and left out with --real.

Requires the Airtable token in airtable_credentials, and Google
credentials, as for the repo's other Google scripts (see README.md,
"Setup").

Usage:
    python update_start_here_doc.py -n
    python update_start_here_doc.py
    python update_start_here_doc.py --real
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "maintenance_tracker" / "pages" / "maintenance-tracker-start-here.md"
DOC_TITLE = "Maintenance tracker: start here"
DOC_MIME = "application/vnd.google-apps.document"
UPLOAD_MIME = "text/markdown"
PLACEHOLDER = re.compile(r"\[\[([^\[\]]+)\]\]")
TRIAL = re.compile(r"\{\{trial:(.*?)\}\}", re.DOTALL)


def placeholders(text: str) -> list[str]:
    """The keys of the page's placeholders, in order of first use."""
    return list(dict.fromkeys(m.strip() for m in PLACEHOLDER.findall(text)))


def link_key(name: str) -> str:
    """A Links row's key, from its Name, as the Apps Script's linkKey makes
    it: "Request form" is "request-form"."""
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


def base_values(base_id: str, people: list[dict], links: list[dict]) -> dict[str, str]:
    """What the placeholders can be filled with, from the base: its link,
    its coordinator, the circle's members' own links to the update form,
    and each Links row that has a Value. Raises ValueError unless exactly
    one person (not moved out) is ticked as Coordinator, or if no circle
    member has an Update form link."""
    coordinators = [p["fields"] for p in people
                    if p["fields"].get("Coordinator") and not p["fields"].get("Moved out")]
    if len(coordinators) != 1:
        raise ValueError(f"Expected one person ticked as Coordinator in People; found {len(coordinators)}.")
    values = {}
    for row in links:
        key, value = link_key(row["fields"].get("Name")), str(row["fields"].get("Value") or "").strip()
        if key and value:
            values[key] = value
    values["base"] = f"https://airtable.com/{base_id}"
    values["coordinator"] = (coordinators[0].get("Name") or "").split(" ")[0]
    values["update-links"], _ = update_links(people)
    if not values["update-links"]:
        raise ValueError("No one ticked as Circle member in People has an Update form link. The "
                         "maintenance_tracker Apps Script writes them (run writeFormLinks).")
    return values


def trial_text(text: str, real: bool) -> str:
    """The page with each {{trial:…}} replaced by the text inside it, or,
    for the real base, by nothing; a paragraph left empty goes too."""
    text = TRIAL.sub("" if real else lambda m: m.group(1), text)
    return re.sub(r"\n{3,}", "\n\n", text)


def update_links(people: list[dict]) -> tuple[str, list[str]]:
    """The circle's members' own links to the "Log an update" form, as a
    Markdown list in order of name ("- [Dana S](https://…)"), and the names
    of the circle's members who have no link yet. Leaves out anyone who's
    moved out."""
    circle = sorted((p["fields"] for p in people
                     if p["fields"].get("Circle member") and not p["fields"].get("Moved out")),
                    key=lambda f: str(f.get("Name") or "").casefold())
    items = [f"- [{f.get('Name')}]({f['Update form link'].strip()})"
             for f in circle if str(f.get("Update form link") or "").strip()]
    missing = [str(f.get("Name") or "") for f in circle if not str(f.get("Update form link") or "").strip()]
    return "\n".join(items), missing


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


def pageless_request() -> dict:
    """The Docs API batchUpdate body that makes a Doc pageless."""
    return {"requests": [{"updateDocumentStyle": {
        "documentStyle": {"documentFormat": {"documentMode": "PAGELESS"}},
        "fields": "documentFormat"}}]}


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
    from maintenance_base import REAL_BASE, TEST_BASE
    from util.airtable import Airtable, AirtableError
    from util.credentials import load_airtable_token
    from util.google_group_utils import DEFAULT_CLIENT_SECRETS_PATH

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="print what would be written, and change nothing")
    parser.add_argument("--real", action="store_true",
                        help=f"read {REAL_BASE!r} instead of {TEST_BASE!r}")
    args = parser.parse_args()

    source = SOURCE.read_text()
    airtable = Airtable(load_airtable_token())
    try:
        base = airtable.find_base(REAL_BASE if args.real else TEST_BASE)
        people = airtable.list_records(
            base, "People", ["Name", "Coordinator", "Circle member", "Moved out", "Update form link"])
        values = base_values(base, people, airtable.list_records(base, "Links"))
    except (AirtableError, ValueError) as e:
        sys.exit(f"Error: {e}")
    try:
        page = join_wrapped_lines(fill(trial_text(source, args.real), values))
    except KeyError as e:
        sys.exit("Error: these have no value: " + ", ".join(e.args[0]) +
                 ". Fill in each one's Value in the base's Links table, adding a row if it has none "
                 "(Name \"Status page\" for status-page).")

    without_link = update_links(people)[1]
    if without_link:
        print("Left out, having no Update form link yet (the Apps Script's writeFormLinks writes them): "
              + ", ".join(without_link), file=sys.stderr)

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload
    from util.google_group_utils import get_credentials

    credentials = get_credentials(str(DEFAULT_CLIENT_SECRETS_PATH))
    drive = build("drive", "v3", credentials=credentials)
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
    build("docs", "v1", credentials=credentials).documents().batchUpdate(
        documentId=doc["id"], body=pageless_request()).execute()
    print(f"Updated {doc['webViewLink']}")


if __name__ == "__main__":
    main()
