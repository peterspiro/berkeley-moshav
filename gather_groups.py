#!/usr/bin/env python3
"""
Populates Gather groups from a Google Sheets circle hierarchy.

Usage:
    python gather_groups.py -u https://sub.gather.coop -e admin@example.com -p secret
    python gather_groups.py -u https://sub.gather.coop -e admin@example.com -p secret -n
    python gather_groups.py -u https://sub.gather.coop -e admin@example.com -p secret \
        -s https://docs.google.com/spreadsheets/d/SHEET_ID/edit?gid=0
"""

import argparse
import csv
import datetime
import io
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright


# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1pgiAffsTAkOv68zVANaE5Skc73gdcFY8BdOZCDAD-Ak/edit?gid=0#gid=0"
)

MAX_DESC = 255
WIKI_TITLE = "Circle Hierarchy"
WIKI_SLUG = "circle-hierarchy"
LOG_FILE = "groups_log.csv"
SCREENSHOT_DIR = Path("import_screenshots")

ACRONYM_EXPANSIONS: dict[str, str] = {
    "P & G": "Process & Governance",
    "D, F, & L": "Development, Finance, & Legal",
    "CLC": "Community Life Circle",
}

# Each frozenset is a group of equivalent names (case-insensitive)
GROUP_NAME_ALIASES: list[frozenset] = [
    frozenset({"tech", "technology"}),
]

# Each frozenset is a group of equivalent first names (case-insensitive)
FIRST_NAME_ALIASES: list[frozenset] = [
    frozenset({"katie", "kathryn"}),
]

# col_index → Gather group kind value
GROUP_KINDS: dict[int, str] = {0: "circle", 1: "circle", 2: "circle"}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Circle:
    raw_name: str
    name: str               # acronym-expanded
    col_index: int          # 0, 1, or 2
    parent_name: Optional[str]
    member_lines: list[str]
    lead_lines: list[str]
    consultant_text: str
    meetings: str
    description: str        # Domain column
    aim: str
    qualifications: str


@dataclass
class GatherUser:
    user_id: str
    first_name: str
    last_name: str
    full_name: str


@dataclass
class GatherGroupMember:
    user_id: str
    is_manager: bool


@dataclass
class GatherGroup:
    group_id: str
    name: str
    kind: str
    availability: str
    description: str
    members: list[GatherGroupMember] = field(default_factory=list)


# ── Logging ───────────────────────────────────────────────────────────────────

_log_writer = None
_log_file_handle = None


def init_log():
    global _log_writer, _log_file_handle
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    _log_file_handle = open(LOG_FILE, "a", newline="")
    _log_writer = csv.writer(_log_file_handle)
    if Path(LOG_FILE).stat().st_size == 0:
        _log_writer.writerow(["timestamp", "level", "action", "detail", "error"])


def log(level: str, action: str, detail: str = "", error: str = ""):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {level:7s} {action}: {detail}" + (f" — {error}" if error else ""))
    if _log_writer:
        _log_writer.writerow([ts, level, action, detail, error])
        _log_file_handle.flush()


def close_log():
    if _log_file_handle:
        _log_file_handle.close()


# ── Browser helpers ───────────────────────────────────────────────────────────

def screenshot(page: Page, name: str):
    path = SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path))
        log("DEBUG", "screenshot", str(path))
    except Exception:
        pass


def _check_submit_errors(page: Page) -> str | None:
    if page.locator(".error, #error_explanation, .alert-danger").count() > 0:
        return page.locator(".error, #error_explanation, .alert-danger").first.inner_text()
    return None


def select2_choose(page: Page, field_selector: str, search_text: str):
    container = page.locator(
        f"{field_selector} ~ .select2-container, "
        f"{field_selector} + .select2-container"
    )
    if container.count() > 0:
        container.first.click()
    else:
        page.locator(".select2-container").last.click()

    search_input = page.locator(".select2-search__field").last
    search_input.wait_for(state="visible", timeout=5000)
    search_input.fill("")
    search_input.type(search_text)

    option = page.locator(".select2-results__option").filter(has_text=search_text).first
    option.wait_for(state="visible", timeout=8000)
    option.click()


