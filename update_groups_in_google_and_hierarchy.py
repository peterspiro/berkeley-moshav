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
      is linked to the group on /gdrive/config with Content manager access.
  0b. If the group now has a folder but no mailing list configured, searches
      existing Google Groups using the usual matching rules and prompts to
      select one or create a new one (using the usual create-group logic),
      then wires the result into Gather as the group's email list. Either
      way — newly created or pre-existing — the group's "Conversation
      history" setting is turned on.
  0c. Once every group's folder and mailing-list state is settled, ensures
      each associated Google Group's custom footer carries an up-to-date
      "Gather group"/"Google Docs folder" link block (this is what
      groups_drive_sync.gs reads to know which folder to sync membership
      from — see util/google_group_footer.py). Any other footer content
      is preserved; the block is only touched when Gather itself
      currently implies a (possibly different) pairing for that email.
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
      (Clubs are never offered here — steps 0a/0b still give them a Drive
      folder, Google Group, and mailing list, but they're excluded from the
      Circle Hierarchy entirely.)
  4.  If a hierarchy entry's linked group no longer exists in Gather, asks
      whether to delete it (reparenting any children to keep the tree intact).

Hidden and deactivated/inactive groups are skipped entirely: not offered
for addition, not flagged as stale if their hierarchy entry still
references them.

The outermost hierarchy entry (the root) is always forced to represent the
"everyone" group: named "Full Community (Circle of Everyone / Top Circle /
HOA)", linked to the "Full Community" Gather group via [Members], and
linked to the root Shared Drive page (/gdrive) via [Documents]. This
happens unconditionally on every run, overwriting any manual edits to that
line.

In dry-run mode (-n), missing/stale entries and folder/email-list gaps are
only reported, never prompted for.

Every interactive prompt accepts 'q' (or 'quit') to stop immediately —
whatever's already been done (Drive folders linked, groups created,
footer links, hierarchy edits) is still saved.

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
from util.folder_matching import find_matching_folders
from util.gather_utils import (
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
    ensure_folder_name_available,
    folder_name_for_group_type,
    gdrive_item_url,
    get_drive_folder_by_id,
    load_gdrive_item_map,
    parent_folder_id_for_group_type,
    scrape_gdrive_config,
    walk_drive_folders,
)
from util.google_group_footer import (
    compute_group_footer_updates,
    ensure_group_footer,
    fetch_footer_folder_id,
)
from util.google_group_utils import (
    CONVERSATION_HISTORY_SETTINGS,
    DEFAULT_CLIENT_SECRETS_PATH,
    DOMAIN,
    compute_group_settings_updates,
    ensure_group_exists,
    ensure_group_settings,
    get_credentials,
    get_group_by_email,
    group_display_name,
    group_email,
    is_in_domain,
)
from util.hierarchy_wiki import (
    WIKI_SLUG,
    HierarchyNode,
    ensure_hierarchy_page,
    fetch_hierarchy_page_content,
    iter_nodes,
    parse_hierarchy,
    remove_node,
    render_hierarchy,
)

