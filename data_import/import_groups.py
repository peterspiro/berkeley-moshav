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
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import Page, sync_playwright

from util.credentials import load_credentials
from util.gather_utils import (
    GatherGroup,
    GatherGroupMember,
    GatherUser,
    _add_inline_member,
    _check_submit_errors,
    _cmp,
    _codemirror_get,
    _codemirror_set,
    _fetch_group_detail,
    _fold_accents,
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
    match_member,
    screenshot,
    select2_choose,
    to_csv_export_url,
)
from util.gdrive_config import ensure_gdrive_group_access
from util.hierarchy_wiki import (
    HIERARCHY_ROOT as _HIERARCHY_ROOT,
    WIKI_SLUG,
    WIKI_TITLE,
    apply_hierarchy_content as _apply_hierarchy_content,
    ensure_hierarchy_page as _ensure_hierarchy_page,
)


# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1pgiAffsTAkOv68zVANaE5Skc73gdcFY8BdOZCDAD-Ak/edit?gid=0#gid=0"
)

MAX_DESC = 255
_LOG_FILE = Path(__file__).parent / "groups_log.csv"
_SCREENSHOT_DIR = Path(__file__).parent / "import_screenshots"

configure(_LOG_FILE, _SCREENSHOT_DIR)

ACRONYM_EXPANSIONS: dict[str, str] = {
    "P & G": "Process & Governance",
    "D, F, & L": "Development, Finance, & Legal",
    "CLC": "Community Life Circle",
}

# Each frozenset is a group of equivalent names (case-insensitive)
GROUP_NAME_ALIASES: list[frozenset] = [
    frozenset({"tech", "technology"}),
    frozenset({"landscape", "landscaping"}),
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
    """Normalize a group name for comparison.

    Steps (in order):
    - Expand known acronyms
    - Strip trailing parenthetical (including unclosed)
    - Lowercase
    - Replace & with 'and' (with surrounding space normalization)
    - Remove commas
    - Collapse runs of whitespace
    - Normalize "working group" → "work group"
    - Apply name aliases
    """
    n = re.sub(r"\s*\([^)]*\)?\s*$", "", expand_acronym(name).strip()).strip().lower()
    n = re.sub(r"\s*&\s*", " and ", n)
    n = n.replace(",", "")
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"\bwork group\b", "working group", n)
    for alias_set in GROUP_NAME_ALIASES:
        canonical = min(alias_set)
        for alias in alias_set:
            if alias != canonical:
                n = re.sub(r"\b" + re.escape(alias) + r"\b", canonical, n)
    return n


def group_names_match(a: str, b: str) -> bool:
    na, nb = _normalize_group_name(a), _normalize_group_name(b)
    if na == nb:
        return True
    # Allow "Foo" to match "Foo Circle" or "Foo Team": strip the trailing
    # suffix from the raw inputs and re-normalize (so alias expansion applies
    # correctly) then compare.  At least one input must have been stripped to
    # avoid false positives.
    def _strip_suffix(s: str) -> str:
        # Strip any trailing parenthetical first so that "Foo Team (desc)"
        # becomes "Foo Team" before the team/circle suffix is removed.
        s = re.sub(r"\s*\([^)]*\)?\s*$", "", s.strip()).strip()
        return re.sub(r"\s+(circle|team)$", "", s, flags=re.IGNORECASE)

    na2 = _normalize_group_name(_strip_suffix(a))
    nb2 = _normalize_group_name(_strip_suffix(b))
    return na2 == nb2 and (na2 != na or nb2 != nb)


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


