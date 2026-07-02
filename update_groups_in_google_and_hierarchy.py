#!/usr/bin/env python3
"""
Syncs Google (Drive folders + Groups) and the "Circle Hierarchy" wiki page
with the current /groups list.

For every circle/working group in Gather (kind "circle" or "committee"),
excluding hidden groups:

  0a. If the group has no associated Drive folder, searches for candidate
      folders using the usual name-matching rules and prompts to select one,
      create a new one, or skip. A new folder's name is standardized and
      given the type's suffix ("Circle"/"Working Group"/"Club") if missing,
      then created at the appropriate place in the Shared Drive (top-level
      for circle/committee, under "Clubs" for club). Either way, the folder
      is added to FOLDER_IDS and linked to the group on /gdrive/config with
      Content manager access.
  0b. If the group now has a folder but no mailing list configured, searches
      existing Google Groups using the usual matching rules and prompts to
      select one or create a new one (using the usual create-group logic),
      then wires the result into Gather as the group's email list.
  1.  If it's already in the hierarchy but has been renamed, updates the
      hierarchy line to the current name (matched by Gather group ID, embedded
      in the [Members] link, so renames don't break the association).
  2.  If it has a Drive folder linked (via /gdrive/config) but the hierarchy
      line is missing a [Documents] link, adds one. If the linked folder has
      changed, updates the existing link. If the folder has been unlinked
      entirely, removes the [Documents] link.
  3.  If it's missing from the hierarchy entirely, prompts for a prefix that
      uniquely matches an existing hierarchy entry's name, and adds it as a
      child of that entry — inserted alphabetically among its siblings.
  4.  If a hierarchy entry's linked group no longer exists in Gather, asks
      whether to delete it (reparenting any children to keep the tree intact).

Hidden and deactivated/inactive groups are skipped entirely: not offered
for addition, not flagged as stale if their hierarchy entry still
references them.

In dry-run mode (-n), missing/stale entries and folder/email-list gaps are
only reported, never prompted for.

Requires Google API access (Drive, Admin Directory, Groups Settings):
  1. In Google Cloud Console, create a Desktop OAuth 2.0 client and download
     the JSON as client_secret.json (or pass a different path via -c).
  2. Enable the Google Drive API, Admin SDK (Directory API), and Groups
     Settings API for the project.
  3. On first run the script prints an auth URL — paste it into a browser
     signed in as a Workspace super-admin. The token is cached at
     ~/.google_setup_token.pkl for subsequent runs.

Usage:
    python update_groups_in_google_and_hierarchy.py -e admin@example.com -p secret
    python update_groups_in_google_and_hierarchy.py -u https://example.gather.coop -n
"""

import argparse
import os
import re
import sys
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from google_setup.match_google_groups_to_drive_folders import DEFAULT_DRIVE_ID, list_all_groups
from util.credentials import load_credentials
from util.folder_matching import find_matching_folders, folder_matches_group
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
    set_gather_group_email_list,
)
from util.gdrive_config import (
    add_group_access_to_gdrive_item,
    create_drive_folder,
    create_gdrive_item,
    dump_gdrive_config_row_html,
    folder_name_for_group_type,
    gdrive_item_url,
    load_gdrive_item_map,
    parent_folder_id_for_group_type,
    scrape_gdrive_config,
    walk_drive_folders,
)
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    ensure_group_exists,
    ensure_group_settings,
    get_credentials,
    group_display_name,
    group_email,
    read_folder_ids,
    write_folder_ids,
)
from util.hierarchy_wiki import (
    WIKI_SLUG,
    HierarchyNode,
    ensure_hierarchy_page,
    iter_nodes,
    parse_hierarchy,
    remove_node,
    render_hierarchy,
)