_LOG_FILE = Path("debug/update_groups_in_google_and_hierarchy_log.csv")
_SCREENSHOT_DIR = Path("debug/update_groups_in_google_and_hierarchy_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)

ELIGIBLE_KINDS = {"circle", "committee", "club"}

# Clubs get a Drive folder, Google Group, and mailing list like any other
# group, but are never added to the Circle Hierarchy wiki page.
HIERARCHY_ELIGIBLE_KINDS = {"circle", "committee"}

QUIT_WORDS = ("q", "quit")


class QuitScript(Exception):
    """Raised when the user asks to quit at any interactive prompt.

    Caught in main(), which stops asking further questions but still saves
    whatever progress was already made (footer links, the hierarchy page)."""


def check_quit(answer: str) -> None:
    """Raise QuitScript if answer is a quit command. Call this on every
    raw input() result before doing anything else with it."""
    if answer.strip().lower() in QUIT_WORDS:
        raise QuitScript()


# ── Data gathering ─────────────────────────────────────────────────────────────

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


def gather_group_email(info: dict) -> str | None:
    """Return the Google Group email implied by a Gather group's mailing
    list, or None if there's no mailing list configured, or what's
    configured doesn't form a well-formed berkeleymoshav.org address.

    Uses the *actual* domain selected in Gather's mailing-list domain
    dropdown (list_domain) — not just an assumed DOMAIN — so a group whose
    list is genuinely configured on a different domain (e.g.
    "care-circle@some-other-domain.com") is caught rather than silently
    treated as "@berkeleymoshav.org" just because that's what we'd default
    to. Also catches the local part itself being malformed (e.g. someone
    pasted a full email into Gather's mailing-list "name" field, which
    should only ever hold the local part). Either way, logged as a warning
    rather than silently constructing/using a bogus or wrong-domain
    address against the Google APIs. If the domain dropdown couldn't be
    read at all (list_domain unknown), DOMAIN is assumed, same as before —
    there's nothing to flag a mismatch against.
    """
    list_name = info.get("list_name")
    if not list_name:
        return None
    list_domain = info.get("list_domain") or DOMAIN
    email = f"{list_name}@{list_domain}"
    if not is_in_domain(email):
        log("WARN", "gather_group_email", info.get("name", ""),
            f"mailing list is {email!r} — not a valid @{DOMAIN} address — skipping")
        return None
    return email


def build_email_by_group_id(
    group_info: dict[str, dict], checked: set[str] | None = None,
) -> dict[str, str]:
    """Return {group_id: email} for every well-formed, in-domain mailing
    list found in group_info.

    `checked` (if given) is a set of group_ids already evaluated (because
    they already had a mailing list at the time) earlier in this run —
    skipped here, and mutated in place to include every group_id newly
    evaluated. Pass the same set across multiple calls (e.g. once before
    ensure_email_lists() assigns new mailing lists, and again after) so a
    group already evaluated — successfully or not — isn't re-derived, and
    its gather_group_email() warning isn't logged twice, just because more
    than one pipeline stage needs the mapping. A group with no mailing
    list yet is never added to `checked`, so it's still picked up on a
    later call once ensure_email_lists() assigns it one.
    """
    if checked is None:
        checked = set()
    result = {}
    for gid, info in group_info.items():
        if gid in checked or not info.get("list_name"):
            continue
        checked.add(gid)
        email = gather_group_email(info)
        if email:
            result[gid] = email
    return result


def fetch_group_info(page, base_url: str) -> tuple[dict[str, dict], set[str], set[str]]:
    """Return ({group_id: {"name", "kind", "url", "list_name", "list_domain"}}, excluded_group_ids,
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
            "list_domain": detail.list_domain,
        }
    return info, excluded_ids, deactivated_ids


def fetch_documents_url_by_group_id(
    page, base_url: str, drive_id: str, config_entries: list[dict], drive_folders: list[dict],
    settings_service, email_by_group_id: dict[str, str],
) -> tuple[dict[str, str], set[str], dict[str, str]]:
    """Return ({group_id: documents_href}, linked_group_ids,
    {group_id: google_file_id}), given the already-scraped /gdrive/config
    entries and an already-walked Shared Drive folder tree (both fetched
    once in main() and reused across steps).

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
      3. The group's own custom footer (Google Groups Settings
         customFooterText), keyed by the group's own email address (via
         email_by_group_id) — populated by ensure_group_footers()/
         add_new_group.py.
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

    documents_url_by_group_id: dict[str, str] = {}
    google_file_id_by_group_id: dict[str, str] = {}
    unresolved: list[dict] = []
    for entry in config_entries:
        gid = entry["group_id"]
        email = email_by_group_id.get(gid)
        google_file_id = (
            entry["google_file_id"]
            or item_id_to_google_file_id.get(entry["item_id"])
        )
        if not google_file_id and email:
            try:
                google_file_id = fetch_footer_folder_id(settings_service, email)
            except Exception as e:
                log("WARN", "documents_link", entry["folder_name"],
                    f"couldn't read footer for {email}: {e}")
        if google_file_id:
            documents_url_by_group_id[gid] = gdrive_item_url(google_file_id)
            google_file_id_by_group_id[gid] = google_file_id
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
                google_file_id_by_group_id[entry["group_id"]] = matches[0]
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

    return documents_url_by_group_id, linked_group_ids, google_file_id_by_group_id


# ── Interactive parent selection ───────────────────────────────────────────────