def _circle_wiki_slug(name: str) -> str:
    """Convert a circle name to a wiki page slug, appending '-wiki'.

    E.g. 'Landscape Work Group' → 'landscape-work-group-wiki'.
    """
    s = _fold_accents(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") + "-wiki"


def _canonical_group_name(name: str) -> str:
    """Return name with 'Work Group' replaced by 'Working Group'."""
    return re.sub(r"\bWork\s+Group\b", "Working Group", name, flags=re.IGNORECASE)


def _group_kind(circle: Circle) -> str:
    """Return the Gather group kind for a circle."""
    kind = GROUP_KINDS[circle.col_index]
    if re.search(r"\bwork(?:ing)?\s+group$", circle.name, flags=re.IGNORECASE):
        kind = "committee"
    return kind


def _needs_wiki(circle: Circle) -> bool:
    """Return True if this circle should get a wiki page.

    All circles get wiki pages, plus working groups (which are committees in
    Gather but are still substantive circles that warrant a wiki page).
    """
    return _group_kind(circle) != "committee" or re.search(
        r"\bwork(?:ing)?\s+group$", circle.name, flags=re.IGNORECASE
    ) is not None


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

    for row in rows[header_idx + 1:]:
        populated = [(c, row[c].strip()) for c in range(3)
                     if c < len(row) and row[c].strip()]

        # Skip rows with multiple circle-name columns populated (malformed)
        if len(populated) > 1:
            continue

        if not populated:
            continue

        col_index, raw_name = populated[0]

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


def build_description(
    circle: Circle, remaining_consultant_text: str
) -> str:
    """Build the Gather group description, capped at MAX_DESC chars.

    Appends Consultants and Meetings lines in order, truncating the
    description column content to make room.  Lines that still don't fit
    with an empty base description are omitted entirely.

    All length checks use POST length (_post_len) because the browser
    normalises bare \\n to \\r\\n before submitting, adding one byte per
    newline.  PostgreSQL's VARCHAR(255) counts those extra bytes.
    """
    extra_lines: list[str] = []
    if remaining_consultant_text:
        extra_lines.append(f"\nConsultants: {remaining_consultant_text}")
    if circle.meetings:
        extra_lines.append(f"\nMeetings: {circle.meetings}")

    # Greedily include extra lines that fit even with an empty base description.
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


# ── Circle hierarchy wiki page ─────────────────────────────────────────────────

def _render_hierarchy_entry(
    circle: Circle,
    by_parent: dict[str, list[Circle]],
    gather_name_map: dict[str, str],
    group_urls: dict[str, str],
    gdrive_url_map: dict[str, str],
    depth: int,
) -> list[str]:
    pad = "    " * depth
    gather_name = gather_name_map.get(circle.name, circle.name)
    parts: list[str] = [gather_name]
    group_url = group_urls.get(circle.name, "")
    gdrive_url = gdrive_url_map.get(circle.name, "")
    if group_url:
        parts.append(f"[Members]({group_url})")
    if gdrive_url:
        parts.append(f"[Documents]({gdrive_url})")
    if len(parts) > 1:
        text = parts[0] + ": " + " | ".join(parts[1:])
    else:
        text = parts[0]
        log("DEBUG", "hierarchy_no_links", f"{circle.name}: no links (group={bool(group_url)} gdrive={bool(gdrive_url)})")
    lines = [f"{pad}- {text}"]
    children = sorted(
        by_parent.get(circle.name, []),
        key=lambda c: gather_name_map.get(c.name, c.name).casefold(),
    )
    for child in children:
        lines.extend(_render_hierarchy_entry(
            child, by_parent, gather_name_map, group_urls, gdrive_url_map, depth + 1
        ))
    return lines


def _build_hierarchy_content(
    circles: list[Circle],
    gather_name_map: dict[str, str],
    group_urls: dict[str, str],
    gdrive_url_map: dict[str, str],
) -> str:
    """Build markdown for the Circle Hierarchy wiki page.

    The circle whose name matches _HIERARCHY_ROOT is the root item.  Any circle
    without a parent_name (other than the root itself) is treated as a direct
    child of the root.  If no circle matches _HIERARCHY_ROOT a synthetic root
    entry (name only, no links) is rendered.

    Orphaned circles (col_index > 0, parent_name=None — caused by appearing
    before their parent in the spreadsheet) are adopted under the sole circle
    at col_index - 1 when exactly one such candidate exists.
    """
    root_circle: Optional[Circle] = None
    by_parent: dict[str, list[Circle]] = {}
    orphans: list[Circle] = []

    for c in circles:
        if group_names_match(c.name, _HIERARCHY_ROOT):
            root_circle = c
        elif c.parent_name:
            by_parent.setdefault(c.parent_name, []).append(c)
        elif c.col_index == 0:
            by_parent.setdefault(_HIERARCHY_ROOT, []).append(c)
        else:
            orphans.append(c)

    # Build lookup of non-root circles by col_index for orphan adoption.
    non_root_by_col: dict[int, list[Circle]] = {}
    for c in circles:
        if not group_names_match(c.name, _HIERARCHY_ROOT):
            non_root_by_col.setdefault(c.col_index, []).append(c)

    for orphan in orphans:
        candidates = non_root_by_col.get(orphan.col_index - 1, [])
        if len(candidates) == 1:
            by_parent.setdefault(candidates[0].name, []).append(orphan)
        else:
            log("WARN", "hierarchy",
                f"Orphan circle {orphan.name!r} (col_index={orphan.col_index}) has "
                f"{len(candidates)} candidate parents; placing under root")
            by_parent.setdefault(_HIERARCHY_ROOT, []).append(orphan)

    if root_circle is not None:
        lines = _render_hierarchy_entry(
            root_circle, by_parent, gather_name_map,
            group_urls, gdrive_url_map, depth=0,
        )
    else:
        log("WARN", "hierarchy", f"No circle matching {_HIERARCHY_ROOT!r} found; "
            "root entry will have no links")
        lines = [f"- {_HIERARCHY_ROOT}"]
        for child in sorted(
            by_parent.get(_HIERARCHY_ROOT, []),
            key=lambda c: gather_name_map.get(c.name, c.name).casefold(),
        ):
            lines.extend(_render_hierarchy_entry(
                child, by_parent, gather_name_map,
                group_urls, gdrive_url_map, depth=1,
            ))
    return "\n".join(lines) + "\n"



# ── Gather: member resolution ─────────────────────────────────────────────────

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
    existing_member_ids: Optional[set[str]] = None,
) -> tuple[list[tuple[GatherUser, bool]], str]:
    """Resolve member/lead lines and consultants to Gather users.

    Returns:
      members: list of (GatherUser, is_manager)
      remaining_consultants: comma-joined names of consultants not in Gather

    Raises ValueError on leads missing from Members.

    existing_member_ids: user IDs already in the Gather group.  When a
    first-name-only match is ambiguous, if exactly one candidate is already
    a member, the others are ignored.
    """
    adults = [u for u in gather_users if not u.child]
    resolved: list[tuple[GatherUser, bool]] = []
    user_by_id: dict[str, GatherUser] = {}

    for line in circle.member_lines:
        for first, last in parse_member_line(line):
            hits = match_member(first, last, adults)
            if len(hits) > 1 and last is None:
                hits = _disambiguate_by_cross_cell(hits, circle.lead_lines)
            if len(hits) > 1 and existing_member_ids:
                in_group = [u for u in hits if u.user_id in existing_member_ids]
                if len(in_group) == 1:
                    log("INFO", "member_match",
                        f"Disambiguated '{first}' in '{circle.name}' via existing "
                        f"membership: {in_group[0].full_name}")
                    hits = in_group
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
                log("WARN", "lead_match",
                    f"Lead '{lead_name}' in '{circle.name}' not found in Gather users, skipping")
                continue
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