_LOG_FILE = Path("debug/update_groups_in_google_and_hierarchy_log.csv")
_SCREENSHOT_DIR = Path("debug/update_groups_in_google_and_hierarchy_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)

ELIGIBLE_KINDS = {"circle", "committee", "club"}


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


_INACTIVE_NAME_RE = re.compile(r"\(\s*inactive\s*\)\s*$", re.IGNORECASE)


def _group_is_deactivated(page, detail) -> bool:
    """True if the group is deactivated/inactive.

    Confirmed signal: Gather appends "(Inactive)" to the group's own name
    (e.g. "HOA Setup" -> "HOA Setup (Inactive)") rather than exposing a
    separate status field. Also checks an availability value containing
    "deactivat"/"inactive", an unchecked "active" checkbox, or a checked
    "deactivated"/"archived" checkbox, in case this Gather instance uses
    one of those instead/as well.
    """
    if _INACTIVE_NAME_RE.search(detail.name.strip()):
        return True
    availability = detail.availability.strip().lower()
    if "deactivat" in availability or "inactive" in availability:
        return True
    active_checkbox = page.locator('input[type="checkbox"][name*="[active]"]')
    if active_checkbox.count() > 0 and not active_checkbox.first.is_checked():
        return True
    for name_fragment in ("[deactivated]", "[archived]"):
        checkbox = page.locator(f'input[type="checkbox"][name*="{name_fragment}"]')
        if checkbox.count() > 0 and checkbox.first.is_checked():
            return True
    return False


def fetch_group_info(page, base_url: str) -> tuple[dict[str, dict], set[str], set[str]]:
    """Return ({group_id: {"name", "kind", "url", "list_name"}}, excluded_group_ids,
    deactivated_group_ids).

    Hidden and deactivated/inactive groups are reported separately (not
    included in info) so callers can skip them entirely — neither offered
    for addition nor flagged as stale if a hierarchy entry still
    references them. excluded_group_ids is the union of both (hidden and
    deactivated); deactivated_group_ids is the subset that's deactivated —
    unlike merely-hidden groups, deactivated groups should also be removed
    from the hierarchy outright.
    """
    groups = fetch_all_gather_groups(page, base_url)
    log("INFO", "fetch_groups", f"{len(groups)} group(s) found")
    info: dict[str, dict] = {}
    excluded_ids: set[str] = set()
    deactivated_ids: set[str] = set()
    for group in groups:
        detail = _fetch_group_detail(page, base_url, group)
        is_deactivated = _group_is_deactivated(page, detail)
        if is_deactivated or _group_is_hidden(page, detail):
            excluded_ids.add(group.group_id)
            if is_deactivated:
                deactivated_ids.add(group.group_id)
            continue
        info[group.group_id] = {
            "name": detail.name,
            "kind": (detail.kind or "").strip().lower(),
            "url": f"/groups/{group.group_id}",
            "list_name": detail.list_name,
        }
    return info, excluded_ids, deactivated_ids


def fetch_documents_url_by_group_id(
    page, base_url: str, drive_id: str, config_entries: list[dict], drive_folders: list[dict],
) -> tuple[dict[str, str], set[str]]:
    """Return ({group_id: documents_href}, linked_group_ids), given the
    already-scraped /gdrive/config entries and an already-walked Shared
    Drive folder tree (both fetched once in main() and reused across steps).

    linked_group_ids is every group_id that currently has a Drive folder
    linked via /gdrive/config, whether or not its URL could be resolved —
    this lets the caller distinguish "folder unlinked entirely" (not in
    linked_group_ids, so any existing [Documents] link should be removed)
    from "still linked, but couldn't resolve this run" (in linked_group_ids
    but not in the returned dict, so an existing link should be left alone
    rather than destroyed over a transient resolution failure).

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
      4. Matching by exact folder name against the already-walked Shared
         Drive tree (works regardless of nesting depth or of how the
         folder was originally linked in Gather).
    Folders resolved by none of these are logged as unresolved.
    """
    linked_group_ids = {entry["group_id"] for entry in config_entries}

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
            documents_url_by_group_id[entry["group_id"]] = gdrive_item_url(google_file_id)
        else:
            unresolved.append(entry)

    if unresolved:
        by_name: dict[str, list[str]] = {}
        for f in drive_folders:
            by_name.setdefault(f["name"], []).append(f["id"])

        still_unresolved: list[dict] = []
        for entry in unresolved:
            matches = by_name.get(entry["folder_name"], [])
            if len(matches) == 1:
                documents_url_by_group_id[entry["group_id"]] = gdrive_item_url(matches[0])
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

    return documents_url_by_group_id, linked_group_ids


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


# ── Step 1: ensure every eligible group has a Drive folder ────────────────────

def prompt_folder_choice_for_group(
    available: list[dict], taken: list[dict], folder_owner: dict[str, str],
) -> tuple[str, dict | None]:
    """Prompt the user to pick a candidate folder, create a new one, or skip.
    Returns (action, folder) where action is "select", "create", or "skip"
    (folder is None unless action == "select")."""
    if taken:
        print("  Already linked to a different group (not selectable):")
        for f in taken:
            owner = folder_owner.get(f["name"], "?")
            print(f"    {' / '.join(f['path'])}   (linked to group {owner})")

    if available:
        print("  Candidate folders:")
        for i, f in enumerate(available, start=1):
            tag = "" if f["match_kind"] == "strict" else " [weak: shared term only]"
            print(f"    {i}. {' / '.join(f['path'])}{tag}")
        prompt = f"  Select folder [1-{len(available)}], 'c' to create a new folder, or Enter to skip: "
    else:
        print("  No candidate folders found.")
        prompt = "  'c' to create a new folder, or Enter to skip: "

    while True:
        choice = input(prompt).strip()
        if choice == "":
            return "skip", None
        if choice.lower() == "c":
            return "create", None
        if available and choice.isdigit() and 1 <= int(choice) <= len(available):
            return "select", available[int(choice) - 1]
        print("  Invalid choice.")


def create_folder_for_group(
    drive_service, drive_id: str, group_kind: str, dry_run: bool
) -> tuple[str, str] | None:
    """Interactively create a new Drive folder for a group of this kind.
    Returns (folder_id, folder_name), or None if skipped/failed."""
    raw_name = input("  Enter the new folder's name: ").strip()
    if not raw_name:
        print("  No name entered; skipping.")
        return None

    try:
        folder_name = folder_name_for_group_type(raw_name, group_kind)
    except ValueError as e:
        print(f"  ERROR: {e}")
        return None

    try:
        parent_id = parent_folder_id_for_group_type(drive_service, drive_id, group_kind)
    except ValueError as e:
        print(f"  ERROR: {e}")
        return None

    if dry_run:
        print(f"  [dry-run] would create folder '{folder_name}'")
        return None

    folder_id = create_drive_folder(drive_service, folder_name, parent_id)
    print(f"  Created folder '{folder_name}' ({folder_id})")
    return folder_id, folder_name


def ensure_group_folders(
    page, base_url: str, drive_service, drive_id: str,
    group_info: dict[str, dict], linked_group_ids: set[str],
    documents_url_by_group_id: dict[str, str], drive_folders: list[dict],
    folder_owner: dict[str, str], folder_name_by_group_id: dict[str, str],
    folder_ids_entries: list[tuple[str, str]], dry_run: bool,
) -> bool:
    """For every eligible group with no Drive folder, interactively find,
    create, or skip one. If resolved, links it on /gdrive/config with
    Content manager access and records it in folder_ids_entries.

    Mutates linked_group_ids, documents_url_by_group_id, folder_owner,
    folder_name_by_group_id, and folder_ids_entries in place. Returns True
    if folder_ids_entries was changed (caller should write folder_ids.gs).
    """
    missing = [
        (gid, info) for gid, info in group_info.items()
        if info["kind"] in ELIGIBLE_KINDS and gid not in linked_group_ids
    ]
    missing.sort(key=lambda item: item[1]["name"].casefold())

    existing_folder_ids = {fid for fid, _ in folder_ids_entries}
    folder_ids_dirty = False

    for group_id, info in missing:
        group_name = info["name"]
        kind = info["kind"]
        print(f"\nGroup '{group_name}' has no associated Drive folder.")

        if dry_run:
            print("  [dry-run] would search for/prompt for a Drive folder")
            log("INFO", "would_ensure_folder", f"{group_name} (id={group_id})")
            continue

        matches = find_matching_folders(group_name, drive_folders)
        available = [f for f in matches if folder_owner.get(f["name"]) in (None, group_id)]
        taken = [f for f in matches if f not in available]

        action, chosen = prompt_folder_choice_for_group(available, taken, folder_owner)

        if action == "skip":
            log("INFO", "skip_folder", f"{group_name} (id={group_id})")
            continue

        if action == "create":
            result = create_folder_for_group(drive_service, drive_id, kind, dry_run)
            if result is None:
                continue
            folder_id, folder_name = result
        else:
            folder_id, folder_name = chosen["id"], chosen["name"]

        item_id, err = create_gdrive_item(page, base_url, folder_id, dry_run=dry_run)
        if err:
            print(f"  ERROR: folder already in /gdrive/config or failed to link — {err}")
            log("ERROR", "link_folder", f"{group_name}: {folder_name} — {err}")
            continue

        ok = add_group_access_to_gdrive_item(page, base_url, item_id, group_name, dry_run)
        if not ok:
            print(f"  ERROR: folder linked, but failed to add '{group_name}' as Content manager")
            log("ERROR", "add_group_access", f"{group_name}: {folder_name}")

        if folder_id not in existing_folder_ids:
            folder_ids_entries.append((folder_id, folder_name))
            existing_folder_ids.add(folder_id)
            folder_ids_dirty = True

        linked_group_ids.add(group_id)
        documents_url_by_group_id[group_id] = gdrive_item_url(folder_id)
        folder_owner[folder_name] = group_id
        folder_name_by_group_id[group_id] = folder_name
        print(f"  Linked '{folder_name}' to '{group_name}'.")
        log("INFO", "ensure_folder", f"{group_name} (id={group_id}) -> '{folder_name}' ({folder_id})")

    return folder_ids_dirty


# ── Step 2: ensure every eligible (foldered) group has a mailing list ─────────

def prompt_group_choice_for_email_list(matches: list[dict]) -> tuple[str, dict | None]:
    """Prompt to select an existing Google Group, or create a new one, or
    skip. Returns (action, group) as with prompt_folder_choice_for_group."""
    if matches:
        print("  Candidate existing Google Groups:")
        for i, g in enumerate(matches, start=1):
            print(f"    {i}. {g['name']} <{g['email']}>")
        prompt = f"  Select group [1-{len(matches)}], 'c' to create a new group, or Enter to skip: "
    else:
        print("  No candidate Google Groups found.")
        prompt = "  'c' to create a new group, or Enter to skip: "

    while True:
        choice = input(prompt).strip()
        if choice == "":
            return "skip", None
        if choice.lower() == "c":
            return "create", None
        if matches and choice.isdigit() and 1 <= int(choice) <= len(matches):
            return "select", matches[int(choice) - 1]
        print("  Invalid choice.")


def ensure_email_lists(
    page, base_url: str, dir_service, settings_service,
    group_info: dict[str, dict], linked_group_ids: set[str],
    folder_name_by_group_id: dict[str, str], dry_run: bool,
) -> None:
    """For every eligible, foldered group with no mailing list configured,
    search existing Google Groups and prompt to select one or create a new
    one, then wire the result into Gather as the group's email list."""
    eligible = [
        (gid, info) for gid, info in group_info.items()
        if info["kind"] in ELIGIBLE_KINDS
        and gid in linked_group_ids
        and not info.get("list_name")
    ]
    eligible.sort(key=lambda item: item[1]["name"].casefold())
    if not eligible:
        return

    all_groups = None if dry_run else list_all_groups(dir_service)

    for group_id, info in eligible:
        group_name = info["name"]
        folder_name = folder_name_by_group_id.get(group_id)
        if not folder_name:
            log("WARN", "ensure_email_list", group_name, "no known folder name; skipping")
            continue

        print(f"\nGroup '{group_name}' has no mailing list configured.")

        if dry_run:
            print("  [dry-run] would search for/prompt for a matching Google Group")
            log("INFO", "would_ensure_email_list", f"{group_name} (id={group_id})")
            continue

        matches = [g for g in all_groups if folder_matches_group(group_name, g["name"])]
        action, chosen = prompt_group_choice_for_email_list(matches)

        if action == "skip":
            log("INFO", "skip_email_list", f"{group_name} (id={group_id})")
            continue

        if action == "create":
            gemail = group_email(folder_name)
            gdisplay = group_display_name(folder_name)
            created = ensure_group_exists(dir_service, gemail, gdisplay)
            updates = ensure_group_settings(settings_service, gemail)
            log("INFO", "create_google_group",
                f"{gemail} {'created' if created else 'already existed'}; settings updates={updates}")
            email = gemail
        else:
            email = chosen["email"]

        list_local = email.split("@")[0]
        set_gather_group_email_list(page, base_url, group_id, list_local, DOMAIN, dry_run)
        print(f"  Set mailing list for '{group_name}' to {email}")
        log("INFO", "ensure_email_list", f"{group_name} (id={group_id}) -> {email}")


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_hierarchy(
    root, group_info: dict[str, dict], documents_url_by_group_id: dict[str, str],
    linked_group_ids: set[str], excluded_ids: set[str], deactivated_ids: set[str], dry_run: bool,
) -> None:
    """Mutate the parsed hierarchy tree in place: rename, add/alter/remove
    Documents links, prompt for deletion of stale entries.

    Deactivated groups are removed from the hierarchy outright (no
    prompt, since they're gone for good, not just excluded from
    consideration). Merely-hidden groups are left untouched.
    """
    for node in list(iter_nodes(root)):
        if not node.group_id:
            continue

        if node.group_id in deactivated_ids:
            if dry_run:
                print(f"[dry-run] '{node.name}' (id={node.group_id}) is deactivated; "
                      f"would remove from the hierarchy")
                log("INFO", "would_remove_deactivated", f"{node.name} (id={node.group_id})")
            else:
                remove_node(node)
                log("INFO", "remove_deactivated", f"{node.name} (id={node.group_id})")
            continue

        if node.group_id in excluded_ids:
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

        current_documents_url = documents_url_by_group_id.get(node.group_id)

        if not node.documents_url:
            if current_documents_url:
                log("INFO", "add_documents_link", f"{node.name} (id={node.group_id})")
                print(f"Adding Documents link for '{node.name}'")
                node.documents_url = current_documents_url
        elif current_documents_url and current_documents_url != node.documents_url:
            log("INFO", "update_documents_link",
                f"{node.name} (id={node.group_id}): {node.documents_url!r} -> {current_documents_url!r}")
            print(f"Updating Documents link for '{node.name}'")
            node.documents_url = current_documents_url
        elif not current_documents_url and node.group_id not in linked_group_ids:
            log("INFO", "remove_documents_link", f"{node.name} (id={node.group_id})")
            print(f"Removing Documents link for '{node.name}' (folder no longer linked)")
            node.documents_url = ""


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
    dir_service = build("admin", "directory_v1", credentials=creds)
    settings_service = build("groupssettings", "v1", credentials=creds)

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

        group_info, excluded_ids, deactivated_ids = fetch_group_info(page, base_url)

        log("INFO", "walk_drive", f"Walking folder tree of Shared Drive {drive_id}…")
        drive_folders = walk_drive_folders(drive_service, drive_id)
        log("INFO", "walk_drive", f"{len(drive_folders)} folder(s) found")

        config_entries = scrape_gdrive_config(page, base_url)
        folder_owner = {e["folder_name"]: e["group_id"] for e in config_entries}
        folder_name_by_group_id = {e["group_id"]: e["folder_name"] for e in config_entries}
        folder_ids_entries = list(read_folder_ids())

        documents_url_by_group_id, linked_group_ids = fetch_documents_url_by_group_id(
            page, base_url, drive_id, config_entries, drive_folders
        )

        # Step 1: ensure every eligible group has a Drive folder.
        folder_ids_dirty = ensure_group_folders(
            page, base_url, drive_service, drive_id, group_info, linked_group_ids,
            documents_url_by_group_id, drive_folders, folder_owner, folder_name_by_group_id,
            folder_ids_entries, dry_run,
        )
        if folder_ids_dirty:
            write_folder_ids(folder_ids_entries)
            log("INFO", "folder_ids", f"Wrote folder_ids.gs with {len(folder_ids_entries)} entries.")

        # Step 2: ensure every eligible, now-foldered group has a mailing list.
        ensure_email_lists(
            page, base_url, dir_service, settings_service, group_info, linked_group_ids,
            folder_name_by_group_id, dry_run,
        )

        sync_hierarchy(
            root, group_info, documents_url_by_group_id, linked_group_ids,
            excluded_ids, deactivated_ids, dry_run
        )
        add_missing_groups(root, group_info, documents_url_by_group_id, dry_run)

        new_content = render_hierarchy(root)
        ok = ensure_hierarchy_page(page, base_url, new_content, dry_run)
        if not ok:
            log("ERROR", "sync", "Failed to write hierarchy wiki page — see log")

        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Sync Google Drive folders/Groups and the Circle Hierarchy wiki page "
                    "with the current /groups list",
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
        "-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
        help="Path to OAuth client secrets JSON (for the Drive folder-ID fallback search) "
             f"(default: {DEFAULT_CLIENT_SECRETS_PATH})",
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