def login(page: Page, base_url: str, email: str, password: str):
    page.goto(f"{base_url}/people/users/sign-in", wait_until="networkidle")
    if "sign-in" not in page.url and "sign_in" not in page.url:
        log("INFO", "login", "Already signed in")
        return
    page.fill('input[name="user[email]"]', email)
    page.fill('input[name="user[password]"]', password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")
    if "sign-in" in page.url or "sign_in" in page.url:
        screenshot(page, "login_failed")
        raise RuntimeError(f"Login failed — still on {page.url}")
    log("INFO", "login", f"Signed in as {email}")


# ── Sheet URL helpers ─────────────────────────────────────────────────────────

def to_csv_export_url(url: str) -> str:
    """Convert any Google Sheets URL to a CSV export URL."""
    m = re.search(r"spreadsheets/d/([^/?#\s]+)", url)
    if not m:
        return url
    sheet_id = m.group(1)
    gid_m = re.search(r"[#&?]gid=(\d+)", url)
    gid = gid_m.group(1) if gid_m else "0"
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={gid}"
    )


def fetch_sheet(url: str) -> str:
    if url.startswith("/") or url.startswith("file://"):
        path = url[7:] if url.startswith("file://") else url
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    export_url = to_csv_export_url(url)
    req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8-sig")


# ── Name helpers ──────────────────────────────────────────────────────────────

def edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance (case-insensitive)."""
    a, b = a.lower(), b.lower()
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp = dp[j]
            dp[j] = prev if a[i - 1] == b[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[n]


def expand_acronym(name: str) -> str:
    return ACRONYM_EXPANSIONS.get(name.strip(), name.strip())


def _normalize_group_name(name: str) -> str:
    """Expand acronym, lowercase, and apply group name aliases."""
    n = expand_acronym(name).strip().lower()
    for alias_set in GROUP_NAME_ALIASES:
        if n in alias_set:
            n = min(alias_set)  # canonical form is alphabetically first
            break
    return n


def group_names_match(a: str, b: str) -> bool:
    return _normalize_group_name(a) == _normalize_group_name(b)


def first_name_matches(a: str, b: str) -> bool:
    a_l, b_l = a.strip().lower(), b.strip().lower()
    if a_l == b_l:
        return True
    for alias_set in FIRST_NAME_ALIASES:
        if a_l in alias_set and b_l in alias_set:
            return True
    return False


# ── Sheet parsing ─────────────────────────────────────────────────────────────

def find_header_row_index(rows: list[list[str]]) -> int:
    """Return index of first row containing the exact cell value 'Members'."""
    for i, row in enumerate(rows):
        if any(cell.strip() == "Members" for cell in row):
            return i
    raise ValueError("Header row not found: no cell with exact text 'Members'")


def best_column_match(headers: list[str], target: str, used: set[int]) -> int:
    """Return the index of the header closest by edit distance to target."""
    best_dist, best_idx = None, None
    for i, h in enumerate(headers):
        if i in used:
            continue
        d = edit_distance(h.strip(), target)
        if best_dist is None or d < best_dist:
            best_dist, best_idx = d, i
    if best_idx is None:
        raise ValueError(f"No column found for '{target}'")
    return best_idx


def parse_cell_lines(cell: str) -> list[str]:
    return [ln.strip() for ln in cell.splitlines() if ln.strip()]


def strip_leading_dash(s: str) -> str:
    return re.sub(r"^-\s*", "", s).strip()


def parse_member_line(line: str) -> list[tuple[str, Optional[str]]]:
    """Parse one member cell line into (first_name, last_or_initial) pairs.

    Strips the leading dash and any trailing parenthetical, then splits on
    '/' for multi-person entries.  The last fragment's last name is shared
    with any earlier slash-separated first-name-only fragments.

    Examples (de-identified):
      "- Alex Green"        -> [("Alex", "Green")]
      "- Alex G."           -> [("Alex", "G.")]
      "- Alex/Robin Blue"   -> [("Alex", "Blue"), ("Robin", "Blue")]
      "- Alex/Robin"        -> [("Alex", None), ("Robin", None)]
      "- Alex (treasurer)"  -> [("Alex", None)]
      "- Alex Blue (note)"  -> [("Alex", "Blue")]
    """
    text = strip_leading_dash(line)
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if not text:
        return []

    parts = [p.strip() for p in text.split("/") if p.strip()]
    fragments = [p.split() for p in parts]

    # Shared last name: last fragment's last word if it has more than one word
    shared_last: Optional[str] = fragments[-1][-1] if len(fragments[-1]) > 1 else None

    result: list[tuple[str, Optional[str]]] = []
    for i, words in enumerate(fragments):
        if not words:
            continue
        first = words[0]
        if len(words) > 1:
            last: Optional[str] = " ".join(words[1:])
        elif shared_last is not None and i < len(fragments) - 1:
            last = shared_last
        else:
            last = None
        result.append((first, last))
    return result


def parse_sheet(csv_text: str) -> list[Circle]:
    """Parse CSV text into a list of Circle objects with hierarchy."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_idx = find_header_row_index(rows)
    headers = rows[header_idx]

    used: set[int] = set(range(3))  # first 3 cols are circle name columns

    def find_col(target: str) -> int:
        idx = best_column_match(headers, target, used)
        used.add(idx)
        return idx

    consultants_col = find_col("Consultants")
    members_col = find_col("Members")
    leads_col = find_col("Lead, Facilitator, Sec.")
    meetings_col = find_col("Meetings")
    desc_col = meetings_col + 1
    aim_col = desc_col + 1
    qual_col = aim_col + 1

    def cell(row: list[str], idx: int) -> str:
        return row[idx].strip() if idx < len(row) else ""

    # Track the most recent circle name in each column for parent detection
    recent: list[Optional[str]] = [None, None, None]
    circles: list[Circle] = []

    for row in rows[header_idx + 1:]:
        col_index = None
        raw_name = ""
        for c in range(3):
            v = row[c].strip() if c < len(row) else ""
            if v:
                col_index = c
                raw_name = v
                break
        if col_index is None:
            continue

        name = expand_acronym(raw_name)
        parent = recent[col_index - 1] if col_index > 0 else None
        recent[col_index] = name
        for deeper in range(col_index + 1, 3):
            recent[deeper] = None

        circles.append(Circle(
            raw_name=raw_name,
            name=name,
            col_index=col_index,
            parent_name=parent,
            member_lines=parse_cell_lines(cell(row, members_col)),
            lead_lines=parse_cell_lines(cell(row, leads_col)),
            consultant_text=cell(row, consultants_col),
            meetings=cell(row, meetings_col),
            description=cell(row, desc_col),
            aim=cell(row, aim_col),
            qualifications=cell(row, qual_col),
        ))

    return circles