def find_matching_group(
    circle: Circle, gather_groups: list[GatherGroup]
) -> Optional[GatherGroup]:
    matches = [g for g in gather_groups if group_names_match(circle.name, g.name)]
    if len(matches) > 1:
        raise ValueError(
            f"Multiple groups match '{circle.name}': {[g.name for g in matches]}"
        )
    return matches[0] if matches else None


def find_gdrive_link(
    gather_name: str, gdrive_links: list[tuple[str, str]]
) -> Optional[str]:
    """Return the href of the /gdrive link whose text matches gather_name, or None.

    Uses group_names_match for flexible matching (circle/team suffix, aliases,
    parentheticals).  Returns None if zero or multiple links match.
    """
    matches = [(text, href) for text, href in gdrive_links
               if group_names_match(gather_name, text)]
    if len(matches) == 1:
        return matches[0][1]
    if len(matches) > 1:
        log("WARN", "gdrive_match", gather_name,
            f"ambiguous — matches: {[t for t, _ in matches]}")
    return None


def _group_needs_update(
    existing: GatherGroup,
    kind: str,
    availability: str,
    description: str,
    members: list[tuple[GatherUser, bool]],
    desired_name: str,
) -> str | None:
    """Return a human-readable reason string if the group needs updating, else None."""
    if existing.name != desired_name:
        if not group_names_match(existing.name, desired_name):
            return f"name: {existing.name!r} → {desired_name!r}"
        # Names match semantically, but trigger a rename if the only difference
        # is "Work Group" vs "Working Group" (canonical form).
        if _canonical_group_name(existing.name) == desired_name:
            return f"name: {existing.name!r} → {desired_name!r}"
    if existing.kind != kind:
        return f"kind: {existing.kind!r} → {kind!r}"
    if existing.availability != availability:
        return f"availability: {existing.availability!r} → {availability!r}"
    if existing.description.strip() != _effective_description(existing.description, description).strip():
        return "description changed"
    existing_by_uid = {m.user_id: m.is_manager for m in existing.members}
    missing = [
        (u.user_id, mgr) for u, mgr in members
        if u.user_id not in existing_by_uid          # absent entirely
        or (mgr and not existing_by_uid[u.user_id])  # needs promotion to manager
    ]
    if missing:
        return f"members changed (missing={missing})"
    return None


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
            selector = (
                f'input[type="checkbox"][name*="mailman_list_attributes"]'
                f'[name*="{checkbox_field}"]'
            )
            cb = page.locator(selector)
            if cb.count() > 0 and not cb.first.is_checked():
                page.evaluate(
                    "(sel) => { const el = document.querySelector(sel);"
                    " if (el) el.checked = true; }",
                    selector,
                )

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
    prefetched_detail: Optional[GatherGroup] = None,
    desired_name: Optional[str] = None,
) -> Optional[str]:
    """Create or update a Gather group; return its group_id (str) or None on failure."""
    name = desired_name if desired_name is not None else circle.name
    kind = _group_kind(circle)
    availability = "closed"

    if existing is not None:
        existing_detail = prefetched_detail or _fetch_group_detail(page, base_url, existing)
        update_reason = _group_needs_update(existing_detail, kind, availability, description, members, name)
        if not update_reason:
            log("INFO", "group", f"Up to date, skipping: {circle.name}")
            return existing.group_id

        log("DEBUG", "update_reason", f"{circle.name}: {update_reason}")
        if dry_run:
            log("DRY-RUN", "update_group", circle.name)
            return existing.group_id

        try:
            page.goto(
                f"{base_url}/groups/{existing.group_id}/edit", wait_until="networkidle"
            )
            eff_desc = _effective_description(existing_detail.description, description)
            _fill_group_basics(page, name, kind, availability, eff_desc)

            # Sync members: add missing and fix manager status; never remove
            # manually-added members who aren't in the spreadsheet.
            existing_by_uid = {m.user_id: m.is_manager for m in existing_detail.members}
            desired_map = {u.user_id: (u, is_mgr) for u, is_mgr in members}

            for uid, (user, is_mgr) in desired_map.items():
                if uid not in existing_by_uid:
                    _add_inline_member(page, user, is_mgr)
                elif is_mgr and not existing_by_uid[uid]:
                    # Promote to manager (never demote — preserve manual changes)
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
            _fill_group_basics(page, name, kind, availability, description)
            for user, is_mgr in members:
                _add_inline_member(page, user, is_mgr)
            if not _submit_group_form(page, circle.name):
                return None

            post_submit_url = page.url
            log("DEBUG", "create_group", f"Post-submit URL: {post_submit_url}")
            screenshot(page, f"group_create_postsubmit_{circle.name[:20]}")

            # After creation Gather redirects to /groups; find the new group by name
            group_id = _find_group_in_list(page, base_url, name)
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


