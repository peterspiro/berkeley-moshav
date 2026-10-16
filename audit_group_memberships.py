#!/usr/bin/env python3
"""
Reports inconsistencies between each Gather group's membership, the
permissions on its linked Google Drive item, and its Google Group's
membership. Read-only — changes nothing.

The three are meant to agree through two syncs, each of which can lag or
fail:

    Gather group members --(Gather's Drive sync)--> Drive item permissions
    Drive item permissions --(groups_drive_sync.gs, hourly)--> Google Group

where a folder's membership is its "Content manager" (fileOrganizer) users
and a shared drive's is its "Viewer" (reader) users — the same rule
groups_drive_sync.gs uses.

For every circle/working group/club in Gather (hidden and deactivated
groups skipped), it reports:

  * Setup problems: no linked folder/shared drive (or one whose Drive ID
    can't be resolved, or more than one); no mailing list, a malformed
    one, or one naming a Google Group that doesn't exist; a Google Group
    footer that's missing its Drive link or links to a different item than
    /gdrive/config does (so groups_drive_sync.gs copies the wrong members).
  * Membership differences: every person missing from at least one of the
    three, with a ✓/✗ per source. People are matched by email address —
    a Gather user by both their Email and Google Email (the address Gather
    shares Drive items with) — with Gmail's ignored dots and "+suffix"
    treated as the same address.
  * Other access: anyone else with a role on the Drive item who isn't a
    member of the Gather group (and so isn't synced to its list) — e.g. a
    shared drive's Managers.

For a folder, only access granted on the folder itself counts; access
inherited from the shared drive is ignored throughout.

Groups with nothing to report are left out.

An "everybody" group's edit page lists no members, so its membership is
approximated as every adult in the Gather directory with an email address.
Gather opt-outs aren't known, so someone missing from both Drive and the
Google Group there is marked "may have opted out".

groups_drive_sync.gs never removes the account it runs as from a group;
pass --ignore with that address (repeatable) to leave such accounts out.

Requires Google API access (Drive, Admin Directory, Groups Settings) —
see update_groups_in_google_and_hierarchy.py's docstring for setup.

Usage:
    python audit_group_memberships.py
    python audit_group_memberships.py -g landscape
    python audit_group_memberships.py --ignore admin@berkeleymoshav.org
"""

import argparse
import functools
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from update_groups_in_google_and_hierarchy import (
    ELIGIBLE_KINDS,
    EVERYBODY_AVAILABILITY,
    _group_is_deactivated,
    _group_is_hidden,
    fetch_documents_url_by_group_id,
    gather_group_email,
)
from util.credentials import load_credentials
from util.gather_utils import (
    GatherUser,
    _fetch_group_detail,
    close_log,
    configure,
    fetch_all_gather_groups,
    fetch_all_gather_users,
    init_log,
    launch_browser,
    log,
    login,
    resolve_group_by_prefix,
)
from util.gdrive_config import (
    DEFAULT_DRIVE_ID,
    list_shared_drives,
    scrape_gdrive_config,
    walk_drive_folders,
)
from util.google_group_footer import ITEM_TYPE_FOLDER, ITEM_TYPE_SHARED_DRIVE, fetch_footer_link
from util.google_group_utils import (
    DEFAULT_CLIENT_SECRETS_PATH,
    get_credentials,
    get_group_by_email,
    list_group_members,
)

_LOG_FILE = Path("debug/audit_group_memberships_log.csv")
_SCREENSHOT_DIR = Path("debug/audit_group_memberships_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)

# Drive role whose holders define an item's membership (matches
# groups_drive_sync.gs's SYNC_ROLE_FOR_TYPE).
SYNC_ROLE_FOR_TYPE = {
    ITEM_TYPE_FOLDER: "fileOrganizer",
    ITEM_TYPE_SHARED_DRIVE: "reader",
}

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}

CHECK, CROSS, NA = "✓", "✗", "–"

