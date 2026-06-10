#!/usr/bin/env python3
"""
Syncs Gather group memberships from a Google Sheets member roster.

For each row where Status starts with "Member" and CIRCLE MEMBERSHIP contains
"BM" (community member), the script ensures the person belongs to exactly the
other circles listed in that column.  Members found in a managed group who are
not listed for that group are removed.  Only groups that appear in the sheet
are managed; other groups are left untouched.

Usage:
    python sync_memberships.py
    python sync_memberships.py -n            # dry-run
    python sync_memberships.py -s SHEET_URL  # override sheet
"""

import argparse
import csv
import io
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from credentials import load_credentials
from gather_utils import (
    GatherGroup,
    GatherUser,
    _add_inline_member,
    _check_submit_errors,
    _fetch_group_detail,
    _remove_inline_member,
    close_log,
    configure,
    fetch_all_gather_groups,
    fetch_all_gather_users,
    fetch_sheet,
    first_name_matches,
    init_log,
    launch_browser,
    log,
    login,
    screenshot,
)


DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "12f7D5JEnCuc5rsQavVQLftEoQVC8jCgpdGnqH3yrkRk/edit?gid=2035578656#gid=2035578656"
)

_LOG_FILE = Path("memberships_log.csv")
_SCREENSHOT_DIR = Path("import_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


# ── Group name matching ───────────────────────────────────────────────────────

_ACRONYM_EXPANSIONS: dict[str, str] = {
    "P & G": "Process & Governance",
    "D, F, & L": "Development, Finance, & Legal",
    "CLC": "Community Life Circle",
}

_GROUP_ALIASES: list[frozenset] = [
    frozenset({"tech", "technology"}),
    frozenset({"landscape", "landscaping"}),
]

_SUFFIX_RE = re.compile(
    r"\s+(Circle|Team|Working Group|Work Group|Group|Committee)\s*$",
    re.IGNORECASE,
)


def _normalize_group(name: str) -> str:
    name = _ACRONYM_EXPANSIONS.get(name.strip(), name.strip())
    name = re.sub(r"\s*\([^)]*\)?\s*$", "", name).strip()
    name = _SUFFIX_RE.sub("", name).strip().lower()
    name = re.sub(r"\s*&\s*", " and ", name)
    name = name.replace(",", "")
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\bwork group\b", "working group", name)
    for alias_set in _GROUP_ALIASES:
        canonical = min(alias_set)
        for alias in alias_set:
            if alias != canonical:
                name = re.sub(r"\b" + re.escape(alias) + r"\b", canonical, name)
    return name


def group_names_match(a: str, b: str) -> bool:
    return _normalize_group(a) == _normalize_group(b)


# ── Sheet parsing ─────────────────────────────────────────────────────────────

def _find_header_row(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows):
        if any(cell.strip() == "First Name" for cell in row):
            return i
    raise ValueError("Header row not found (no 'First Name' cell)")


def parse_memberships(csv_text: str) -> list[dict]:
    """Return one dict per BM member-status row.

    Each dict has: first_name, last_name, email, groups (non-BM circle names).
    Rows where Status doesn't start with "member" (case-insensitive) are skipped.
    Rows where CIRCLE MEMBERSHIP does not contain "BM" are skipped.
    """
    all_rows = list(csv.reader(io.StringIO(csv_text)))
    header_idx = _find_header_row(all_rows)
    fieldnames = [cell.strip() for cell in all_rows[header_idx]]

    results = []
    for raw in all_rows[header_idx + 1:]:
        row = {k: v.strip() for k, v in zip(fieldnames, raw) if k}
        if not row.get("Status", "").lower().startswith("member"):
            continue
        memberships_raw = row.get("CIRCLE MEMBERSHIP", "")
        entries = [e.strip() for e in memberships_raw.split(";") if e.strip()]
        if not any(e.upper() == "BM" for e in entries):
            continue
        groups = [e for e in entries if e.upper() != "BM"]
        results.append({
            "first_name": row.get("First Name", "").strip(),
            "last_name": row.get("Last Name", "").strip(),
            "email": row.get("Email Address", "").strip().lower(),
            "groups": groups,
        })
    return results


# ── User resolution ───────────────────────────────────────────────────────────

def resolve_user(
    entry: dict,
    email_index: dict[str, GatherUser],
    gather_users: list[GatherUser],
    warnings: list[str],
) -> GatherUser | None:
    """Find the GatherUser for a sheet row.

    Tries email first; falls back to first+last name.
    """
    email = entry["email"]
    if email and email in email_index:
        return email_index[email]

    first, last = entry["first_name"], entry["last_name"]
    candidates = [
        u for u in gather_users
        if first_name_matches(first, u.first_name)
        and u.last_name.strip().lower() == last.strip().lower()
    ]
    if len(candidates) == 1:
        if email:
            warnings.append(
                f"Matched {first} {last} by name (email {email!r} not in Gather)"
            )
        return candidates[0]
    if len(candidates) > 1:
        warnings.append(
            f"Ambiguous name match for {first} {last}: "
            f"{[u.full_name for u in candidates]}"
        )
    else:
        warnings.append(
            f"No Gather user found for {first} {last}"
            + (f" <{email}>" if email else "")
        )
    return None


# ── Group sync ────────────────────────────────────────────────────────────────

def _submit_group_form(page, group_name: str) -> bool:
    page.locator('input[name="commit"]').click()
    page.wait_for_load_state("networkidle")
    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"membership_err_{group_name[:20]}")
        log("ERROR", "group_form", group_name, err[:200])
        return False
    return True