def _effective_description(existing_desc: str, desired_desc: str) -> str:
    """Return the description to write when updating an existing group.

    If the existing group already has a description, it is preserved.
    If the existing description is empty, desired_desc is used as-is.
    """
    return existing_desc if existing_desc.strip() else desired_desc


def _ensure_circle_wiki_page(
    page: Page, base_url: str, circle_name: str, dry_run: bool
) -> bool:
    """Create a blank wiki page for a circle if one does not already exist."""
    slug = _circle_wiki_slug(circle_name)
    title = f"{circle_name} Wiki"
    try:
        page.goto(f"{base_url}/wiki/{slug}", wait_until="networkidle")
        page_title = page.title()
        page_exists = (
            "Exception" not in page_title
            and "Error" not in page_title
            and "404" not in page_title
            and page.locator("h1").count() > 0
        )
        if page_exists:
            log("INFO", "circle_wiki", f"'{title}' already exists, skipping")
            return True
        if dry_run:
            log("DRY-RUN", "create_circle_wiki", title)
            return True
        page.goto(f"{base_url}/wiki/new", wait_until="networkidle")
        if page.locator("form").count() == 0:
            log("ERROR", "create_circle_wiki", title, "New wiki page form not found")
            return False
        title_el = page.locator('input[name="wiki_page[title]"], #wiki_page_title')
        if title_el.count() > 0:
            title_el.fill(title)
        if page.locator(".CodeMirror").count() == 0:
            log("ERROR", "create_circle_wiki", title, "Editor not found on new page form")
            return False
        _codemirror_set(page, "")
        page.locator('input[name="commit"]').click()
        page.wait_for_load_state("networkidle")
        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"wiki_circle_err_{slug[:20]}")
            log("ERROR", "create_circle_wiki", title, err[:200])
            return False
        log("INFO", "create_circle_wiki", f"Created: '{title}'")
        return True
    except Exception as e:
        screenshot(page, f"wiki_circle_exc_{slug[:20]}")
        log("ERROR", "create_circle_wiki", title, str(e))
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def process(
    base_url: str,
    email: str,
    password: str,
    sheet_url: str = DEFAULT_SHEET_URL,
    dry_run: bool = False,
    circle_prefix: Optional[str] = None,
    create_lists: bool = False,
):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start", f"base_url={base_url} dry_run={dry_run}")

    log("INFO", "fetch_sheet", sheet_url)
    csv_text = fetch_sheet(sheet_url)
    circles = parse_sheet(csv_text)
    log("INFO", "parse_sheet", f"{len(circles)} circles parsed")

    if circle_prefix is not None:
        circles = _filter_circles(circles, circle_prefix)
        log("INFO", "filter", f"Processing 1 circle: {circles[0].name!r}")

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
        log("INFO", "fetch_users", f"{len(gather_users)} users found")

        gather_groups = fetch_all_gather_groups(page, base_url)
        log("INFO", "fetch_groups", f"{len(gather_groups)} groups found")

        group_urls: dict[str, str] = {}
        # Maps circle.name → the actual Gather group name (may differ via suffix/alias rules).
        gather_name_map: dict[str, str] = {}
        stats = {
            "created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": 0,
            "list_created": 0, "list_failed": 0,
            "wiki_index_ok": False,
            "gdrive_linked": 0, "gdrive_failed": 0, "gdrive_not_found": 0,
        }

        for circle in circles:
            # Populate maps with derived-from-spreadsheet fallbacks so the
            # hierarchy and wiki loop always have values even when group lookup
            # or member resolution fails and we hit an early `continue`.
            _fallback_name = _canonical_group_name(circle.name)
            gather_name_map[circle.name] = _fallback_name

            try:
                existing = find_matching_group(circle, gather_groups)
            except ValueError as e:
                log("ERROR", "find_group", circle.name, str(e))
                stats["errors"] += 1
                continue

            # Override with the actual Gather group name (may differ from the
            # spreadsheet name, e.g. "Technology Circle" vs "Technology").
            # "Work Group" is canonicalized to "Working Group".
            gather_name = _canonical_group_name(
                existing.name if existing is not None else circle.name
            )
            gather_name_map[circle.name] = gather_name

            # Pre-fetch the group detail for existing groups so we can:
            # (a) pass existing member IDs to resolve_group_members for disambiguation,
            # (b) detect any existing wiki link in the description,
            # (c) avoid a second page load inside create_or_update_group.
            prefetched_detail: Optional[GatherGroup] = None
            if existing is not None:
                prefetched_detail = _fetch_group_detail(page, base_url, existing)

            existing_member_ids = (
                {m.user_id for m in prefetched_detail.members}
                if prefetched_detail is not None else None
            )

            try:
                members, remaining_consultants = resolve_group_members(
                    circle, gather_users, existing_member_ids
                )
            except ValueError as e:
                log("ERROR", "resolve_members", circle.name, str(e))
                stats["errors"] += 1
                continue

            description = build_description(circle, remaining_consultants)
            log("DEBUG", "description", f"{circle.name}: {len(description)} chars")

            group_id = create_or_update_group(
                page, base_url, circle, existing, members, description, dry_run,
                prefetched_detail=prefetched_detail,
                desired_name=gather_name,
            )

            if group_id is None:
                stats["failed"] += 1
                if existing is not None:
                    log("WARN", "group_url", circle.name, "group update failed; using existing URL for wiki/hierarchy")
                    group_urls[circle.name] = f"/groups/{existing.group_id}"
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
            # Skipped unless --mailman was passed (create_lists=True).
            if group_id != "dry-run" and create_lists:
                list_name = _circle_name_to_list_name(circle.name)
                ok = _ensure_mailman_list(page, base_url, group_id, list_name, dry_run)
                stats["list_created" if ok else "list_failed"] += 1

        # Build gdrive URL map for all circles (used for group access and hierarchy).
        gdrive_links = fetch_gdrive_links(page, base_url)
        gdrive_url_map: dict[str, str] = {}
        for circle in circles:
            gather_name = gather_name_map.get(circle.name, circle.name)
            gdrive_url_map[circle.name] = find_gdrive_link(gather_name, gdrive_links) or ""

        # Link each circle's Gather group to its gdrive entry with Content manager access.
        for circle in circles:
            gdrive_href = gdrive_url_map.get(circle.name, "")
            gather_name = gather_name_map.get(circle.name, circle.name)
            if not gdrive_href:
                stats["gdrive_not_found"] += 1
                continue
            if circle.name not in group_urls:
                continue
            ok = ensure_gdrive_group_access(
                page, base_url, gdrive_href, gather_name, dry_run
            )
            stats["gdrive_linked" if ok else "gdrive_failed"] += 1

        if circle_prefix is None:
            # Log any circles whose gdrive entries are missing before building hierarchy.
            for _c in circles:
                _gu = gdrive_url_map.get(_c.name, "")
                _gurl = group_urls.get(_c.name, "")
                if not _gu:
                    log("INFO", "hierarchy_links",
                        f"{_c.name}: gdrive={_gu!r} group={_gurl!r}")
            hierarchy_content = _build_hierarchy_content(
                circles, gather_name_map, group_urls, gdrive_url_map
            )
            stats["wiki_index_ok"] = _ensure_hierarchy_page(
                page, base_url, hierarchy_content, dry_run
            )
        else:
            stats["wiki_index_ok"] = True

        browser.close()

    log("INFO", "done", str(stats))
    close_log()