def prompt_for_parent(group_name: str, all_nodes: list) -> "object | None":
    """Ask the user for a prefix uniquely matching an existing hierarchy
    node's name. Returns the matched node, or None if the user skips."""
    while True:
        prefix = input(
            f"Group '{group_name}' is not in the hierarchy. Enter a prefix "
            f"uniquely matching its parent circle's name (blank to skip, "
            f"'q' to quit): "
        ).strip()
        check_quit(prefix)
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
    answer = input(f"{question} [y/N/q]: ").strip().lower()
    check_quit(answer)
    return answer in ("y", "yes")


# ── Step 1: ensure every eligible group has a Drive folder ────────────────────

def prompt_folder_choice_for_group(
    available: list[dict], taken: list[dict], folder_owner: dict[str, str],
) -> tuple[str, dict | None]:
    """Prompt the user to pick a candidate folder, create a new one, enter
    an existing folder's ID directly, or skip. Returns (action, folder)
    where action is "select", "create", "id", or "skip" (folder is None
    unless action == "select")."""
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
        prompt = (f"  Select folder [1-{len(available)}], 'c' to create a new folder, "
                  f"'i' to enter an existing folder's ID, Enter to skip, or 'q' to quit: ")
    else:
        print("  No candidate folders found.")
        prompt = ("  'c' to create a new folder, 'i' to enter an existing folder's ID, "
                   "Enter to skip, or 'q' to quit: ")

    while True:
        choice = input(prompt).strip()
        check_quit(choice)
        if choice == "":
            return "skip", None
        if choice.lower() == "c":
            return "create", None
        if choice.lower() == "i":
            return "id", None
        if available and choice.isdigit() and 1 <= int(choice) <= len(available):
            return "select", available[int(choice) - 1]
        print("  Invalid choice.")


def create_folder_for_group(
    drive_service, drive_id: str, group_kind: str, group_name: str, dry_run: bool
) -> tuple[str, str] | None:
    """Interactively create a new Drive folder for a group of this kind.
    Defaults to the group's own name if the user enters nothing. Returns
    (folder_id, folder_name), or None if skipped/failed."""
    raw_name = input(f"  Enter the new folder's name [{group_name}]: ").strip()
    check_quit(raw_name)
    if not raw_name:
        raw_name = group_name

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

    try:
        ensure_folder_name_available(drive_service, drive_id, parent_id, folder_name)
    except ValueError as e:
        print(f"  ERROR: {e}")
        return None

    if dry_run:
        print(f"  [dry-run] would create folder '{folder_name}'")
        return None

    folder_id = create_drive_folder(drive_service, folder_name, parent_id)
    print(f"  Created folder '{folder_name}' ({folder_id})")
    return folder_id, folder_name


def prompt_folder_id_for_group(drive_service) -> tuple[str, str] | None:
    """Ask the user for an existing Drive folder's ID directly. Returns
    (folder_id, folder_name), or None if skipped/invalid."""
    folder_id = input("  Enter the existing Drive folder's ID: ").strip()
    check_quit(folder_id)
    if not folder_id:
        print("  No ID entered; skipping.")
        return None

    folder = get_drive_folder_by_id(drive_service, folder_id)
    if folder is None:
        print(f"  ERROR: no folder found with ID {folder_id!r} (or it isn't a folder)")
        return None

    return folder["id"], folder["name"]


def ensure_group_folders(
    page, base_url: str, drive_service, drive_id: str,
    group_info: dict[str, dict], linked_group_ids: set[str],
    documents_url_by_group_id: dict[str, str], google_file_id_by_group_id: dict[str, str],
    drive_folders: list[dict], folder_owner: dict[str, str],
    folder_name_by_group_id: dict[str, str], dry_run: bool,
) -> None:
    """For every eligible group with no Drive folder, interactively find,
    create, or skip one. If resolved, links it on /gdrive/config with
    Content manager access.

    Mutates linked_group_ids, documents_url_by_group_id,
    google_file_id_by_group_id, folder_owner, and folder_name_by_group_id
    in place. Google Group footers aren't touched here — they're synced
    separately in ensure_group_footers(), once every group's folder *and*
    mailing list state is final, since a footer link needs both a folder
    and a group email to be meaningful.
    """
    missing = [
        (gid, info) for gid, info in group_info.items()
        if info["kind"] in ELIGIBLE_KINDS and gid not in linked_group_ids
    ]
    missing.sort(key=lambda item: item[1]["name"].casefold())

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
            result = create_folder_for_group(drive_service, drive_id, kind, group_name, dry_run)
            if result is None:
                continue
            folder_id, folder_name = result
        elif action == "id":
            result = prompt_folder_id_for_group(drive_service)
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

        linked_group_ids.add(group_id)
        documents_url_by_group_id[group_id] = gdrive_item_url(folder_id)
        google_file_id_by_group_id[group_id] = folder_id
        folder_owner[folder_name] = group_id
        folder_name_by_group_id[group_id] = folder_name
        print(f"  Linked '{folder_name}' to '{group_name}'.")
        log("INFO", "ensure_folder", f"{group_name} (id={group_id}) -> '{folder_name}' ({folder_id})")


