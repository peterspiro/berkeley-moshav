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
import unicodedata
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
LOG_FILE = Path(__file__).parent / "groups_log.csv"
SCREENSHOT_DIR = Path(__file__).parent / "import_screenshots"

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
    frozenset({"ann", "annie"}),
    frozenset({"yocab", "yacov"}),
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
    child: bool = False


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
    list_name: Optional[str] = None


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
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path))
        log("DEBUG", "screenshot", str(path))
    except Exception as e:
        log("WARN", "screenshot", f"Failed to save {path}: {e}")


def _check_submit_errors(page: Page) -> str | None:
    sel = ".error, #error_explanation, .alert-danger, .alert-warning"
    if page.locator(sel).count() > 0:
        return page.locator(sel).first.inner_text()
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
    if url.startswith("file://"):
        path = url[7:]
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    if not url.startswith("http://") and not url.startswith("https://"):
        with open(url, encoding="utf-8-sig") as f:
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
    """Expand acronym, strip trailing parenthetical, lowercase, and apply aliases."""
    n = re.sub(r"\s*\([^)]*\)?\s*$", "", expand_acronym(name).strip()).strip().lower()
    for alias_set in GROUP_NAME_ALIASES:
        if n in alias_set:
            n = min(alias_set)  # canonical form is alphabetically first
            break
    return n


def group_names_match(a: str, b: str) -> bool:
    return _normalize_group_name(a) == _normalize_group_name(b)


def _fold_accents(s: str) -> str:
    """Strip diacritical marks so accented characters match their base letter.

    E.g. 'Zoë' -> 'Zoe', 'Müller' -> 'Muller'.
    """
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _cmp(s: str) -> str:
    """Canonical form for accent- and case-insensitive comparison."""
    return _fold_accents(s).strip().lower()


def _circle_name_to_list_name(name: str) -> str:
    """Convert a circle name to a Mailman-safe list name.

    Folds accents, lowercases, replaces runs of non-alphanumeric characters
    with a single hyphen, strips leading/trailing hyphens, and truncates to
    50 characters.
    """
    s = _fold_accents(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:50]


def first_name_matches(a: str, b: str) -> bool:
    a_l, b_l = _cmp(a), _cmp(b)
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


_ROLE_RE = re.compile(
    r"\s*-?\s*\b(?:lead|facilitator|sec(?:retary)?\.?|feedback\s+link)\b[^/]*",
    re.IGNORECASE,
)


def _strip_roles(s: str) -> str:
    """Remove role-indicator words/phrases (Lead, Facilitator, Secretary, Feedback link)."""
    return " ".join(_ROLE_RE.sub("", s).split())


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
    text = re.sub(r"\s*\(.*", "", text).strip()
    text = _strip_roles(text)
    text = re.sub(r"[-\s]+$", "", text)  # strip trailing hyphens/spaces
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

    seen_col0 = False
    for row in rows[header_idx + 1:]:
        populated = [(c, row[c].strip()) for c in range(3)
                     if c < len(row) and row[c].strip()]

        # Skip rows with multiple circle-name columns populated (malformed)
        if len(populated) > 1:
            continue

        if not populated:
            continue

        col_index, raw_name = populated[0]

        # Only the first col-0 row is valid (single top-level circle)
        if col_index == 0:
            if seen_col0:
                continue
            seen_col0 = True

        name = expand_acronym(
            re.sub(r"^[\W_]+|[\W_]+$", "",
                   re.sub(r"\s*\([^)]*\)?\s*$", "", raw_name.strip()).strip()).strip()
        )
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

def _post_len(s: str) -> int:
    """Length of s after browser \n -> \r\n normalisation during form POST."""
    return len(s) + s.count("\n")