def fetch_gdrive_links(page: Page, base_url: str) -> list[tuple[str, str]]:
    """Scrape the /gdrive page and return (link_text, href) pairs for all links."""
    try:
        page.goto(f"{base_url}/gdrive", wait_until="networkidle")
        links: list[tuple[str, str]] = []
        for link in page.locator("a[href^='/gdrive/']").all():
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()
            if text and href:
                links.append((text, href))
        log("INFO", "fetch_gdrive", f"{len(links)} links found")
        return links
    except Exception as e:
        log("ERROR", "fetch_gdrive", "", str(e))
        return []


def _links_block(circle_name: str, group_url: str, gdrive_href: str = "") -> str:
    links = []
    if group_url:
        links.append(f"[Members]({group_url})")
    if gdrive_href:
        links.append(f"[Documents]({gdrive_href})")
    if not links:
        return ""
    return f"{circle_name}: {' | '.join(links)}"


# Matches the new single-line links block written by this script.
# Two alternatives: (1) Members with optional Documents, (2) Documents-only (when no group URL).
_LINKS_BLOCK_RE = re.compile(
    r"[^\n]+: \[Members\]\([^)]*\)(?: \| \[[^\]]*\]\(/gdrive/[^)]+\))?"
    r"|[^\n]+: \[Documents\]\(/gdrive/[^)]+\)"
)
# Matches the old multi-line links block from a previous script version.
_OLD_LINKS_BLOCK_RE = re.compile(
    r"[^\n]*'s:\n\* \[Members\]\([^)]*\)(?:\n\* \[[^\]]*\]\(/gdrive/[^)]+\))?"
)
# Matches the old single-line bare gdrive markdown link.
_OLD_GDRIVE_RE = re.compile(r"\[([^\]]*)\]\((/gdrive/[^)]+)\)")