# Drive API role -> the name Drive's sharing dialog uses for it, lowest
# access first (the order is used to pick a person's highest direct role).
ROLE_LABELS = {
    "reader": "Viewer",
    "commenter": "Commenter",
    "writer": "Contributor",
    "fileOrganizer": "Content manager",
    "organizer": "Manager",
    "owner": "Owner",
}
_ROLE_RANK = list(ROLE_LABELS)


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def normalize_email(email: str) -> str:
    """Lowercase, and for Gmail addresses drop the dots and any "+suffix"
    in the local part (Gmail ignores both), so equivalent spellings of one
    address compare equal."""
    email = (email or "").strip().lower()
    local, sep, domain = email.partition("@")
    if not sep:
        return email
    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class GroupState:
    """Everything gathered about one Gather group. A source that couldn't be
    read is None (e.g. drive_members when no Drive item is linked) and is
    left out of the comparison, shown as "–"."""
    group_id: str
    name: str
    everybody: bool
    gather_member_ids: set[str]
    list_email: str | None = None
    item_label: str | None = None
    # Addresses holding the item's sync role.
    drive_members: list[str] | None = None
    # (address, role) for every user with access to the item (for a folder,
    # access granted on the folder itself) — for the other-role notes and
    # the "Other access" list.
    drive_access: list[tuple[str, str]] = field(default_factory=list)
    google_members: list[str] | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class PersonRow:
    label: str
    in_gather: bool
    in_drive: bool | None
    in_google: bool | None
    notes: list[str] = field(default_factory=list)


# ── Comparison (pure) ────────────────────────────────────────────────────────

class UserIndex:
    """Looks up Gather users by any of their (normalized) addresses."""

    def __init__(self, users: list[GatherUser]):
        self.by_id = {u.user_id: u for u in users}
        self.by_address: dict[str, GatherUser] = {}
        for u in users:
            for addr in (u.email, u.google_email):
                if addr:
                    self.by_address.setdefault(normalize_email(addr), u)
        self.adults_with_email = [
            u for u in users if not u.child and (u.email or u.google_email)
        ]


def _person_key(user: GatherUser) -> str:
    return f"user:{user.user_id}"


def _user_label(user: GatherUser) -> str:
    addr = user.google_email or user.email
    return f"{user.full_name} <{addr}>" if addr else user.full_name


