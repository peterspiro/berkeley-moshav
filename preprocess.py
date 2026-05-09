#!/usr/bin/env python3
"""
Preprocesses a TSV spreadsheet of community members into household clusters.

Usage:
    python preprocess.py members.tsv
    python preprocess.py members.tsv --output households.json
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Member:
    first_name: str
    last_name: str
    email: str
    phone: str
    child: bool = False
    full_access: bool = True


@dataclass
class Household:
    household_name: str
    unit_num: Optional[int]
    unit_suffix: Optional[str]
    members: list = field(default_factory=list)


# ── Union-Find ────────────────────────────────────────────────────────────────

class UnionFind:
    def __init__(self):
        self._parent = {}

    def find(self, x):
        self._parent.setdefault(x, x)
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x, y):
        self._parent[self.find(x)] = self.find(y)

    def groups(self):
        groups = {}
        for x in self._parent:
            root = self.find(x)
            groups.setdefault(root, set()).add(x)
        return list(groups.values())


# ── Name helpers ──────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def strip_pronunciation(name: str) -> str:
    """Remove trailing parenthetical annotations from a First Name cell.

    E.g. 'Ayala (a-ya-LA)' -> 'Ayala'
    """
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


def derive_household_name(last_names: list[str]) -> str:
    unique = sorted(set(ln.strip() for ln in last_names if ln.strip()))
    name = "-".join(unique)
    return name[:32]  # Gather's max household name length


def parse_unit(unit_str: str) -> tuple[Optional[int], Optional[str]]:
    """Parse '411' or '20-2A' into (unit_num, unit_suffix)."""
    if not unit_str or not unit_str.strip():
        return None, None
    m = re.match(r"^(\d+)(.*)$", unit_str.strip())
    if not m:
        return None, unit_str.strip() or None
    num = int(m.group(1))
    suffix = m.group(2).lstrip("-").strip() or None
    return num, suffix


def is_international_phone(phone: str) -> bool:
    """Return True if the phone number is non-US international.

    Numbers starting with '+' are international unless they are US +1 numbers
    (11 digits starting with 1).
    """
    s = phone.strip()
    if not s.startswith("+"):
        return False
    digits = "".join(c for c in s if c.isdigit())
    return not (len(digits) == 11 and digits.startswith("1"))


# ── "Others in the Household" parsing ────────────────────────────────────────

def _parse_others_entry(entry: str) -> tuple[str, Optional[str]]:
    """Parse one entry from Others in the Household.

    Returns (name, qualifier) where qualifier is the text inside the trailing
    parentheses, or None if there are no parentheses.
    E.g. 'Luna Liliana (2)' -> ('Luna Liliana', '2')
         'Sandra Rosenblum'  -> ('Sandra Rosenblum', None)
         'Justin Radick (uncle)' -> ('Justin Radick', 'uncle')
    """
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", entry.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return entry.strip(), None


def _is_child_qualifier(qualifier: Optional[str]) -> bool:
    """Return True if this qualifier marks a child (a number or 'infant')."""
    if qualifier is None:
        return False
    q = qualifier.strip().lower()
    return q == "infant" or bool(re.match(r"^\d+$", q))


def parse_others(others_raw: str) -> list[tuple[str, Optional[str]]]:
    """Split 'Others in the Household' by commas and semicolons, parse each entry.

    Returns a list of (name, qualifier) tuples, skipping blank entries.
    """
    results = []
    for part in re.split(r"[,;]", others_raw):
        part = part.strip()
        if not part:
            continue
        name, qualifier = _parse_others_entry(part)
        if name:
            results.append((name, qualifier))
    return results


def resolve_adult_name(
    raw_name: str,
    current_last: str,
    name_to_row: dict,
    warnings: list,
) -> Optional[str]:
    """Resolve an adult name from the Others column to a key in name_to_row.

    Rules:
    - 2 names (first last): look up directly; if not found, return None.
    - 3+ names: treat as first + last (ignore middle); same lookup.
    - 1 name (first only): try first + current_last first; then search all
      rows by first name. Return None if not found, raise on ambiguous match.
    """
    parts = raw_name.strip().split()
    if not parts:
        return None

    if len(parts) == 1:
        first, last = parts[0], None
    elif len(parts) == 2:
        first, last = parts[0], parts[1]
    else:
        # 3+ names: ignore middle name(s), use first and last
        first, last = parts[0], parts[-1]

    if last:
        key = normalize_name(f"{first} {last}")
        return key if key in name_to_row else None

    # Only first name given: try first + current row's last name
    key = normalize_name(f"{first} {current_last}")
    if key in name_to_row:
        return key

    # Fall back to unique first-name match across all rows
    matches = [n for n in name_to_row if n.split()[0].lower() == first.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        warnings.append(f"Ambiguous name '{first}' in Others column: matches {matches}")
    return None


# ── Main parsing logic ────────────────────────────────────────────────────────

def preprocess(tsv_path: str) -> list[dict]:
    rows = []
    with open(tsv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            rows.append(row)

    if not rows:
        return []

    # Include Member status; "Member & Consultant" and similar variants qualify.
    rows = [r for r in rows if r.get("Status", "").lower().startswith("member")]

    if not rows:
        return []

    # Strip pronunciation guides from First Name cells, e.g. "Ayala (a-ya-LA)"
    for row in rows:
        if "First Name" in row:
            row["First Name"] = strip_pronunciation(row["First Name"])

    # Build full-name → row mapping
    name_to_row: dict[str, dict] = {}
    for row in rows:
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        if first or last:
            name_to_row[normalize_name(f"{first} {last}")] = row

    uf = UnionFind()
    warnings = []

    # Seed every row's own name
    for name in name_to_row:
        uf.find(name)

    # Union adults via "Others in the Household"; skip children
    for name, row in name_to_row.items():
        current_last = row.get("Last Name", "").strip()
        others_raw = row.get("Others in the Household", "")
        for other_name, qualifier in parse_others(others_raw):
            if _is_child_qualifier(qualifier):
                continue
            resolved = resolve_adult_name(other_name, current_last, name_to_row, warnings)
            if resolved:
                uf.union(name, resolved)

    # Map union-find root → list of known member names in that group
    groups = uf.groups()
    root_to_known: dict[str, list[str]] = {}
    for group in groups:
        known = [n for n in group if n in name_to_row]
        if known:
            root = uf.find(known[0])
            root_to_known.setdefault(root, []).extend(known)

    households = []

    for root, member_names in root_to_known.items():
        member_rows = [name_to_row[n] for n in member_names]

        # Validate: all members should share the same unit number
        units = [r.get("Unit #", "").strip() for r in member_rows]
        non_blank_units = [u for u in units if u]
        if len(set(non_blank_units)) > 1:
            warnings.append(f"Unit # mismatch in household {member_names}: {units}")

        unit_str = non_blank_units[0] if non_blank_units else ""
        unit_num, unit_suffix = parse_unit(unit_str)

        # Build adult members and collect last names for household naming
        adult_members = []
        last_names = []
        for row in member_rows:
            first = row.get("First Name", "").strip()
            last = row.get("Last Name", "").strip()
            email = row.get("Email Address", "").strip()
            phone = row.get("Phone", "").strip()
            pronouns = ""
            if is_international_phone(phone):
                pronouns = phone
                phone = ""
            if last:
                last_names.append(last)
            adult_members.append({
                "first_name": first,
                "last_name": last,
                "email": email,
                "phone": phone,
                "pronouns": pronouns,
                "child": False,
                "full_access": True,
            })

        # Derive household name before assigning it to children
        hh_name = derive_household_name(last_names)

        # Build child members from the Others column of every row in this household.
        # Children are identified by a numeric age or "infant" in parentheses.
        # Their first_name is all given names; last_name is the household name.
        child_members = []
        seen_child_names: set[str] = set()
        for row in member_rows:
            others_raw = row.get("Others in the Household", "")
            for other_name, qualifier in parse_others(others_raw):
                if not _is_child_qualifier(qualifier):
                    continue
                child_key = normalize_name(other_name)
                if child_key in seen_child_names:
                    continue
                seen_child_names.add(child_key)
                child_members.append({
                    "first_name": child_key,   # all given names as first_name
                    "last_name": hh_name,
                    "email": "",
                    "phone": "",
                    "child": True,
                    "full_access": False,
                })

        households.append({
            "household_name": hh_name,
            "unit_num": unit_num,
            "unit_suffix": unit_suffix,
            "members": adult_members + child_members,
        })

    # Sort for deterministic output
    households.sort(key=lambda h: h["household_name"])

    if warnings:
        print("WARNINGS:", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)

    return households


def main():
    parser = argparse.ArgumentParser(description="Preprocess member TSV into household JSON")
    parser.add_argument("tsv", help="Path to input TSV file")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    households = preprocess(args.tsv)
    output = json.dumps(households, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Wrote {len(households)} households to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