def _apply_gdrive_link(content: str, circle_name: str, gdrive_href: str, group_url: str) -> tuple[Optional[str], str]:
    """Compute the updated wiki content after applying the Members (+ optional Google Drive) links block.

    gdrive_href may be empty; in that case the Google Drive link is omitted.
    The block is a single line placed at the top of the page.

    Returns (new_content, action) where action is one of:
      "skip"   — block already exists at top with correct content; no change needed
      "update" — existing block(s) found but wrong/duplicated; new_content corrects it
      "add"    — no block yet; new_content has the block prepended
    """
    expected = _links_block(circle_name, group_url, gdrive_href)

    new_block_matches = list(_LINKS_BLOCK_RE.finditer(content))
    old_block_matches = list(_OLD_LINKS_BLOCK_RE.finditer(content))
    # Search for bare gdrive links only outside new-format blocks to avoid
    # falsely matching the /gdrive/ URL inside our own correct block.
    content_outside_blocks = _LINKS_BLOCK_RE.sub("", content)
    old_gdrive_match = _OLD_GDRIVE_RE.search(content_outside_blocks) if gdrive_href else None

    # Skip only when there is exactly one block, it is correct, it is at the top,
    # and there is nothing stale left to clean up.
    if (
        len(new_block_matches) == 1
        and new_block_matches[0].group(0) == expected
        and new_block_matches[0].start() == 0
        and not old_block_matches
        and not old_gdrive_match
    ):
        return None, "skip"

    # Strip every existing new-format block, old multi-line block, and (when a
    # gdrive href is available) every old bare gdrive link, then prepend the
    # single correct block.
    body = _LINKS_BLOCK_RE.sub("", content)
    body = _OLD_LINKS_BLOCK_RE.sub("", body)
    if gdrive_href:
        body = _OLD_GDRIVE_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    new_content = expected + ("\n\n" + body if body else "\n")
    had_block = bool(new_block_matches or old_block_matches or old_gdrive_match)
    return new_content, "update" if had_block else "add"


