"""
The maintenance tracker's base as a file: reading `maintenance/schema.json`,
checking it, and working out what a base lacks compared with it.

The schema file names tables, fields, and views; it holds no Airtable IDs.
Each field has a `type`, one of FIELD_TYPES' keys. A `select` or
`multiSelect` field lists its `choices`, or names a list in the file's
`choice_lists` as "@name". A `link` field names the `table` it links to and
the name of its `inverse`, the field Airtable makes in that table to show
the link from the other side; a link is declared once, on either side. A
`formula` field gives its `formula`, with field names in braces. A table's
first field is its primary field. `views` are the views each table should
have: the API can't make them, so they're only checked.

plan() never proposes deleting or retyping anything, since the API can do
neither; what doesn't match is reported for a person to look at.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_FILE = Path(__file__).parent.parent / "maintenance" / "schema.json"
TIME_ZONE = "America/Los_Angeles"

_DATE = {"dateFormat": {"name": "local"}}
# Our type -> (Airtable's type, the options a new field of it is created with)
FIELD_TYPES = {
    "text": ("singleLineText", None),
    "longText": ("multilineText", None),
    "number": ("number", {"precision": 0}),
    "date": ("date", _DATE),
    "dateTime": ("dateTime", {**_DATE, "timeFormat": {"name": "12hour"}, "timeZone": TIME_ZONE}),
    "checkbox": ("checkbox", {"icon": "check", "color": "greenBright"}),
    "email": ("email", None),
    "phone": ("phoneNumber", None),
    "url": ("url", None),
    "attachments": ("multipleAttachments", None),
    "autoNumber": ("autoNumber", None),
    "createdTime": ("createdTime", None),
    "select": ("singleSelect", None),
    "multiSelect": ("multipleSelects", None),
    "link": ("multipleRecordLinks", None),
    "formula": ("formula", None),
}
# Types a table's primary field can be created as.
PRIMARY_TYPES = ("text", "autoNumber")
# Created after every other field: links need their tables, and formulas
# the fields they name.
_LATER_TYPES = ("link", "formula")


class SchemaError(Exception):
    pass


def load_schema(path: Path = SCHEMA_FILE) -> dict:
    """The schema file, with each "@name" choice list filled in, checked."""
    schema = json.loads(Path(path).read_text())
    lists = schema.get("choice_lists", {})
    for table in schema["tables"]:
        for f in table["fields"]:
            choices = f.get("choices")
            if isinstance(choices, str):
                if not choices.startswith("@") or choices[1:] not in lists:
                    raise SchemaError(f"{table['name']}.{f['name']}: no choice list {choices!r}")
                f["choices"] = list(lists[choices[1:]])
    problems = schema_problems(schema)
    if problems:
        raise SchemaError("Problems in the schema file:\n  " + "\n  ".join(problems))
    return schema


def expected_fields(schema: dict) -> dict[str, dict[str, dict]]:
    """{table name: {field name: field}} for every field each table should
    have, including the inverses of links declared in other tables. An
    inverse is {"type": "link", "table": <the declaring table>, "inverse":
    <the declared field's name>, "declared_in": ...}."""
    fields = {t["name"]: {f["name"]: f for f in t["fields"]} for t in schema["tables"]}
    for t in schema["tables"]:
        for f in t["fields"]:
            if f["type"] == "link" and f["table"] in fields:
                fields[f["table"]].setdefault(f["inverse"], {
                    "name": f["inverse"], "type": "link", "table": t["name"],
                    "inverse": f["name"], "declared_in": t["name"]})
    return fields


def schema_problems(schema: dict) -> list[str]:
    """What's wrong with a schema file, as messages; empty if nothing."""
    problems = []
    names = [t["name"] for t in schema["tables"]]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"Two tables are named {name!r}.")
    for t in schema["tables"]:
        where = t["name"]
        if not t["fields"]:
            problems.append(f"{where} has no fields.")
            continue
        if t["fields"][0]["type"] not in PRIMARY_TYPES:
            problems.append(f"{where}: its first (primary) field must be one of {PRIMARY_TYPES}.")
        seen = set()
        for f in t["fields"]:
            at = f"{where}.{f['name']}"
            if f["name"].lower() in seen:
                problems.append(f"{at} is declared twice.")
            seen.add(f["name"].lower())
            if f["type"] not in FIELD_TYPES:
                problems.append(f"{at}: unknown type {f['type']!r}.")
            elif f["type"] in ("select", "multiSelect"):
                choices = f.get("choices") or []
                if not choices or len(set(choices)) != len(choices):
                    problems.append(f"{at} needs a list of distinct choices.")
            elif f["type"] == "link":
                if f.get("table") not in names:
                    problems.append(f"{at} links to a table that isn't in the file: {f.get('table')!r}.")
                if not f.get("inverse"):
                    problems.append(f"{at} needs an inverse field's name.")
            elif f["type"] == "formula" and not f.get("formula"):
                problems.append(f"{at} needs a formula.")
        for f in t["fields"]:
            # An inverse another table's link puts here mustn't share a name
            # with a field of this table (or with another inverse).
            if f["type"] == "link" and f.get("table") in names and f.get("inverse"):
                target = next(x for x in schema["tables"] if x["name"] == f["table"])
                clashes = [g for g in target["fields"] if g["name"].lower() == f["inverse"].lower()]
                others = [(u["name"], g["name"]) for u in schema["tables"] for g in u["fields"]
                          if g["type"] == "link" and g.get("table") == f["table"]
                          and (g.get("inverse") or "").lower() == f["inverse"].lower()
                          and (u["name"], g["name"]) != (t["name"], f["name"])]
                if clashes or others:
                    problems.append(
                        f"{where}.{f['name']}: its inverse {f['inverse']!r} is already a field of {f['table']}.")
        views = [v["name"] for v in t.get("views", [])]
        if len(set(views)) != len(views):
            problems.append(f"{where} lists a view twice.")
    return problems