def sync_group_members(
    page,
    base_url: str,
    group: GatherGroup,
    desired_ids: set[str],
    dry_run: bool,
    users_by_id: dict[str, GatherUser],
) -> dict[str, int]:
    """Sync one group's membership to match desired_ids exactly.

    Returns {'added': N, 'removed': N, 'failed': N}.
    """
    stats = {"added": 0, "removed": 0, "failed": 0}
    try:
        detail = _fetch_group_detail(page, base_url, group)
    except Exception as e:
        log("ERROR", "fetch_detail", group.name, str(e))
        stats["failed"] += 1
        return stats

    current_ids = {m.user_id for m in detail.members}
    to_add = desired_ids - current_ids
    to_remove = current_ids - desired_ids

    if not to_add and not to_remove:
        log("INFO", "group", f"Up to date: {group.name}")
        return stats

    if dry_run:
        for uid in sorted(to_add):
            u = users_by_id.get(uid)
            log("DRY-RUN", "add_member", f"{u.full_name if u else uid} → {group.name}")
            stats["added"] += 1
        for uid in sorted(to_remove):
            u = users_by_id.get(uid)
            log("DRY-RUN", "remove_member", f"{u.full_name if u else uid} ← {group.name}")
            stats["removed"] += 1
        return stats

    try:
        page.goto(f"{base_url}/groups/{group.group_id}/edit", wait_until="networkidle")

        for uid in to_add:
            user = users_by_id.get(uid)
            if not user:
                log("WARN", "add_member", f"user_id={uid} not in Gather users; skipping")
                stats["failed"] += 1
                continue
            _add_inline_member(page, user, is_manager=False)
            log("INFO", "add_member", f"{user.full_name} → {group.name}")
            stats["added"] += 1

        for uid in to_remove:
            user = users_by_id.get(uid)
            name = user.full_name if user else uid
            _remove_inline_member(page, uid)
            log("INFO", "remove_member", f"{name} ← {group.name}")
            stats["removed"] += 1

        if not _submit_group_form(page, group.name):
            stats["failed"] += 1

    except Exception as e:
        screenshot(page, f"membership_exc_{group.name[:20]}")
        log("ERROR", "sync_group", group.name, str(e))
        stats["failed"] += 1

    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    sheet_url: str, base_url: str, email: str, password: str, dry_run: bool = False
):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"sheet_url={sheet_url} base_url={base_url} dry_run={dry_run}")

    csv_text = fetch_sheet(sheet_url)
    sheet_members = parse_memberships(csv_text)
    log("INFO", "parse_sheet", f"{len(sheet_members)} BM members found")

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

        gather_users = fetch_all_gather_users(page, base_url)
        log("INFO", "fetch_users", f"{len(gather_users)} Gather users loaded")

        gather_groups = fetch_all_gather_groups(page, base_url)
        log("INFO", "fetch_groups", f"{len(gather_groups)} Gather groups loaded")

        email_index: dict[str, GatherUser] = {
            u.email: u for u in gather_users if u.email
        }
        users_by_id: dict[str, GatherUser] = {u.user_id: u for u in gather_users}
        warnings: list[str] = []

        # desired_by_norm: normalized group name → set of user_ids
        desired_by_norm: dict[str, set[str]] = {}
        for entry in sheet_members:
            user = resolve_user(entry, email_index, gather_users, warnings)
            if not user:
                continue
            for group_name in entry["groups"]:
                norm = _normalize_group(group_name)
                desired_by_norm.setdefault(norm, set()).add(user.user_id)

        # Map normalized name → GatherGroup for groups that appear in the sheet
        managed: dict[str, GatherGroup] = {}
        for gg in gather_groups:
            norm = _normalize_group(gg.name)
            if norm in desired_by_norm and norm not in managed:
                managed[norm] = gg

        for norm in desired_by_norm:
            if norm not in managed:
                warnings.append(f"Group from sheet not found in Gather: {norm!r}")

        for w in warnings:
            log("WARN", "resolve", w)

        total = {"added": 0, "removed": 0, "failed": 0}
        for norm, user_ids in desired_by_norm.items():
            gg = managed.get(norm)
            if not gg:
                continue
            stats = sync_group_members(page, base_url, gg, user_ids, dry_run, users_by_id)
            for k in total:
                total[k] += stats[k]

        log("INFO", "done", str(total))
        browser.close()

    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Sync Gather group memberships from a spreadsheet",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-s", "--sheet-url", default=DEFAULT_SHEET_URL,
        help="Google Sheets URL or local file path",
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument("-e", "--email", help="Admin login email")
    parser.add_argument("-p", "--password", help="Admin login password")
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would happen without making changes",
    )
    args = parser.parse_args()

    if not args.email or not args.password:
        creds_email, creds_password = load_credentials()
        args.email = args.email or creds_email
        args.password = args.password or creds_password

    main(args.sheet_url, args.base_url, args.email, args.password, args.dry_run)


if __name__ == "__main__":
    cli()
