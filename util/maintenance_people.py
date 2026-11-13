"""
The maintenance tracker's People table, from the Gather directory.

People holds the adults in Gather's directory, so that a request can be
linked to the member who reported it (by either of their email addresses)
and to the member who's looking into it. Gather stays the directory: the
fields in SYNCED_FIELDS are overwritten from it on each sync, and the
others (Circle member, Coordinator) are the circle's to set in Airtable.

A person is matched by their Gather ID. Nobody is ever deleted, since
requests link to them: someone with a departure date that has passed, or
who's no longer in the directory, is marked Moved out. Rows without a
Gather ID, added by hand for people outside the community, are left alone.
"""

from datetime import date

from util.gather_utils import GatherUser

SYNCED_FIELDS = ("Name", "Email", "Other email", "Phone", "Location", "Gather ID", "Moved out")
KEY = "Gather ID"


def _has_left(departure: str, today: date) -> bool:
    """Whether a directory departure date has passed. One that can't be
    read counts as passed."""
    if not departure:
        return False
    try:
        return date.fromisoformat(departure[:10]) <= today
    except ValueError:
        return True


def location_name(unit: str, locations: dict[str, str]) -> str | None:
    """The Locations row for a directory unit ("411", or "411A" for a unit
    number with a suffix, which falls back to "411"); None if there's none."""
    for candidate in (unit, unit.rstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")):
        if candidate and f"Unit {candidate}" in locations:
            return f"Unit {candidate}"
    return None


def people_rows(users: list[GatherUser], locations: dict[str, str],
                today: date) -> tuple[list[dict], list[str]]:
    """The People row for each adult in the directory, as Airtable fields,
    and warnings. `locations` is {Locations row name: record ID}."""
    rows, warnings = [], []
    for u in users:
        if u.child or not u.full_name:
            continue
        place = location_name(u.unit, locations)
        if u.unit and not place:
            warnings.append(f"{u.full_name}: no Locations row for unit {u.unit!r}.")
        other = u.google_email if u.google_email != u.email else ""
        rows.append({
            "Name": u.full_name,
            "Email": u.email or None,
            "Other email": other or None,
            "Phone": u.phone or None,
            "Location": [locations[place]] if place else [],
            "Gather ID": u.user_id,
            "Moved out": _has_left(u.departure_date, today),
        })
    return rows, warnings


def _same(field: str, wanted, have) -> bool:
    """Airtable leaves out empty fields and unticked boxes."""
    empty = [] if field == "Location" else False if field == "Moved out" else None
    return (wanted if wanted else empty) == (have if have else empty)


def changes(rows: list[dict], records: list[dict]) -> tuple[list[dict], list[str]]:
    """Compare the directory's rows with People's records. Returns the rows
    to write (new people, changed people, and people gone from the
    directory, to mark Moved out) and a line describing each."""
    have = {r["fields"][KEY]: r["fields"] for r in records if r["fields"].get(KEY)}
    to_write, lines = [], []
    for row in rows:
        old = have.get(row[KEY])
        if old is None:
            to_write.append(row)
            lines.append(f"Add {row['Name']}.")
            continue
        changed = [f for f in SYNCED_FIELDS if not _same(f, row[f], old.get(f))]
        if changed:
            to_write.append(row)
            lines.append(f"Update {row['Name']}: {', '.join(changed)}.")
    in_directory = {row[KEY] for row in rows}
    for gather_id, old in have.items():
        if gather_id not in in_directory and not old.get("Moved out"):
            to_write.append({KEY: gather_id, "Moved out": True})
            lines.append(f"Mark {old.get('Name', gather_id)} as moved out (not in the directory).")
    return to_write, lines