# ── Description building ──────────────────────────────────────────────────────

def build_description(circle: Circle, remaining_consultant_text: str) -> str:
    """Build the Gather group description, capped at MAX_DESC chars.

    Appends Consultants, Meetings, and Parent lines in order, truncating
    the description column content to make room.  Lines that still don't fit
    with an empty description are omitted entirely.
    """
    extra_lines: list[str] = []
    if remaining_consultant_text:
        extra_lines.append(f"\nConsultants: {remaining_consultant_text}")
    if circle.meetings:
        extra_lines.append(f"\nMeetings: {circle.meetings}")
    if circle.parent_name:
        extra_lines.append(f"\nParent: {circle.parent_name}")

    # Greedily include extra lines that fit even with an empty base description
    extra = ""
    for line in extra_lines:
        if len(extra) + len(line) <= MAX_DESC:
            extra += line

    desc = circle.description[: MAX_DESC - len(extra)]
    return desc + extra


# ── Wiki markdown ─────────────────────────────────────────────────────────────

def _render_entry(
    circle: Circle,
    by_parent: dict[Optional[str], list["Circle"]],
    group_urls: dict[str, str],
    depth: int,
) -> list[str]:
    pad = "  " * depth
    child_pad = "  " * (depth + 1)

    url = group_urls.get(circle.name, "")
    root_text = f"[{circle.name}]({url})" if url else circle.name
    lines = [f"{pad}- {root_text}"]

    if circle.description:
        lines.append(f"{child_pad}- Domain: {circle.description}")
    if circle.aim:
        lines.append(f"{child_pad}- Aim: {circle.aim}")
    if circle.qualifications:
        lines.append(f"{child_pad}- Qualifications: {circle.qualifications}")

    children = by_parent.get(circle.name, [])
    if children:
        lines.append(f"{child_pad}- Sub-circles:")
        for child in children:
            lines.extend(_render_entry(child, by_parent, group_urls, depth + 2))

    return lines


