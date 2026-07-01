#!/usr/bin/env python3
"""
Syncs the "Circle Hierarchy" wiki page with the current /groups list.

For every circle/working group in Gather (kind "circle" or "committee"),
excluding hidden groups:
  - If it's already in the hierarchy but has been renamed, updates the
    hierarchy line to the current name (matched by Gather group ID, embedded
    in the [Members] link, so renames don't break the association).
  - If it has a Drive folder linked (via /gdrive/config) but the hierarchy
    line is missing a [Documents] link, adds one.
  - If it's missing from the hierarchy entirely, prompts for a prefix that
    uniquely matches an existing hierarchy entry's name, and adds it as a
    child of that entry — inserted alphabetically among its siblings.
  - If a hierarchy entry's linked group no longer exists in Gather, asks
    whether to delete it (reparenting any children to keep the tree intact).

Hidden groups are skipped entirely: not offered for addition, not flagged
as stale if their hierarchy entry still references them.

In dry-run mode (-n), missing/stale entries are only reported, never
prompted for.

Resolving a group's linked Drive folder ID (for the [Documents] link)
requires Google API access, since Gather's own /gdrive/config page doesn't
reliably expose it:
  1. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  2. Enable the Google Drive API for the project.
  3. On first run the script prints an auth URL — paste it into a browser
     signed in as a Workspace super-admin. The token is cached at
     ~/.google_setup_token.pkl for subsequent runs.

Usage:
    python update_circle_hierarchy.py -e admin@example.com -p secret
    python update_circle_hierarchy.py -u https://example.gather.coop -n
"""

import argparse
import os
import sys
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from google_setup.match_google_groups_to_drive_folders import DEFAULT_DRIVE_ID
from util.credentials import load_credentials
from util.gather_utils import (
    _codemirror_get,
    _fetch_group_detail,
    close_log,
    configure,
    fetch_all_gather_groups,
    init_log,
    launch_browser,
    log,
    login,
)
from util.gdrive_config import (
    dump_gdrive_config_row_html,
    gdrive_item_url,
    load_gdrive_item_map,
    scrape_gdrive_config,
    walk_drive_folders,
)
from util.google_group_utils import get_credentials, read_folder_ids
from util.hierarchy_wiki import (
    WIKI_SLUG,
    HierarchyNode,
    ensure_hierarchy_page,
    iter_nodes,
    parse_hierarchy,
    remove_node,
    render_hierarchy,
)