def compare_group(
    state: GroupState, users: UserIndex, ignore: set[str],
) -> tuple[list[PersonRow], list[tuple[str, str]]]:
    """Return (rows, other_access).

    rows: one for every person missing from at least one of the group's
    readable sources (Gather, Drive, Google Group), sorted by label.
    Addresses belonging to a Gather user are merged into that user's row;
    others get a row of their own.

    other_access: (label, role label) for everyone else with access to the
    Drive item — not a member of the Gather group, not in the Google Group,
    and without the sync role — sorted by label.

    Addresses in `ignore` (normalized) are left out of both."""
    # key -> {"label", "user", "address", "gather", "drive", "google"}
    # ("address" is set only for a row not belonging to a Gather user)
    people: dict[str, dict] = {}

    def entry_for_address(email: str) -> dict | None:
        norm = normalize_email(email)
        if norm in ignore:
            return None
        user = users.by_address.get(norm)
        key = _person_key(user) if user else f"addr:{norm}"
        if key not in people:
            people[key] = {"label": _user_label(user) if user else email.lower(),
                           "user": user, "address": None if user else email,
                           "gather": False, "drive": False, "google": False}
        return people[key]

    if state.everybody:
        gather_users = users.adults_with_email
    else:
        gather_users = []
        for uid in state.gather_member_ids:
            user = users.by_id.get(uid)
            if user is None:
                people[f"id:{uid}"] = {"label": f"Gather user #{uid}", "user": None,
                                       "gather": True, "drive": False, "google": False}
            else:
                gather_users.append(user)
    for user in gather_users:
        if any(normalize_email(a) in ignore for a in (user.email, user.google_email) if a):
            continue
        key = _person_key(user)
        people.setdefault(key, {"label": _user_label(user), "user": user, "gather": False,
                                "drive": False, "google": False})
        people[key]["gather"] = True

    for email in state.drive_members or []:
        entry = entry_for_address(email)
        if entry is not None:
            entry["drive"] = True

    for email in state.google_members or []:
        entry = entry_for_address(email)
        if entry is not None:
            entry["google"] = True

    drive_roles = {normalize_email(e): r for e, r in state.drive_access}

    # Everyone else with access to the item gets no row (they're in none of
    # the three), so list them separately.
    other_access = []
    for email, role in state.drive_access:
        norm = normalize_email(email)
        if norm in ignore:
            continue
        user = users.by_address.get(norm)
        if (_person_key(user) if user else f"addr:{norm}") in people:
            continue
        other_access.append((_user_label(user) if user else email.lower(), role_label(role)))
    other_access.sort(key=lambda item: item[0].casefold())

    has_drive = state.drive_members is not None
    has_google = state.google_members is not None
    rows = []
    for p in people.values():
        present = [p["gather"]]
        if has_drive:
            present.append(p["drive"])
        if has_google:
            present.append(p["google"])
        if all(present):
            continue

        notes = []
        user = p["user"]
        if has_drive and not p["drive"]:
            addresses = ([user.google_email, user.email] if user is not None
                         else [p.get("address")])
            other_role = _other_drive_role(addresses, drive_roles)
            if other_role:
                notes.append(f"has Drive role {role_label(other_role)} instead")
            elif p["gather"] and user is not None and not user.google_email:
                notes.append("no Google email in Gather")
        # (Not when a different Drive role already explains the gap.)
        if (state.everybody and p["gather"] and not any("Drive role" in n for n in notes)
                and not (has_drive and p["drive"]) and not (has_google and p["google"])):
            notes.append("may have opted out")
        rows.append(PersonRow(
            label=p["label"], in_gather=p["gather"],
            in_drive=p["drive"] if has_drive else None,
            in_google=p["google"] if has_google else None,
            notes=notes,
        ))
    rows.sort(key=lambda r: r.label.casefold())
    return rows, other_access


def _other_drive_role(addresses: list[str | None], drive_roles: dict[str, str]) -> str | None:
    """Return the Drive role held by the first of `addresses` that has one."""
    for addr in addresses:
        if addr:
            role = drive_roles.get(normalize_email(addr))
            if role:
                return role
    return None


def format_group_report(
    state: GroupState, rows: list[PersonRow], other_access: list[tuple[str, str]] = (),
) -> str:
    """Render one group's problems, membership differences, and other access."""
    details = [d for d in (state.list_email, state.item_label) if d]
    lines = [state.name + (f"   ({' · '.join(details)})" if details else "")]
    for problem in state.problems:
        lines.append(f"  ! {problem}")
    if rows:
        if state.everybody:
            lines.append("  Membership approximated from the Gather directory "
                         "(adults with an email; opt-outs not known)")

        def mark(v: bool | None) -> str:
            return NA if v is None else (CHECK if v else CROSS)

        width = max(len("Person"), *(len(r.label) for r in rows))
        lines.append(f"  {'Person':<{width}}  Gather  Drive  Google Group")
        for r in rows:
            line = (f"  {r.label:<{width}}  {mark(r.in_gather):^6}  {mark(r.in_drive):^5}  "
                    f"{mark(r.in_google):^12}")
            if r.notes:
                line += "  " + "; ".join(r.notes)
            lines.append(line.rstrip())
    if other_access:
        lines.append("  Other access (not Gather members, not synced to the list):")
        width = max(len(label) for label, _ in other_access)
        for label, role in other_access:
            lines.append(f"    {label:<{width}}  {role}")
    return "\n".join(lines)


# ── Data gathering ────────────────────────────────────────────────────────────