@dataclass
class Plan:
    """What a base lacks. `actions` are done in order by apply; `by_hand`
    and `notes` are for a person."""
    actions: list[dict] = field(default_factory=list)
    by_hand: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def in_step(self) -> bool:
        return not self.actions and not self.by_hand


def airtable_field(f: dict, table_ids: dict[str, str] | None = None) -> dict:
    """The body that creates field `f` through the API. A link needs
    `table_ids`, {table name: ID}."""
    kind, options = FIELD_TYPES[f["type"]]
    body = {"name": f["name"], "type": kind}
    if f["type"] in ("select", "multiSelect"):
        options = {"choices": [{"name": c} for c in f["choices"]]}
    elif f["type"] == "link":
        options = {"linkedTableId": table_ids[f["table"]]}
    elif f["type"] == "formula":
        options = {"formula": f["formula"]}
    if options:
        body["options"] = options
    return body


def plan(schema: dict, tables: list[dict]) -> Plan:
    """Compare a base's `tables` (as the API lists them) with the schema."""
    p = Plan()
    actual = {t["name"]: t for t in tables}
    wanted = expected_fields(schema)
    later = []  # links and formulas, after every table and plain field

    for t in schema["tables"]:
        name, primary = t["name"], t["fields"][0]
        have = actual.get(name)
        have_fields = {f["name"]: f for f in have["fields"]} if have else {}
        if not have:
            p.actions.append({"do": "create_table", "table": name, "primary": primary,
                              "description": t.get("description", "")})
            have_fields = {primary["name"]: None}  # made with the table
        else:
            first = next(f for f in have["fields"] if f["id"] == have["primaryFieldId"])
            if first["name"] != primary["name"]:
                p.by_hand.append(f"{name}: make {primary['name']!r} the primary field "
                                 f"(it's {first['name']!r}).")

        for f in t["fields"]:
            got = have_fields.get(f["name"])
            if f["name"] not in have_fields:
                action = {"do": "create_field", "table": name, "field": f}
                (later if f["type"] in _LATER_TYPES else p.actions).append(action)
            elif got is not None:
                _compare_field(p, name, f, got, actual, wanted)

        if have:
            # Fields the base has that the file doesn't know about.
            for extra in sorted(set(have_fields) - set(wanted[name])):
                p.notes.append(f"{name}.{extra} isn't in the schema file.")
            # Inverses of links declared in other tables that already exist.
            for fname, f in wanted[name].items():
                if "declared_in" in f and fname in have_fields:
                    _compare_field(p, name, f, have_fields[fname], actual, wanted, inverse=True)

        have_views = {(v["name"], v["type"]) for v in have["views"]} if have else set()
        for v in t.get("views", []):
            if (v["name"], v["type"]) not in have_views:
                p.by_hand.append(f"{name}: make the {v['type']} view {v['name']!r}.")

    # Links before formulas; a link whose inverse already exists was made
    # from the other side, and deleted on this one, which a person must fix.
    later.sort(key=lambda a: a["field"]["type"] != "link")
    for action in later:
        f = action["field"]
        target = actual.get(f.get("table"))
        if f["type"] == "link" and target and any(x["name"] == f["inverse"] for x in target["fields"]):
            p.by_hand.append(f"{action['table']}.{f['name']} is missing, but its inverse "
                             f"{f['table']}.{f['inverse']} exists: rename or remove one by hand.")
        else:
            p.actions.append(action)

    for extra in sorted(set(actual) - set(wanted)):
        p.notes.append(f"The table {extra!r} isn't in the schema file.")
    return p


