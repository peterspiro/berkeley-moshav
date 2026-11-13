#!/usr/bin/env python3
"""
Keeps the maintenance tracker's Airtable base in step with its schema file.

The base's tables and fields are described in maintenance/schema.json (see
util/airtable_schema.py for the format, and maintenance-tracker-plan.md for
the design). This script compares a base with that file and adds what's
missing:

    diff    Show what the base lacks, and change nothing.
    apply   Create the missing tables and fields, and add missing options
            to select fields.
    seed    Load the starting rows in maintenance/seed.json (the building's
            units), adding rows that are missing and updating the others.

It never deletes or retypes anything; Airtable's API can't. What it can't
do is listed under "By hand": the views and forms, and making a formula a
table's primary field, which have to be done in Airtable (see
maintenance/README.md), and anything in the base that disagrees with the
file.

It works on the test base, "Building Maintenance (test)", unless told
otherwise: --real picks "Building Maintenance", and asks you to type the
base's name before changing it. Bases are found by name, so no base ID is
kept in the repo.

Requires an Airtable personal access token that can reach the base, with
the scopes data.records:read, data.records:write, schema.bases:read, and
schema.bases:write, in airtable_credentials (see README.md).

Usage:
    python maintenance_base.py diff
    python maintenance_base.py apply -n
    python maintenance_base.py apply
    python maintenance_base.py seed
    python maintenance_base.py diff --real
"""

import argparse
import json
import sys
from pathlib import Path

from util.airtable import Airtable, AirtableError
from util.airtable_schema import (
    PRIMARY_TYPES, SchemaError, airtable_field, describe, load_schema, plan)
from util.credentials import load_airtable_token

TEST_BASE = "Building Maintenance (test)"
REAL_BASE = "Building Maintenance"
SEED_FILE = Path(__file__).parent / "maintenance" / "seed.json"


def show(p, out=print) -> None:
    """Print a plan: what apply would do, what's left to a person, notes."""
    if p.actions:
        out("To do:")
        for action in p.actions:
            out(f"  {describe(action)}")
    if p.by_hand:
        out("By hand, in Airtable:")
        for line in p.by_hand:
            out(f"  {line}")
    if p.notes:
        out("Notes:")
        for line in p.notes:
            out(f"  {line}")
    if p.in_step and not p.notes:
        out("The base matches the schema file.")


def apply(client, base: str, schema: dict, out=print) -> None:
    """Carry out a plan's actions, then show what's left."""
    p = plan(schema, client.tables(base))
    tables = {t["name"]: t for t in client.tables(base)}
    ids = {name: t["id"] for name, t in tables.items()}
    for action in p.actions:
        out(describe(action))
        table = action["table"]
        if action["do"] == "create_table":
            assert action["primary"]["type"] in PRIMARY_TYPES
            made = client.create_table(base, table, [airtable_field(action["primary"])],
                                       action["description"])
            ids[table] = made["id"]
        elif action["do"] == "create_field":
            f = action["field"]
            made = client.create_field(base, ids[table], airtable_field(f, ids))
            if f["type"] == "link":
                # Airtable names the other side's field itself; give it ours.
                client.rename_field(base, ids[f["table"]],
                                    made["options"]["inverseLinkFieldId"], f["inverse"])
        elif action["do"] == "add_choices":
            client.add_select_choices(base, ids[table], action["field"],
                                      action["choices"], action["multiple"])
        elif action["do"] == "rename_field":
            fields = next(t for t in client.tables(base) if t["id"] == ids[table])["fields"]
            field_id = next(f["id"] for f in fields if f["name"] == action["from"])
            client.rename_field(base, ids[table], field_id, action["to"])
    out("")
    show(plan(schema, client.tables(base)), out)


def seed(client, base: str, seeds: dict, dry_run: bool, out=print) -> None:
    """Add or update each table's starting rows, matched by its key field."""
    for table, spec in seeds.items():
        key, rows = spec["key"], spec["rows"]
        have = {r["fields"].get(key) for r in client.list_records(base, table, [key])}
        new = [r for r in rows if r[key] not in have]
        out(f"{table}: {len(new)} to add, {len(rows) - len(new)} already there"
            f"{'' if dry_run else ' (brought up to date)'}.")
        if not dry_run:
            client.upsert_records(base, table, key, rows)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("command", choices=["diff", "apply", "seed"])
    parser.add_argument("--real", action="store_true",
                        help=f"work on {REAL_BASE!r} instead of {TEST_BASE!r}")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="show what apply or seed would do, and change nothing")
    args = parser.parse_args(argv)

    base_name = REAL_BASE if args.real else TEST_BASE
    changing = args.command != "diff" and not args.dry_run
    try:
        schema = load_schema()
        client = Airtable(load_airtable_token())
        base = client.find_base(base_name)
        print(f"Base: {base_name}")
        if args.real and changing:
            if input(f"This changes the real base. Type its name to go on: ").strip() != REAL_BASE:
                sys.exit("Stopped; nothing was changed.")
        if args.command == "seed":
            seed(client, base, json.loads(SEED_FILE.read_text()), args.dry_run)
        elif changing:
            apply(client, base, schema)
        else:
            show(plan(schema, client.tables(base)))
    except (AirtableError, SchemaError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