# ── Step 2: ensure every eligible (foldered) group has a mailing list ─────────

def prompt_group_choice_for_email_list(matches: list[dict]) -> tuple[str, dict | None]:
    """Prompt to select an existing Google Group, create a new one, enter
    an existing group's address directly, or skip. Returns (action, group)
    where action is "select", "create", "address", or "skip" (group is
    None unless action == "select")."""
    if matches:
        print("  Candidate existing Google Groups:")
        for i, g in enumerate(matches, start=1):
            tag = "" if g["match_kind"] == "strict" else " [weak: shared term only]"
            print(f"    {i}. {g['name']} <{g['email']}>{tag}")
        prompt = (f"  Select group [1-{len(matches)}], 'c' to create a new group, "
                  f"'a' to enter an existing group's address, Enter to skip, or 'q' to quit: ")
    else:
        print("  No candidate Google Groups found.")
        prompt = ("  'c' to create a new group, 'a' to enter an existing group's address, "
                   "Enter to skip, or 'q' to quit: ")

    while True:
        choice = input(prompt).strip()
        check_quit(choice)
        if choice == "":
            return "skip", None
        if choice.lower() == "c":
            return "create", None
        if choice.lower() == "a":
            return "address", None
        if matches and choice.isdigit() and 1 <= int(choice) <= len(matches):
            return "select", matches[int(choice) - 1]
        print("  Invalid choice.")


def prompt_group_address_for_email_list(dir_service) -> dict | None:
    """Ask the user for an existing Google Group's email address directly.
    Returns the group dict ({'id', 'email', 'name'}), or None if
    skipped/invalid."""
    gemail = input("  Enter the existing Google Group's email address: ").strip()
    check_quit(gemail)
    if not gemail:
        print("  No address entered; skipping.")
        return None

    group = get_group_by_email(dir_service, gemail)
    if group is None:
        print(f"  ERROR: no Google Group found with address {gemail!r}")
        return None

    if not is_in_domain(group["email"]):
        print(f"  ERROR: {group['email']!r} is not a @{DOMAIN} address — "
              "Gather's mailing list field can only hold a local part, so a "
              "different-domain group can't be recorded correctly here.")
        return None

    return group


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

        matches = find_matching_folders(group_name, all_groups)
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
        elif action == "address":
            group = prompt_group_address_for_email_list(dir_service)
            if group is None:
                continue
            email = group["email"]
        else:
            email = chosen["email"]

        list_local = email.split("@")[0]
        set_gather_group_email_list(page, base_url, group_id, list_local, DOMAIN, dry_run)
        info["list_name"] = list_local
        print(f"  Set mailing list for '{group_name}' to {email}")
        log("INFO", "ensure_email_list", f"{group_name} (id={group_id}) -> {email}")


def ensure_conversation_history(
    settings_service, group_info: dict[str, dict], email_by_group_id: dict[str, str],
    dry_run: bool,
) -> None:
    """Turn on "Conversation history" for every eligible group's associated
    Google Group — whether that group was just created/matched this run or
    already had a mailing list configured from a previous run. Can't be
    folded into ensure_email_lists(): that function only ever visits
    groups with *no* mailing list configured yet, so a group that already
    has one would never be checked there.

    Takes the already-computed email_by_group_id (rather than deriving its
    own via gather_group_email()) so a group with no valid email isn't
    re-evaluated — and re-warned about — on top of the check already done
    when email_by_group_id was built.
    """
    eligible = [
        (gid, info, email_by_group_id[gid]) for gid, info in group_info.items()
        if info["kind"] in ELIGIBLE_KINDS and gid in email_by_group_id
    ]
    eligible.sort(key=lambda item: item[1]["name"].casefold())

    for group_id, info, email in eligible:
        group_name = info["name"]

        if dry_run:
            updates = compute_group_settings_updates(settings_service, email, CONVERSATION_HISTORY_SETTINGS)
            if updates:
                print(f"[dry-run] '{group_name}' ({email}): would turn on Conversation history")
                log("INFO", "would_turn_on_conversation_history", f"{group_name} ({email})")
            continue

        updates = ensure_group_settings(settings_service, email, CONVERSATION_HISTORY_SETTINGS)
        if updates:
            print(f"Turned on Conversation history for '{group_name}' ({email})")
            log("INFO", "conversation_history", f"{group_name} ({email}): turned on")