def build_wiki_markdown(
    circles: list[Circle], group_urls: dict[str, str]
) -> str:
    by_parent: dict[Optional[str], list[Circle]] = {}
    for c in circles:
        by_parent.setdefault(c.parent_name, []).append(c)

    lines: list[str] = [f"# {WIKI_TITLE}", ""]
    for root in by_parent.get(None, []):
        lines.extend(_render_entry(root, by_parent, group_urls, 0))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── Gather: fetch users ───────────────────────────────────────────────────────

_JUNK_NAMES = {"profile", "edit", "delete", "members", "new", "sign in", "sign out"}


def fetch_all_gather_users(page: Page, base_url: str) -> list[GatherUser]:
    """Scrape all users from the Gather directory, following pagination."""
    # Collect all candidate names per uid; the same uid may appear multiple
    # times (e.g. as "Profile" link and as the user's actual name).
    names_by_uid: dict[str, list[str]] = {}
    url: Optional[str] = f"{base_url}/users"

    while url:
        page.goto(url, wait_until="networkidle")
        for link in page.locator('a[href*="/users/"]').all():
            href = link.get_attribute("href") or ""
            m = re.search(r"/users/(\d+)$", href)
            if not m:
                continue
            uid = m.group(1)
            name = link.inner_text().strip()
            if name and name.lower() not in _JUNK_NAMES:
                names_by_uid.setdefault(uid, []).append(name)

        next_link = page.locator('a[rel="next"]')
        next_href = next_link.get_attribute("href") if next_link.count() > 0 else None
        url = f"{base_url}{next_href}" if next_href else None

    users: list[GatherUser] = []
    for uid, names in names_by_uid.items():
        # Prefer the name with the most words (full name over nav labels)
        name = max(names, key=lambda n: len(n.split()))
        parts = name.split(None, 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
        users.append(GatherUser(user_id=uid, first_name=first,
                                last_name=last, full_name=name))
    return users


# ── Gather: member resolution ─────────────────────────────────────────────────

def match_member(
    first: str,
    last_or_initial: Optional[str],
    gather_users: list[GatherUser],
) -> list[GatherUser]:
    results = []
    for user in gather_users:
        if not first_name_matches(first, user.first_name):
            continue
        if last_or_initial is None:
            results.append(user)
        elif last_or_initial.endswith("."):
            initial = last_or_initial.rstrip(".").lower()
            if user.last_name.lower().startswith(initial):
                results.append(user)
        elif user.last_name.strip().lower() == last_or_initial.strip().lower():
            results.append(user)
    return results


def resolve_group_members(
    circle: Circle,
    gather_users: list[GatherUser],
) -> tuple[list[tuple[GatherUser, bool]], str]:
    """Resolve member/lead lines and consultants to Gather users.

    Returns:
      members: list of (GatherUser, is_manager)
      remaining_consultants: comma-joined names of consultants not in Gather

    Raises ValueError on ambiguous member matches or leads missing from Members.
    """
    resolved: list[tuple[GatherUser, bool]] = []
    user_by_id: dict[str, GatherUser] = {}

    for line in circle.member_lines:
        for first, last in parse_member_line(line):
            hits = match_member(first, last, gather_users)
            if len(hits) > 1:
                raise ValueError(
                    f"Ambiguous member '{first} {last or ''}' in '{circle.name}': "
                    f"matches {[u.full_name for u in hits]}"
                )
            if not hits:
                log("WARN", "member_match",
                    f"No user for '{first} {last or ''}' in '{circle.name}', skipping")
                continue
            u = hits[0]
            if u.user_id not in user_by_id:
                user_by_id[u.user_id] = u
                resolved.append((u, False))

    # Mark leads as managers; raise if a lead name has no member match
    manager_ids: set[str] = set()
    for line in circle.lead_lines:
        lead_name = strip_leading_dash(line).strip()
        if not lead_name:
            continue
        first = lead_name.split()[0]
        matches = [u for u, _ in resolved if first_name_matches(first, u.first_name)]
        if not matches:
            raise ValueError(
                f"Lead '{lead_name}' in '{circle.name}' not found in Members list"
            )
        for u in matches:
            manager_ids.add(u.user_id)

    resolved = [(u, u.user_id in manager_ids) for u, _ in resolved]

    # Consultants: add those found in Gather; keep rest for description
    remaining: list[str] = []
    for raw_line in parse_cell_lines(circle.consultant_text):
        cname = strip_leading_dash(raw_line)
        pairs = parse_member_line(f"- {cname}")
        if not pairs:
            remaining.append(cname)
            continue
        first, last = pairs[0]
        hits = match_member(first, last, gather_users)
        if hits:
            for u in hits:
                if u.user_id not in user_by_id:
                    user_by_id[u.user_id] = u
                    resolved.append((u, False))
        else:
            remaining.append(cname)

    return resolved, ", ".join(remaining)


# ── Gather: fetch groups ──────────────────────────────────────────────────────

def fetch_all_gather_groups(page: Page, base_url: str) -> list[GatherGroup]:
    """Scrape the group list page, returning basic info (members fetched later)."""
    groups: list[GatherGroup] = []
    seen_ids: set[str] = set()
    url: Optional[str] = f"{base_url}/groups"

    while url:
        page.goto(url, wait_until="networkidle")
        for link in page.locator('a[href*="/groups/"]').all():
            href = link.get_attribute("href") or ""
            m = re.search(r"/groups/(\d+)$", href)
            if not m:
                continue
            gid = m.group(1)
            if gid in seen_ids:
                continue
            name = link.inner_text().strip()
            if not name or name in ("Edit", "Delete", "Members", "New Group", "New group"):
                continue
            seen_ids.add(gid)
            groups.append(GatherGroup(group_id=gid, name=name,
                                      kind="", availability="", description=""))

        next_link = page.locator('a[rel="next"]')
        next_href = next_link.get_attribute("href") if next_link.count() > 0 else None
        url = f"{base_url}{next_href}" if next_href else None

    return groups


def find_matching_group(
    circle: Circle, gather_groups: list[GatherGroup]
) -> Optional[GatherGroup]:
    matches = [g for g in gather_groups if group_names_match(circle.name, g.name)]
    if len(matches) > 1:
        raise ValueError(
            f"Multiple groups match '{circle.name}': {[g.name for g in matches]}"
        )
    return matches[0] if matches else None


def _fetch_group_detail(page: Page, base_url: str, group: GatherGroup) -> GatherGroup:
    """Load the group edit page to read current kind, availability, description,
    and inline membership rows."""
    page.goto(f"{base_url}/groups/{group.group_id}/edit", wait_until="networkidle")

    def get_select(name: str) -> str:
        el = page.locator(f'select[name="{name}"]')
        return el.input_value() if el.count() > 0 else ""

    def get_textarea(name: str) -> str:
        el = page.locator(f'textarea[name="{name}"]')
        return el.input_value() if el.count() > 0 else ""

    kind = get_select("groups_group[kind]")
    availability = get_select("groups_group[availability]")
    description = get_textarea("groups_group[description]")

    # Read inline memberships from the edit form
    members: list[GatherGroupMember] = []
    for sel in page.locator('select[name*="[user_id]"]').all():
        uid = sel.input_value()
        if not uid:
            continue
        name_attr = sel.get_attribute("name") or ""
        kind_name = name_attr.replace("[user_id]", "[kind]")
        member_kind = page.locator(f'select[name="{kind_name}"]').input_value()
        members.append(GatherGroupMember(user_id=uid, is_manager=(member_kind == "manager")))

    return GatherGroup(group_id=group.group_id, name=group.name,
                       kind=kind, availability=availability,
                       description=description, members=members)


def _group_needs_update(
    existing: GatherGroup,
    kind: str,
    availability: str,
    description: str,
    members: list[tuple[GatherUser, bool]],
) -> bool:
    if existing.kind != kind:
        return True
    if existing.availability != availability:
        return True
    if existing.description.strip() != description.strip():
        return True
    existing_set = {(m.user_id, m.is_manager) for m in existing.members}
    desired_set = {(u.user_id, mgr) for u, mgr in members}
    return existing_set != desired_set


# ── Gather: group operations ──────────────────────────────────────────────────

def _fill_group_basics(
    page: Page, name: str, kind: str, availability: str, description: str
):
    """Fill the basic (non-member) fields of the group create/edit form."""
    page.locator('input[name="groups_group[name]"]').fill(name)
    page.locator('select[name="groups_group[kind]"]').select_option(kind)
    page.locator('select[name="groups_group[availability]"]').select_option(availability)
    desc_el = page.locator('textarea[name="groups_group[description]"]')
    if desc_el.count() > 0:
        desc_el.fill(description)


def _add_inline_member(page: Page, user: GatherUser, is_manager: bool):
    """Click '+ Add Member', then fill in the user and kind for the new row."""
    page.locator('a:has-text("Add Member")').first.click()
    page.wait_for_timeout(400)

    # The new row's user_id select is the last one with no value
    user_sel = page.locator('select[name*="[user_id]"]').last
    uid_name = user_sel.get_attribute("name") or ""
    select2_choose(page, f'select[name="{uid_name}"]', user.full_name)

    kind_name = uid_name.replace("[user_id]", "[kind]")
    page.locator(f'select[name="{kind_name}"]').select_option(
        "manager" if is_manager else "joiner"
    )


def _remove_inline_member(page: Page, user_id: str):
    """Mark an existing membership row for destruction on the edit form."""
    # Find the select whose current value matches this user_id, then set _destroy
    for sel in page.locator('select[name*="[user_id]"]').all():
        if sel.input_value() == user_id:
            name_attr = sel.get_attribute("name") or ""
            destroy_name = name_attr.replace("[user_id]", "[_destroy]")
            destroy_el = page.locator(f'input[name="{destroy_name}"]')
            if destroy_el.count() > 0:
                page.evaluate(
                    f'document.querySelector(\'input[name="{destroy_name}"]\').value = "1"'
                )
            # Also try clicking a visible Remove link in the same fieldset/row
            else:
                m = re.search(r'\[memberships_attributes\]\[(\w+)\]', name_attr)
                if m:
                    key = m.group(1)
                    remove_link = page.locator(
                        f'[data-key="{key}"] a:has-text("Remove"), '
                        f'a[data-remove-fields][data-key="{key}"]'
                    ).first
                    if remove_link.count() > 0:
                        remove_link.click()
                        page.wait_for_timeout(200)
            break


def _find_group_in_list(page: Page, base_url: str, name: str) -> Optional[str]:
    """Scan the groups list page for a group matching `name`; return its ID."""
    page.goto(f"{base_url}/groups", wait_until="networkidle")
    for link in page.locator('a[href*="/groups/"]').all():
        href = link.get_attribute("href") or ""
        m = re.search(r"/groups/(\d+)$", href)
        if m and group_names_match(link.inner_text().strip(), name):
            return m.group(1)
    return None


def _submit_group_form(page: Page, circle_name: str) -> bool:
    page.locator('input[name="commit"]').click()
    page.wait_for_load_state("networkidle")
    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"group_form_err_{circle_name[:20]}")
        log("ERROR", "group_form", circle_name, err[:200])
        return False
    return True