def _ensure_gdrive_link_on_wiki(
    page: Page, base_url: str, wiki_slug: str, gdrive_href: str,
    circle_name: str, group_url: str, dry_run: bool,
) -> bool:
    """Add or correct the Members (+ optional Google Drive) links block on the circle's wiki page."""
    try:
        page.goto(f"{base_url}/wiki/{wiki_slug}/edit", wait_until="networkidle")
        if page.locator(".CodeMirror").count() == 0:
            log("ERROR", "gdrive_wiki", wiki_slug, "Editor not found on wiki edit page")
            return False
        content = _codemirror_get(page)
        if not group_url and not gdrive_href:
            log("WARN", "gdrive_wiki", wiki_slug, "no group URL and no gdrive link; removing stale blocks")
            cleaned = _LINKS_BLOCK_RE.sub("", content)
            cleaned = _OLD_LINKS_BLOCK_RE.sub("", cleaned)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            if cleaned == content.strip():
                return True
            if not dry_run:
                _codemirror_set(page, cleaned)
                page.locator('input[name="commit"]').click()
                page.wait_for_load_state("networkidle")
                log("INFO", "gdrive_wiki", f"{wiki_slug}: removed stale blocks (no group URL, no gdrive)")
            return True
        if not group_url:
            log("INFO", "gdrive_wiki", wiki_slug, "no Gather group URL; writing gdrive-only link")
        new_content, action = _apply_gdrive_link(content, circle_name, gdrive_href, group_url)
        if action == "skip":
            log("INFO", "gdrive_wiki", f"{wiki_slug}: links block up to date, skipping")
            return True
        if dry_run:
            log("DRY-RUN", f"{action}_links_block", f"{wiki_slug} (gdrive: {gdrive_href or 'none'})")
            return True
        _codemirror_set(page, new_content)
        page.locator('input[name="commit"]').click()
        page.wait_for_load_state("networkidle")
        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"gdrive_wiki_err_{wiki_slug[:20]}")
            log("ERROR", f"{action}_links_block", wiki_slug, err[:200])
            return False
        log("INFO", f"{action}_links_block", f"{wiki_slug}: {action}d (gdrive: {gdrive_href or 'none'})")
        return True
    except Exception as e:
        screenshot(page, f"gdrive_wiki_exc_{wiki_slug[:20]}")
        log("ERROR", "gdrive_wiki", wiki_slug, str(e))
        return False