def direct_role(permission: dict) -> str | None:
    """Return the highest role a permission grants on the item itself, or
    None if all of its access is inherited from the shared drive. Items
    outside a shared drive carry no permissionDetails, so their role is
    taken as is."""
    details = permission.get("permissionDetails")
    if not details:
        return permission.get("role")
    roles = [d.get("role") for d in details if not d.get("inherited")]
    if not roles:
        return None
    return max(roles, key=lambda r: _ROLE_RANK.index(r) if r in _ROLE_RANK else -1)


def fetch_drive_access(drive_service, item_id: str, item_type: str) -> list[tuple[str, str]]:
    """Return (address, role) for every user with access to the item. For a
    folder, only access granted on the folder itself counts (see
    direct_role()); a shared drive's own permissions are taken as is."""
    access: list[tuple[str, str]] = []
    page_token = None
    while True:
        resp = drive_service.permissions().list(
            fileId=item_id,
            supportsAllDrives=True,
            # Listing a shared drive's own members needs domain-admin access.
            useDomainAdminAccess=(item_type == ITEM_TYPE_SHARED_DRIVE),
            fields="nextPageToken,permissions(emailAddress,role,type,"
                   "permissionDetails(role,inherited))",
            pageToken=page_token,
        ).execute()
        for p in resp.get("permissions", []):
            if p.get("type") != "user" or not p.get("emailAddress"):
                continue
            role = direct_role(p) if item_type == ITEM_TYPE_FOLDER else p.get("role")
            if role:
                access.append((p["emailAddress"].lower(), role))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return access