def create_or_update_group(
    page: Page,
    base_url: str,
    circle: Circle,
    existing: Optional[GatherGroup],
    members: list[tuple[GatherUser, bool]],
    description: str,
    dry_run: bool,
) -> Optional[str]:
    """Create or update a Gather group; return its group_id (str) or None on failure."""
    kind = GROUP_KINDS[circle.col_index]
    availability = "closed"

    if existing is not None:
        existing_detail = _fetch_group_detail(page, base_url, existing)
        if not _group_needs_update(existing_detail, kind, availability, description, members):
            log("INFO", "group", f"Up to date, skipping: {circle.name}")
            return existing.group_id

        if dry_run:
            log("DRY-RUN", "update_group", circle.name)
            return existing.group_id

        try:
            page.goto(
                f"{base_url}/groups/{existing.group_id}/edit", wait_until="networkidle"
            )
            _fill_group_basics(page, circle.name, kind, availability, description)

            # Sync members on the edit form
            existing_by_uid = {m.user_id: m.is_manager for m in existing_detail.members}
            desired_map = {u.user_id: (u, is_mgr) for u, is_mgr in members}

            for uid in set(existing_by_uid) - set(desired_map):
                _remove_inline_member(page, uid)
            for uid, (user, is_mgr) in desired_map.items():
                if uid not in existing_by_uid:
                    _add_inline_member(page, user, is_mgr)
                elif existing_by_uid[uid] != is_mgr:
                    # Update kind for existing row
                    for sel in page.locator('select[name*="[user_id]"]').all():
                        if sel.input_value() == uid:
                            kind_name = (sel.get_attribute("name") or "").replace(
                                "[user_id]", "[kind]"
                            )
                            page.locator(f'select[name="{kind_name}"]').select_option(
                                "manager" if is_mgr else "joiner"
                            )
                            break

            if not _submit_group_form(page, circle.name):
                return None
            log("INFO", "update_group", f"Updated: {circle.name}")
            return existing.group_id

        except Exception as e:
            screenshot(page, f"group_update_exc_{circle.name[:20]}")
            log("ERROR", "update_group", circle.name, str(e))
            return None
    else:
        if dry_run:
            log("DRY-RUN", "create_group", circle.name)
            return "dry-run"

        try:
            page.goto(f"{base_url}/groups/new", wait_until="networkidle")
            _fill_group_basics(page, circle.name, kind, availability, description)
            for user, is_mgr in members:
                _add_inline_member(page, user, is_mgr)
            if not _submit_group_form(page, circle.name):
                return None

            # After creation Gather redirects to /groups; find the new group by name
            group_id = _find_group_in_list(page, base_url, circle.name)
            if not group_id:
                log("ERROR", "create_group", circle.name, "Group not found in list after creation")
                return None
            log("INFO", "create_group", f"Created: {circle.name} (id={group_id})")
            return group_id

        except Exception as e:
            screenshot(page, f"group_create_exc_{circle.name[:20]}")
            log("ERROR", "create_group", circle.name, str(e))
            return None


