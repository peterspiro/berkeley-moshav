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


# ── Name normalization ────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    return " ".join(name.strip().split())


def parse_child_name(name: str) -> tuple[str, str]:
    """Split 'First Last' into (first, last). Falls back gracefully."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return parts[0] if parts else name, ""


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


# ── Main parsing logic ────────────────────────────────────────────────────────

def preprocess(tsv_path: str) -> list[dict]:
    rows = []
    with open(tsv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            # Strip whitespace from all values
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            rows.append(row)

    if not rows:
        return []

    # Filter to Member status only
    rows = [r for r in rows if r.get("Status", "").lower() == "member"]

    if not rows:
        return []

    # Build full-name → row mapping (normalized)
    name_to_row: dict[str, dict] = {}
    for row in rows:
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        if first or last:
            name_to_row[normalize_name(f"{first} {last}")] = row

    uf = UnionFind()

    # Seed every row's own name
    for name in name_to_row:
        uf.find(name)

    # Union via "Others in the Household" cross-references
    for name, row in name_to_row.items():
        others_raw = row.get("Others in the Household", "")
        for other in others_raw.split(","):
            other = normalize_name(other)
            if other:
                # Union even if 'other' isn't a known row — creates a phantom node
                uf.union(name, other)

    # Map root → list of known row names in that group
    groups = uf.groups()
    root_to_known: dict[str, list[str]] = {}
    for group in groups:
        known = [n for n in group if n in name_to_row]
        if known:
            root = uf.find(known[0])
            root_to_known.setdefault(root, []).extend(known)

    warnings = []
    households = []

    for root, member_names in root_to_known.items():
        member_rows = [name_to_row[n] for n in member_names]

        # Validate: all members should share the same unit number
        units = [r.get("Unit #", "").strip() for r in member_rows]
        non_blank_units = [u for u in units if u]
        if len(set(non_blank_units)) > 1:
            warnings.append(
                f"Unit # mismatch in household {member_names}: {units}"
            )

        unit_str = non_blank_units[0] if non_blank_units else ""
        unit_num, unit_suffix = parse_unit(unit_str)

        # Build adult members
        adult_members = []
        last_names = []
        for row in member_rows:
            first = row.get("First Name", "").strip()
            last = row.get("Last Name", "").strip()
            email = row.get("Email Address", "").strip()
            phone = row.get("Phone", "").strip()
            if last:
                last_names.append(last)
            adult_members.append({
                "first_name": first,
                "last_name": last,
                "email": email,
                "phone": phone,
                "child": False,
                "full_access": True,
            })

        # Build child members from the Kids column of any row in this household
        child_members = []
        seen_kid_names: set[str] = set()
        for row in member_rows:
            kids_raw = row.get("Kids", "").strip()
            if not kids_raw:
                continue
            for kid_name in kids_raw.split(","):
                kid_name = normalize_name(kid_name)
                if not kid_name or kid_name in seen_kid_names:
                    continue
                seen_kid_names.add(kid_name)
                first, last = parse_child_name(kid_name)
                if last:
                    last_names.append(last)
                child_members.append({
                    "first_name": first,
                    "last_name": last,
                    "email": "",
                    "phone": "",
                    "child": True,
                    "full_access": False,
                })

        hh_name = derive_household_name(last_names)

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