def _compare_field(p, table, f, got, actual, wanted, inverse=False):
    """Note where an existing field differs from the schema's."""
    at = f"{table}.{f['name']}"
    kind = FIELD_TYPES[f["type"]][0]
    if got["type"] != kind:
        p.by_hand.append(f"{at} should be {kind}, but is {got['type']}.")
        return
    if f["type"] in ("select", "multiSelect"):
        have = [c["name"] for c in got["options"]["choices"]]
        missing = [c for c in f["choices"] if c not in have]
        if missing:
            p.actions.append({"do": "add_choices", "table": table, "field": f["name"],
                              "choices": missing, "multiple": f["type"] == "multiSelect"})
        extra = [c for c in have if c not in f["choices"]]
        if extra:
            p.notes.append(f"{at} has options that aren't in the schema file: {', '.join(extra)}.")
    elif f["type"] == "link":
        target = actual.get(f["table"])
        if not target or got["options"]["linkedTableId"] != target["id"]:
            p.by_hand.append(f"{at} should link to {f['table']}.")
        elif not inverse:
            # The field Airtable made on the other side keeps its automatic
            # name if a run stopped between creating the link and naming it.
            other_id = got["options"].get("inverseLinkFieldId")
            other = next((x for x in target["fields"] if x["id"] == other_id), None)
            if other and other["name"] != f["inverse"]:
                p.actions.append({"do": "rename_field", "table": f["table"],
                                  "from": other["name"], "to": f["inverse"]})


def describe(action: dict) -> str:
    """An action as a line for a person."""
    if action["do"] == "create_table":
        return f"Create the table {action['table']!r}."
    if action["do"] == "create_field":
        f = action["field"]
        extra = f" to {f['table']} (inverse {f['inverse']!r})" if f["type"] == "link" else ""
        return f"Add {action['table']}.{f['name']} ({f['type']}{extra})."
    if action["do"] == "add_choices":
        return f"Add to {action['table']}.{action['field']}'s options: {', '.join(action['choices'])}."
    if action["do"] == "rename_field":
        return f"Rename {action['table']}.{action['from']} to {action['to']!r}."
    raise ValueError(action["do"])