def build_description(circle: Circle, remaining_consultant_text: str) -> str:
    """Build the Gather group description, capped at MAX_DESC chars.

    Appends Consultants, Meetings, and Parent lines in order, truncating
    the description column content to make room.  Lines that still don't fit
    with an empty description are omitted entirely.

    All length checks use POST length (_post_len) because the browser
    normalises bare \\n to \\r\\n before submitting, adding one byte per
    newline.  PostgreSQL's VARCHAR(255) counts those extra bytes.
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
        if _post_len(extra) + _post_len(line) <= MAX_DESC:
            extra += line

    # Trim base description so that POST length of (desc + extra) <= MAX_DESC
    budget = MAX_DESC - _post_len(extra)
    desc = circle.description
    while _post_len(desc) > budget:
        desc = desc[:-1]
    result = desc + extra
    # Final safety: trim trailing chars until POST length fits
    while _post_len(result) > MAX_DESC:
        result = result[:-1]
    return result


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



def fetch_all_gather_users(page: Page, base_url: str) -> list[GatherUser]:
    """Download all users from the Gather directory CSV export."""
    response = page.request.get(f"{base_url}/users.csv")
    reader = csv.DictReader(io.StringIO(response.text()))
    users: list[GatherUser] = []
    for row in reader:
        uid = row.get("ID", "").strip()
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        is_child = row.get("Is Child", "").strip().lower() == "true"
        if not uid:
            continue
        users.append(GatherUser(
            user_id=uid,
            first_name=first,
            last_name=last,
            full_name=f"{first} {last}".strip(),
            child=is_child,
        ))
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
            initial = _cmp(last_or_initial.rstrip("."))
            if _cmp(user.last_name).startswith(initial):
                results.append(user)
        elif _cmp(user.last_name) == _cmp(last_or_initial):
            results.append(user)
    return results


def _disambiguate_by_cross_cell(
    candidates: list[GatherUser],
    cross_lines: list[str],
) -> list[GatherUser]:
    """Narrow candidates using last-name evidence from another cell's lines.

    If exactly one candidate has a first+last match in cross_lines, return it.
    Otherwise return the original candidates unchanged.
    """
    confirmed: set[str] = set()
    for line in cross_lines:
        for lf, ll in parse_member_line(line):
            if ll is None:
                continue
            for u in candidates:
                if (first_name_matches(u.first_name, lf)
                        and _cmp(u.last_name) == _cmp(ll)):
                    confirmed.add(u.user_id)
    if len(confirmed) == 1:
        uid = next(iter(confirmed))
        return [u for u in candidates if u.user_id == uid]
    return candidates


def resolve_group_members(
    circle: Circle,
    gather_users: list[GatherUser],
) -> tuple[list[tuple[GatherUser, bool]], str]:
    """Resolve member/lead lines and consultants to Gather users.

    Returns:
      members: list of (GatherUser, is_manager)
      remaining_consultants: comma-joined names of consultants not in Gather

    Raises ValueError on leads missing from Members.
    """
    adults = [u for u in gather_users if not u.child]
    resolved: list[tuple[GatherUser, bool]] = []
    user_by_id: dict[str, GatherUser] = {}

    for line in circle.member_lines:
        for first, last in parse_member_line(line):
            hits = match_member(first, last, adults)
            if len(hits) > 1 and last is None:
                hits = _disambiguate_by_cross_cell(hits, circle.lead_lines)
            if len(hits) > 1:
                log("WARN", "member_match",
                    f"Ambiguous member '{first} {last or ''}' in '{circle.name}': "
                    f"matches {[u.full_name for u in hits]}, adding all")
            if not hits:
                log("WARN", "member_match",
                    f"No user for '{first} {last or ''}' in '{circle.name}', skipping")
                continue
            for u in hits:
                if u.user_id not in user_by_id:
                    user_by_id[u.user_id] = u
                    resolved.append((u, False))

    # Mark leads as managers; if a lead isn't already a member, add them
    manager_ids: set[str] = set()
    for line in circle.lead_lines:
        lead_name = _strip_roles(strip_leading_dash(line)).strip()
        # In lead lines, slash separates roles (e.g. "Facilitator/Feedback Link"),
        # not names, so discard everything from the first slash onward.
        lead_name = lead_name.split("/")[0].strip()
        lead_name = re.sub(r"[-\s]+$", "", lead_name)
        if not lead_name:
            continue
        words = lead_name.split()
        first = words[0].split("-")[0]
        last = " ".join(words[1:]) if len(words) > 1 else None
        matches = [u for u, _ in resolved if first_name_matches(first, u.first_name)]
        if not matches:
            # Lead not in members list — try to find them in all Gather users
            hits = match_member(first, last, adults)
            if len(hits) > 1:
                raise ValueError(
                    f"Lead '{lead_name}' in '{circle.name}' is ambiguous: "
                    f"matches {[u.full_name for u in hits]}"
                )
            if not hits:
                raise ValueError(
                    f"Lead '{lead_name}' in '{circle.name}' not found in Members list or Gather users"
                )
            u = hits[0]
            if u.user_id not in user_by_id:
                user_by_id[u.user_id] = u
                resolved.append((u, False))
            matches = [u]
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
        hits = match_member(first, last, adults)
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

    mailman_name_el = page.locator(
        'input[name*="mailman_list_attributes"][name*="[name]"]'
    )
    list_name: Optional[str] = None
    if mailman_name_el.count() > 0:
        list_name = mailman_name_el.first.input_value().strip() or None

    return GatherGroup(group_id=group.group_id, name=group.name,
                       kind=kind, availability=availability,
                       description=description, members=members,
                       list_name=list_name)


def _group_needs_update(
    existing: GatherGroup,
    kind: str,
    availability: str,
    description: str,
    members: list[tuple[GatherUser, bool]],
    desired_name: str,
) -> bool:
    if existing.name != desired_name:
        return True
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
    """Scan the groups list page (all pages) for a group matching `name`; return its ID."""
    url: Optional[str] = f"{base_url}/groups"
    found_names: list[str] = []
    while url:
        page.goto(url, wait_until="networkidle")
        for link in page.locator('a[href*="/groups/"]').all():
            href = link.get_attribute("href") or ""
            m = re.search(r"/groups/(\d+)$", href)
            if not m:
                continue
            text = link.inner_text().strip()
            found_names.append(text)
            if group_names_match(text, name):
                return m.group(1)
        next_link = page.locator('a[rel="next"]')
        next_href = next_link.get_attribute("href") if next_link.count() > 0 else None
        url = f"{base_url}{next_href}" if next_href else None
    log("DEBUG", "group_list", f"Looking for {name!r}; found: {found_names}")
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


def _ensure_mailman_list(
    page: Page,
    base_url: str,
    group_id: str,
    list_name: str,
    dry_run: bool,
) -> bool:
    """Create a Mailman email list for the group if one does not already exist.

    Returns True if a list exists or was successfully created, False on error
    or if Mailman is not configured for this Gather instance.
    """
    try:
        page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")

        name_el = page.locator(
            'input[name*="mailman_list_attributes"][name*="[name]"]'
        )
        if name_el.count() == 0:
            log("WARN", "mailman_list", f"group_id={group_id}",
                "Mailman list field not found — Mailman may not be configured")
            return False

        current = name_el.first.input_value().strip()
        if current:
            log("INFO", "mailman_list",
                f"group_id={group_id}: list '{current}' already exists, skipping")
            return True

        if dry_run:
            log("DRY-RUN", "create_mailman_list",
                f"group_id={group_id} list_name={list_name}")
            return True

        name_el.first.fill(list_name)

        domain_el = page.locator(
            'select[name*="mailman_list_attributes"][name*="[domain_id]"]'
        )
        if domain_el.count() > 0:
            options = domain_el.first.locator("option").all()
            selected = False
            for opt in options:
                val = opt.get_attribute("value") or ""
                if val.strip():
                    domain_el.first.select_option(val)
                    selected = True
                    break
            if not selected:
                log("WARN", "mailman_list", f"group_id={group_id}",
                    "No non-blank domain option found")
                return False

        for checkbox_field in (
            "[all_cmty_members_can_send]",
            "[managers_can_administer]",
            "[managers_can_moderate]",
        ):
            cb = page.locator(
                f'input[name*="mailman_list_attributes"][name*="{checkbox_field}"]'
            )
            if cb.count() > 0 and not cb.first.is_checked():
                cb.first.check()

        if not _submit_group_form(page, f"mailman:{group_id}"):
            return False

        log("INFO", "create_mailman_list",
            f"group_id={group_id}: created list '{list_name}'")
        return True

    except Exception as e:
        screenshot(page, f"mailman_exc_{group_id[:20]}")
        log("ERROR", "create_mailman_list", f"group_id={group_id}", str(e))
        return False


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
        if not _group_needs_update(existing_detail, kind, availability, description, members, circle.name):
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

            post_submit_url = page.url
            log("DEBUG", "create_group", f"Post-submit URL: {post_submit_url}")
            screenshot(page, f"group_create_postsubmit_{circle.name[:20]}")

            # After creation Gather redirects to /groups; find the new group by name
            group_id = _find_group_in_list(page, base_url, circle.name)
            if not group_id:
                log("ERROR", "create_group", circle.name,
                    f"Group not found in list after creation (post-submit URL: {post_submit_url})")
                screenshot(page, f"group_create_notfound_{circle.name[:20]}")
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
        stats = {
            "created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": 0,
            "list_created": 0, "list_failed": 0,
        }

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
            log("DEBUG", "description", f"{circle.name}: {len(description)} chars")

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
                continue

            if group_id == "dry-run":
                stats["created"] += 1
            elif existing is None:
                stats["created"] += 1
                group_urls[circle.name] = f"/groups/{group_id}"
            else:
                group_urls[circle.name] = f"/groups/{group_id}"
                # distinguish updated vs skipped via log (already logged inside)
                stats["skipped"] += 1  # conservative; update logged separately

            # Ensure a Mailman list exists for every confirmed-existing group.
            # Skipped for dry-run placeholders (no real group_id to navigate to).
            if group_id != "dry-run":
                list_name = _circle_name_to_list_name(circle.name)
                ok = _ensure_mailman_list(page, base_url, group_id, list_name, dry_run)
                stats["list_created" if ok else "list_failed"] += 1

        browser.close()

    log("INFO", "done", str(stats))
    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Populate Gather groups from a Google Sheets circle hierarchy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
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