def _filter_circles(circles: list[Circle], prefix: str) -> list[Circle]:
    """Return the subset of circles whose name starts with prefix (case-insensitive).

    Exits with an error message if the prefix matches zero or more than one circle.
    """
    prefix_lower = prefix.strip().lower()
    matches = [c for c in circles if c.name.lower().startswith(prefix_lower)]
    if len(matches) == 1:
        return matches
    if not matches:
        names = ", ".join(repr(c.name) for c in circles)
        sys.exit(
            f"Error: --circle {prefix!r} does not match any circle name.\n"
            f"Available: {names}"
        )
    names = ", ".join(repr(c.name) for c in matches)
    sys.exit(
        f"Error: --circle {prefix!r} is ambiguous; matches: {names}"
    )


def cli():
    parser = argparse.ArgumentParser(
        description="Populate Gather groups from a Google Sheets circle hierarchy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )

    parser.add_argument(
        "-s", "--sheet-url", default=DEFAULT_SHEET_URL,
        help="Google Sheets URL (edit or export format)",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would happen without making any changes",
    )
    parser.add_argument(
        "-c", "--circle", default=None, metavar="PREFIX",
        help="Process only the circle whose name starts with PREFIX (must match exactly one)",
    )
    parser.add_argument(
        "-m", "--mailman", action="store_true", default=False,
        help="Create Mailman email lists for groups (disabled by default)",
    )
    args = parser.parse_args()
    email, password = load_credentials()
    process(args.base_url, email, password, args.sheet_url, args.dry_run, args.circle, args.mailman)


if __name__ == "__main__":
    cli()