# ── Gather: wiki page ─────────────────────────────────────────────────────────

def _codemirror_get(page: Page) -> str:
    """Return current value from the first CodeMirror editor on the page."""
    return page.evaluate(
        "() => { const cm = document.querySelector('.CodeMirror')?.CodeMirror;"
        " return cm ? cm.getValue() : ''; }"
    )


def _codemirror_set(page: Page, text: str):
    """Set value on the first CodeMirror editor, then trigger change event."""
    page.evaluate(
        "(text) => { const cm = document.querySelector('.CodeMirror')?.CodeMirror;"
        " if (cm) { cm.setValue(text); cm.save(); } }",
        text,
    )

def create_or_update_wiki_page(
    page: Page, base_url: str, content: str, dry_run: bool
) -> bool:
    """Create or update the Circle Hierarchy wiki page."""
    # Check whether the page already exists by navigating to its slug.
    # Gather throws an exception page (not a 404) for missing slugs, so we
    # detect existence by checking the title for known error strings.
    page.goto(f"{base_url}/wiki/{WIKI_SLUG}", wait_until="networkidle")
    title = page.title()
    page_exists = (
        "Exception" not in title
        and "Error" not in title
        and "404" not in title
        and page.locator('h1').count() > 0
    )

    if page_exists:
        page.goto(f"{base_url}/wiki/{WIKI_SLUG}/edit", wait_until="networkidle")

        if page.locator(".CodeMirror").count() == 0:
            log("ERROR", "update_wiki", WIKI_TITLE, "Editor not found on edit page")
            return False

        current = _codemirror_get(page)
        if current.strip() == content.strip():
            log("INFO", "wiki", f"'{WIKI_TITLE}' up to date, skipping")
            return True
        if dry_run:
            log("DRY-RUN", "update_wiki", WIKI_TITLE)
            return True
        _codemirror_set(page, content)
        page.locator('input[name="commit"]').click()
        page.wait_for_load_state("networkidle")
        err = _check_submit_errors(page)
        if err:
            screenshot(page, "wiki_update_err")
            log("ERROR", "update_wiki", WIKI_TITLE, err[:200])
            return False
        log("INFO", "update_wiki", f"Updated: '{WIKI_TITLE}'")
        return True
    else:
        page.goto(f"{base_url}/wiki/new", wait_until="networkidle")

        if dry_run:
            log("DRY-RUN", "create_wiki", WIKI_TITLE)
            return True

        if page.locator("form").count() == 0:
            log("ERROR", "create_wiki", WIKI_TITLE, "New wiki page form not found")
            return False

        title_el = page.locator('input[name="wiki_page[title]"], #wiki_page_title')
        if title_el.count() > 0:
            title_el.fill(WIKI_TITLE)

        if page.locator(".CodeMirror").count() == 0:
            log("ERROR", "create_wiki", WIKI_TITLE, "Editor not found on new page form")
            return False

        _codemirror_set(page, content)
        page.locator('input[name="commit"]').click()
        page.wait_for_load_state("networkidle")
        err = _check_submit_errors(page)
        if err:
            screenshot(page, "wiki_create_err")
            log("ERROR", "create_wiki", WIKI_TITLE, err[:200])
            return False
        log("INFO", "create_wiki", f"Created: '{WIKI_TITLE}'")
        return True