# ── Step 3: keep each Google Group's footer link in sync with Gather ──────────

def ensure_group_footers(
    settings_service, base_url: str, group_info: dict[str, dict],
    google_file_id_by_group_id: dict[str, str], email_by_group_id: dict[str, str],
    dry_run: bool,
) -> None:
    """Keep each eligible Google Group's custom footer in sync with
    Gather's own state: for every eligible group that currently has both a
    linked Drive folder and a configured mailing list, that pairing
    implies one footer link block (see util/google_group_footer.py),
    added or updated to match. groups_drive_sync.gs reads this footer to
    know which folder to sync a group's membership from.

    A footer is only ever added/updated here, never blanked out — a group
    Gather can't currently derive a pairing for is left alone, and any
    other footer content a human wrote is preserved regardless.
    """
    eligible = [
        (gid, info) for gid, info in group_info.items()
        if info["kind"] in ELIGIBLE_KINDS
        and email_by_group_id.get(gid)
        and google_file_id_by_group_id.get(gid)
    ]
    eligible.sort(key=lambda item: item[1]["name"].casefold())

    for group_id, info in eligible:
        group_name = info["name"]
        email = email_by_group_id[group_id]
        folder_id = google_file_id_by_group_id[group_id]

        if dry_run:
            updates = compute_group_footer_updates(
                settings_service, email, group_id, folder_id, base_url, group_name
            )
            if updates:
                print(f"[dry-run] '{group_name}' ({email}): would update footer link to folder {folder_id}")
                log("INFO", "would_update_footer", f"{group_name} ({email}) -> {folder_id}")
            continue

        updates = ensure_group_footer(settings_service, email, group_id, folder_id, base_url, group_name)
        if updates:
            print(f"  ~ footer: '{group_name}' ({email}) -> folder {folder_id}")
            log("INFO", "update_footer", f"{group_name} ({email}) -> {folder_id}")


# ── Main ──────────────────────────────────────────────────────────────────────

FULL_COMMUNITY_GROUP_NAME = "Full Community"
ROOT_NODE_NAME = "Full Community (Circle of Everyone / Top Circle / HOA)"
ROOT_DOCUMENTS_URL = "/gdrive"


def ensure_root_node(root, group_info: dict[str, dict], dry_run: bool) -> None:
    """Force the hierarchy root's name/links to always be the "everyone"
    group: [Members] -> the "Full Community" Gather group, [Documents] ->
    the root Shared Drive page (/gdrive).

    Unlike every other node, the root isn't matched/renamed by its
    group_id via sync_hierarchy (see the `node is root` skip there) —
    its identity is fixed by this function instead.
    """
    full_community_id = next(
        (gid for gid, info in group_info.items()
         if info["name"].casefold() == FULL_COMMUNITY_GROUP_NAME.casefold()),
        None,
    )
    if full_community_id is None:
        log("WARN", "root_node",
            f"No Gather group named '{FULL_COMMUNITY_GROUP_NAME}' found; "
            "leaving hierarchy root untouched")
        return

    members_url = f"/groups/{full_community_id}"
    changes = []
    if root.name != ROOT_NODE_NAME:
        changes.append(f"name: {root.name!r} -> {ROOT_NODE_NAME!r}")
    if root.members_url != members_url:
        changes.append(f"[Members] -> {members_url}")
    if root.documents_url != ROOT_DOCUMENTS_URL:
        changes.append(f"[Documents] -> {ROOT_DOCUMENTS_URL}")
    if not changes:
        return

    if dry_run:
        print(f"[dry-run] Would update hierarchy root: {'; '.join(changes)}")
        log("INFO", "would_update_root", "; ".join(changes))
        return

    root.name = ROOT_NODE_NAME
    root.group_id = full_community_id
    root.members_url = members_url
    root.documents_url = ROOT_DOCUMENTS_URL
    print(f"Updated hierarchy root: {'; '.join(changes)}")
    log("INFO", "update_root", "; ".join(changes))