def main(base_url: str, email: str, password: str, credentials_path: str,
         drive_id: str, group_prefix: str | None, ignore: list[str]) -> None:
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} group={group_prefix!r} ignore={ignore}")
    ignore_set = {normalize_email(a) for a in ignore}

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

        users = UserIndex(fetch_all_gather_users(page, base_url))

        all_groups = fetch_all_gather_groups(page, base_url)
        if group_prefix:
            try:
                all_groups = [resolve_group_by_prefix(all_groups, group_prefix)]
            except ValueError as e:
                close_log()
                browser.close()
                sys.exit(f"Error: {e}")

        states: dict[str, GroupState] = {}
        info_by_id: dict[str, dict] = {}
        for group in all_groups:
            detail = _fetch_group_detail(page, base_url, group)
            if _group_is_deactivated(page, detail) or _group_is_hidden(page, detail):
                if group_prefix:
                    close_log()
                    browser.close()
                    sys.exit(f"Error: '{detail.name}' is hidden or deactivated.")
                continue
            if (detail.kind or "").strip().lower() not in ELIGIBLE_KINDS:
                continue
            availability = (detail.availability or "").strip().lower()
            states[group.group_id] = GroupState(
                group_id=group.group_id,
                name=detail.name,
                everybody=(availability == EVERYBODY_AVAILABILITY),
                gather_member_ids={m.user_id for m in detail.members},
            )
            info_by_id[group.group_id] = {"name": detail.name, "list_name": detail.list_name,
                                          "list_domain": detail.list_domain}
        log("INFO", "fetch_groups", f"{len(states)} group(s) to audit")

        # Mailing lists.
        email_by_group_id: dict[str, str] = {}
        for gid, info in info_by_id.items():
            state = states[gid]
            if not info["list_name"]:
                state.problems.append("no mailing list configured in Gather")
                continue
            list_email = gather_group_email(info)
            if not list_email:
                state.problems.append(
                    f"mailing list {info['list_name']}@{info['list_domain'] or '?'} "
                    "isn't a valid berkeleymoshav.org address")
                continue
            state.list_email = list_email
            email_by_group_id[gid] = list_email

        # Linked Drive items.
        config_entries = [e for e in scrape_gdrive_config(page, base_url)
                          if e["group_id"] in states]
        entries_by_group: dict[str, list[dict]] = {}
        for entry in config_entries:
            entries_by_group.setdefault(entry["group_id"], []).append(entry)
        multi_linked = {gid for gid, es in entries_by_group.items() if len(es) > 1}
        for gid in multi_linked:
            names = ", ".join(repr(e["folder_name"]) for e in entries_by_group[gid])
            states[gid].problems.append(f"linked to more than one Drive item ({names})")

        @functools.cache
        def get_drive_folders() -> list[dict]:
            return walk_drive_folders(drive_service, drive_id)

        @functools.cache
        def get_shared_drives() -> list[dict]:
            return list_shared_drives(drive_service)

        _, linked_ids, file_id_by_gid, type_by_gid = fetch_documents_url_by_group_id(
            page, base_url, drive_id,
            [e for e in config_entries if e["group_id"] not in multi_linked],
            get_drive_folders, get_shared_drives, settings_service, email_by_group_id,
        )

        browser.close()

    for gid, state in states.items():
        if gid in multi_linked:
            continue
        if gid not in linked_ids:
            state.problems.append("no linked folder or shared drive")
            continue
        entry = entries_by_group[gid][0]
        item_type = type_by_gid.get(gid, entry["item_type"])
        kind = "shared drive" if item_type == ITEM_TYPE_SHARED_DRIVE else "folder"
        state.item_label = f"{kind} {entry['folder_name']!r}"
        item_id = file_id_by_gid.get(gid)
        if not item_id:
            state.problems.append(f"couldn't find the Drive ID of linked {kind} "
                                  f"{entry['folder_name']!r}")
            continue
        try:
            state.drive_access = fetch_drive_access(drive_service, item_id, item_type)
            sync_role = SYNC_ROLE_FOR_TYPE[item_type]
            state.drive_members = [e for e, r in state.drive_access if r == sync_role]
        except Exception as e:
            state.problems.append(f"couldn't read the {kind}'s permissions: {e}")

    # Google Groups and their footers.
    for gid, list_email in email_by_group_id.items():
        state = states[gid]
        if get_group_by_email(dir_service, list_email) is None:
            state.problems.append(f"Google Group {list_email} doesn't exist")
            continue
        state.google_members = [m["email"] for m in list_group_members(dir_service, list_email)]
        try:
            link = fetch_footer_link(settings_service, list_email)
        except ValueError as e:
            state.problems.append(f"footer: {e}")
            continue
        expected = file_id_by_gid.get(gid)
        if link is None:
            state.problems.append("Google Group footer has no Drive link, so "
                                  "groups_drive_sync.gs doesn't sync it")
        elif expected and link[0] != expected:
            state.problems.append(
                f"footer links to Drive item {link[0]}, but /gdrive/config links "
                f"{expected} — groups_drive_sync.gs syncs from the footer's item")

    reported = 0
    for state in sorted(states.values(), key=lambda s: s.name.casefold()):
        rows, other_access = compare_group(state, users, ignore_set)
        if not state.problems and not rows and not other_access:
            continue
        reported += 1
        print()
        print(format_group_report(state, rows, other_access))

    print()
    if reported:
        print(f"{reported} of {len(states)} group(s) have inconsistencies.")
    else:
        print(f"No inconsistencies found in {len(states)} group(s).")
    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Report inconsistencies between Gather group membership, Google "
                    "Drive item permissions, and Google Group membership (read-only)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-g", "--group", metavar="PREFIX", default=None,
        help="Audit only the Gather group whose name starts with this prefix "
             "(case-insensitive; must match exactly one group)",
    )
    parser.add_argument(
        "--ignore", metavar="EMAIL", action="append", default=[],
        help="Leave this address out of the comparison (repeatable) — e.g. the "
             "account groups_drive_sync.gs runs as, which it never removes",
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-c", "--credentials", default=str(DEFAULT_CLIENT_SECRETS_PATH),
        help="Path to OAuth client secrets JSON",
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
    main(args.base_url, email, password, args.credentials, args.drive_id,
         args.group, args.ignore)


if __name__ == "__main__":
    cli()