# ── Main ──────────────────────────────────────────────────────────────────────

def process(
    base_url: str,
    email: str,
    password: str,
    sheet_url: str = DEFAULT_SHEET_URL,
    dry_run: bool = False,
):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} dry_run={dry_run}")

    log("INFO", "fetch_sheet", sheet_url)
    csv_text = fetch_sheet(sheet_url)
    circles = parse_sheet(csv_text)
    log("INFO", "parse_sheet", f"{len(circles)} circles parsed")

    chrome_path = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    launch_kwargs: dict = {"args": ["--no-sandbox"]}
    if os.path.exists(chrome_path):
        launch_kwargs["executable_path"] = chrome_path

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        page = browser.new_context().new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            close_log()
            browser.close()
            sys.exit(1)

        gather_users = fetch_all_gather_users(page, base_url)
        log("INFO", "fetch_users", f"{len(gather_users)} users found")

        gather_groups = fetch_all_gather_groups(page, base_url)
        log("INFO", "fetch_groups", f"{len(gather_groups)} groups found")

        group_urls: dict[str, str] = {}
        stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": 0}

        for circle in circles:
            try:
                members, remaining_consultants = resolve_group_members(
                    circle, gather_users
                )
            except ValueError as e:
                log("ERROR", "resolve_members", circle.name, str(e))
                stats["errors"] += 1
                continue

            description = build_description(circle, remaining_consultants)

            try:
                existing = find_matching_group(circle, gather_groups)
            except ValueError as e:
                log("ERROR", "find_group", circle.name, str(e))
                stats["errors"] += 1
                continue

            group_id = create_or_update_group(
                page, base_url, circle, existing, members, description, dry_run
            )

            if group_id is None:
                stats["failed"] += 1
            elif group_id == "dry-run":
                stats["created"] += 1
            elif existing is None:
                stats["created"] += 1
                group_urls[circle.name] = f"/groups/{group_id}"
            else:
                group_urls[circle.name] = f"/groups/{group_id}"
                # distinguish updated vs skipped via log (already logged inside)
                stats["skipped"] += 1  # conservative; update logged separately

        browser.close()

    log("INFO", "done", str(stats))
    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Populate Gather groups from a Google Sheets circle hierarchy"
    )
    parser.add_argument(
        "-u", "--base-url", required=True,
        help="Gather base URL, e.g. https://sub.gather.coop",
    )
    parser.add_argument("-e", "--email", required=True, help="Admin login email")
    parser.add_argument("-p", "--password", required=True, help="Admin login password")
    parser.add_argument(
        "-s", "--sheet-url", default=DEFAULT_SHEET_URL,
        help="Google Sheets URL (edit or export format)",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would happen without making any changes",
    )
    args = parser.parse_args()
    process(args.base_url, args.email, args.password, args.sheet_url, args.dry_run)


if __name__ == "__main__":
    cli()