def sync_hierarchy(
    root, group_info: dict[str, dict], documents_url_by_group_id: dict[str, str],
    linked_group_ids: set[str], excluded_ids: set[str], deactivated_ids: set[str], dry_run: bool,
) -> None:
    """Mutate the parsed hierarchy tree in place: rename, add/alter/remove
    Documents links, prompt for deletion of stale entries.

    Deactivated groups are removed from the hierarchy outright (no
    prompt, since they're gone for good, not just excluded from
    consideration). Merely-hidden groups are left untouched.

    The root node itself is skipped — its name/links are forced by
    ensure_root_node(), not by the generic rename/link-sync rules here.
    """
    for node in list(iter_nodes(root)):
        if node is root or not node.group_id:
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
        if info["kind"] in HIERARCHY_ELIGIBLE_KINDS and gid not in tree_group_ids
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

        current_content = fetch_hierarchy_page_content(page, base_url)
        root = parse_hierarchy(current_content)

        group_info, excluded_ids, deactivated_ids = fetch_group_info(page, base_url)

        log("INFO", "walk_drive", f"Walking folder tree of Shared Drive {drive_id}…")
        drive_folders = walk_drive_folders(drive_service, drive_id)
        log("INFO", "walk_drive", f"{len(drive_folders)} folder(s) found")

        config_entries = scrape_gdrive_config(page, base_url)
        folder_owner = {e["folder_name"]: e["group_id"] for e in config_entries}
        folder_name_by_group_id = {e["group_id"]: e["folder_name"] for e in config_entries}

        checked_email_ids: set[str] = set()
        email_by_group_id = build_email_by_group_id(group_info, checked_email_ids)

        documents_url_by_group_id, linked_group_ids, google_file_id_by_group_id = (
            fetch_documents_url_by_group_id(
                page, base_url, drive_id, config_entries, drive_folders,
                settings_service, email_by_group_id,
            )
        )

        quit_requested = False

        # Step 1: ensure every eligible group has a Drive folder.
        try:
            ensure_group_folders(
                page, base_url, drive_service, drive_id, group_info, linked_group_ids,
                documents_url_by_group_id, google_file_id_by_group_id, drive_folders,
                folder_owner, folder_name_by_group_id, dry_run,
            )
        except QuitScript:
            quit_requested = True
            print("\nQuit requested — skipping remaining steps and saving progress so far.")
            log("INFO", "quit", "User quit during the Drive folder step")

        # Step 2: ensure every eligible, now-foldered group has a mailing list.
        if not quit_requested:
            try:
                ensure_email_lists(
                    page, base_url, dir_service, settings_service, group_info, linked_group_ids,
                    folder_name_by_group_id, dry_run,
                )
            except QuitScript:
                quit_requested = True
                print("\nQuit requested — skipping remaining steps and saving progress so far.")
                log("INFO", "quit", "User quit during the mailing list step")

        # Pick up any mailing lists newly assigned by ensure_email_lists() above
        # (checked_email_ids keeps this from re-deriving/re-warning about groups
        # already evaluated before Step 1).
        email_by_group_id.update(build_email_by_group_id(group_info, checked_email_ids))

        # Turn on "Conversation history" for every eligible group's Google Group,
        # whether it was just newly associated above or already had one.
        ensure_conversation_history(settings_service, group_info, email_by_group_id, dry_run)

        # Step 3: keep each Google Group's footer link in sync with Gather.
        ensure_group_footers(
            settings_service, base_url, group_info, google_file_id_by_group_id,
            email_by_group_id, dry_run,
        )

        if not quit_requested:
            try:
                ensure_root_node(root, group_info, dry_run)
                sync_hierarchy(
                    root, group_info, documents_url_by_group_id, linked_group_ids,
                    excluded_ids, deactivated_ids, dry_run
                )
                add_missing_groups(root, group_info, documents_url_by_group_id, dry_run)
            except QuitScript:
                quit_requested = True
                print("\nQuit requested — saving progress made so far.")
                log("INFO", "quit", "User quit during the hierarchy sync step")

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