_LOG_FILE = Path("debug/update_circle_hierarchy_log.csv")
_SCREENSHOT_DIR = Path("debug/update_circle_hierarchy_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)

ELIGIBLE_KINDS = {"circle", "committee"}


# ── Data gathering ─────────────────────────────────────────────────────────────

def fetch_current_hierarchy(page, base_url: str) -> str:
    """Read the current Circle Hierarchy wiki page content, or exit with an
    error if it doesn't exist yet."""
    page.goto(f"{base_url}/wiki/{WIKI_SLUG}", wait_until="networkidle")
    page_title = page.title()
    page_exists = (
        "Exception" not in page_title
        and "Error" not in page_title
        and "404" not in page_title
        and page.locator("h1").count() > 0
    )
    if not page_exists:
        sys.exit(
            f"Error: the '{WIKI_SLUG}' wiki page doesn't exist yet. "
            "Run import_groups.py first to create it."
        )

    page.goto(f"{base_url}/wiki/{WIKI_SLUG}/edit", wait_until="networkidle")
    if page.locator(".CodeMirror").count() == 0:
        sys.exit("Error: editor not found on the hierarchy wiki edit page.")
    return _codemirror_get(page)


def _group_is_hidden(page, detail) -> bool:
    """True if the group is hidden — either via a "Hidden" availability
    value, or a boolean hidden checkbox on the edit page (whichever this
    Gather instance uses)."""
    if detail.availability.strip().lower() == "hidden":
        return True
    checkbox = page.locator('input[type="checkbox"][name*="[hidden]"]')
    return checkbox.count() > 0 and checkbox.first.is_checked()


def fetch_group_info(page, base_url: str) -> tuple[dict[str, dict], set[str]]:
    """Return ({group_id: {"name", "kind", "url"}}, hidden_group_ids).

    Hidden groups are reported separately (not included in info) so callers
    can skip them entirely — neither offered for addition nor flagged as
    stale if a hierarchy entry still references them.
    """
    groups = fetch_all_gather_groups(page, base_url)
    log("INFO", "fetch_groups", f"{len(groups)} group(s) found")
    info: dict[str, dict] = {}
    hidden_ids: set[str] = set()
    for group in groups:
        detail = _fetch_group_detail(page, base_url, group)
        if _group_is_hidden(page, detail):
            hidden_ids.add(group.group_id)
            continue
        info[group.group_id] = {
            "name": detail.name,
            "kind": (detail.kind or "").strip().lower(),
            "url": f"/groups/{group.group_id}",
        }
    return info, hidden_ids


def fetch_documents_url_by_group_id(page, base_url: str, drive_service, drive_id: str) -> dict[str, str]:
    """Return {group_id: documents_href} for every Gather group with a
    Drive folder linked via /gdrive/config.

    /gdrive/config's Folders rows don't reliably expose the underlying
    Drive folder ID at all (confirmed: some rows render the folder name as
    a plain label with no link, no external_id field, and no working
    show/edit page for the item — only "add group" and "delete item"
    actions). So the Drive folder ID is resolved via, in order:
      1. A /gdrive/item/{id} link on the folder name, if the row happens
         to have one.
      2. gdrive_item_map.json — google_file_id -> item_id, populated
         whenever create_gdrive_item() links a new folder.
      3. groups_drive_sync/folder_ids.gs — folder_id/folder_name pairs,
         populated by match_google_groups_to_drive_folders.py.
      4. A live Google Drive API search of the Shared Drive tree, matching
         by exact folder name (works regardless of nesting depth or of
         how the folder was originally linked in Gather). Only performed
         if something is still unresolved after the first three steps.
    Folders resolved by none of these are logged as unresolved.
    """
    config_entries = scrape_gdrive_config(page, base_url)

    item_id_to_google_file_id = {
        item_id: google_file_id
        for google_file_id, item_id in load_gdrive_item_map().items()
    }
    google_file_id_by_folder_name = {
        name: folder_id for folder_id, name in read_folder_ids()
    }

    documents_url_by_group_id: dict[str, str] = {}
    unresolved: list[dict] = []
    for entry in config_entries:
        google_file_id = (
            entry["google_file_id"]
            or item_id_to_google_file_id.get(entry["item_id"])
            or google_file_id_by_folder_name.get(entry["folder_name"])
        )
        if google_file_id:
            documents_url_by_group_id[entry["group_id"]] = gdrive_item_url(base_url, google_file_id)
        else:
            unresolved.append(entry)

    if unresolved:
        log("INFO", "documents_link",
            f"{len(unresolved)} folder(s) unresolved via config/cache; "
            f"searching Shared Drive {drive_id} by name…")
        drive_folders = walk_drive_folders(drive_service, drive_id)
        by_name: dict[str, list[str]] = {}
        for f in drive_folders:
            by_name.setdefault(f["name"], []).append(f["id"])

        still_unresolved: list[dict] = []
        for entry in unresolved:
            matches = by_name.get(entry["folder_name"], [])
            if len(matches) == 1:
                documents_url_by_group_id[entry["group_id"]] = gdrive_item_url(base_url, matches[0])
            else:
                still_unresolved.append(entry)
                if len(matches) > 1:
                    log("WARN", "documents_link", entry["folder_name"],
                        f"{len(matches)} Drive folders share this name — ambiguous, skipping")
        unresolved = still_unresolved

    for entry in unresolved:
        dump_path = dump_gdrive_config_row_html(page, base_url, entry["folder_name"])
        dump_note = f"row HTML dumped to {dump_path}" if dump_path else \
            "row HTML dump also failed — no matching row found"
        log("WARN", "documents_link", entry["folder_name"],
            f"no known Drive folder ID for item_id={entry['item_id']!r}, and no "
            f"exact name match found in Shared Drive {drive_id} ({dump_note})")

    return documents_url_by_group_id


# ── Interactive parent selection ───────────────────────────────────────────────

def prompt_for_parent(group_name: str, all_nodes: list) -> "object | None":
    """Ask the user for a prefix uniquely matching an existing hierarchy
    node's name. Returns the matched node, or None if the user skips."""
    while True:
        prefix = input(
            f"Group '{group_name}' is not in the hierarchy. Enter a prefix "
            f"uniquely matching its parent circle's name (blank to skip): "
        ).strip()
        if not prefix:
            return None
        matches = [n for n in all_nodes if n.name.lower().startswith(prefix.lower())]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            print(f"  No hierarchy entry starts with {prefix!r}. Try again.")
        else:
            names = ", ".join(f"'{n.name}'" for n in matches)
            print(f"  Ambiguous — matches {len(matches)} entries: {names}. Try again.")


def prompt_yes_no(question: str) -> bool:
    answer = input(f"{question} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_hierarchy(
    root, group_info: dict[str, dict], documents_url_by_group_id: dict[str, str],
    hidden_ids: set[str], dry_run: bool,
) -> None:
    """Mutate the parsed hierarchy tree in place: rename, add Documents
    links, prompt for deletion of stale entries."""
    for node in list(iter_nodes(root)):
        if not node.group_id or node.group_id in hidden_ids:
            continue

        live = group_info.get(node.group_id)
        if live is None:
            if dry_run:
                print(f"[dry-run] '{node.name}' (id={node.group_id}) no longer exists "
                      f"in Gather; would prompt to delete")
                log("INFO", "would_remove", f"{node.name} (id={node.group_id})")
            elif prompt_yes_no(
                f"Group '{node.name}' (id={node.group_id}) is in the hierarchy "
                f"but no longer exists in Gather. Delete it from the hierarchy?"
            ):
                remove_node(node)
                log("INFO", "remove", f"{node.name} (id={node.group_id})")
            continue

        if node.name != live["name"]:
            log("INFO", "rename", f"{node.name!r} -> {live['name']!r} (id={node.group_id})")
            print(f"Renaming '{node.name}' -> '{live['name']}'")
            node.name = live["name"]

        if not node.documents_url:
            documents_url = documents_url_by_group_id.get(node.group_id)
            if documents_url:
                log("INFO", "add_documents_link", f"{node.name} (id={node.group_id})")
                print(f"Adding Documents link for '{node.name}'")
                node.documents_url = documents_url


def add_missing_groups(
    root, group_info: dict[str, dict], documents_url_by_group_id: dict[str, str],
    dry_run: bool,
) -> None:
    """Prompt for and add any eligible group not yet present in the hierarchy."""
    tree_group_ids = {n.group_id for n in iter_nodes(root) if n.group_id}
    all_nodes = list(iter_nodes(root))

    missing = [
        (gid, info) for gid, info in group_info.items()
        if info["kind"] in ELIGIBLE_KINDS and gid not in tree_group_ids
    ]
    missing.sort(key=lambda item: item[1]["name"].casefold())

    for group_id, info in missing:
        if dry_run:
            print(f"[dry-run] '{info['name']}' (id={group_id}) is missing from the "
                  f"hierarchy; would prompt for its parent circle")
            log("INFO", "would_add_missing", f"{info['name']} (id={group_id})")
            continue

        parent = prompt_for_parent(info["name"], all_nodes)
        if parent is None:
            log("INFO", "skip_missing", f"{info['name']} (id={group_id})")
            continue

        new_node = HierarchyNode(
            name=info["name"],
            group_id=group_id,
            members_url=info["url"],
            documents_url=documents_url_by_group_id.get(group_id, ""),
            parent=parent,
        )
        parent.children.append(new_node)
        all_nodes.append(new_node)
        log("INFO", "add_missing", f"{info['name']} (id={group_id}) under '{parent.name}'")
        print(f"Added '{info['name']}' under '{parent.name}'")


def main(base_url: str, email: str, password: str, dry_run: bool,
         credentials_path: str, drive_id: str):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} dry_run={dry_run}")

    creds = get_credentials(credentials_path)
    drive_service = build("drive", "v3", credentials=creds)

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            close_log()
            browser.close()
            sys.exit(1)

        current_content = fetch_current_hierarchy(page, base_url)
        root = parse_hierarchy(current_content)

        group_info, hidden_ids = fetch_group_info(page, base_url)
        documents_url_by_group_id = fetch_documents_url_by_group_id(
            page, base_url, drive_service, drive_id
        )

        sync_hierarchy(root, group_info, documents_url_by_group_id, hidden_ids, dry_run)
        add_missing_groups(root, group_info, documents_url_by_group_id, dry_run)

        new_content = render_hierarchy(root)
        ok = ensure_hierarchy_page(page, base_url, new_content, dry_run)
        if not ok:
            log("ERROR", "sync", "Failed to write hierarchy wiki page — see log")

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Sync the Circle Hierarchy wiki page with the current /groups list",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would change without writing the wiki page",
    )
    parser.add_argument(
        "-c", "--credentials", default="client_secret.json",
        help="Path to OAuth client secrets JSON (for the Drive folder-ID fallback search)",
    )
    parser.add_argument(
        "-d", "--drive-id", default=DEFAULT_DRIVE_ID,
        help="Shared Drive ID to search when a folder's ID can't be found any other way",
    )
    args = parser.parse_args()

    if not os.path.exists(args.credentials):
        sys.exit(
            f"Error: credentials file not found: {args.credentials}\n"
            "Download a Desktop OAuth 2.0 client JSON from Google Cloud Console."
        )

    email, password = load_credentials()
    main(args.base_url, email, password, args.dry_run, args.credentials, args.drive_id)


if __name__ == "__main__":
    cli()
