"""
An in-memory stand-in for util.airtable.Airtable, behaving as Airtable's
API was seen to (see util/airtable.py): a new link field gets an inverse
in the linked table, named after the linking table; a table can't be
created with a formula or created-time field; nothing can be deleted.
"""

import itertools

from util.airtable import AirtableError


class FakeAirtable:
    def __init__(self):
        self._ids = itertools.count(1)
        self._tables = []
        self.records = {}  # table ID -> [{id, fields}]
        self.calls = []

    def _id(self, prefix):
        return f"{prefix}{next(self._ids):04d}"

    def _table(self, table_id):
        return next(t for t in self._tables if t["id"] == table_id)

    def _new_field(self, table, field):
        if any(f["name"] == field["name"] for f in table["fields"]):
            raise AirtableError(f"DUPLICATE_OR_EMPTY_FIELD_NAME {field['name']}")
        made = {"id": self._id("fld"), "name": field["name"], "type": field["type"]}
        if "options" in field:
            made["options"] = dict(field["options"])
        table["fields"].append(made)
        return made

    def tables(self, base):
        return self._tables

    def create_table(self, base, name, fields, description=""):
        self.calls.append(("create_table", name))
        if any(f["type"] in ("formula", "createdTime") for f in fields):
            raise AirtableError("UNSUPPORTED_FIELD_TYPE_FOR_CREATE")
        table = {"id": self._id("tbl"), "name": name, "fields": [],
                 "views": [{"id": self._id("viw"), "name": "Grid view", "type": "grid"}]}
        self._tables.append(table)
        self.records[table["id"]] = []
        for f in fields:
            self._new_field(table, f)
        table["primaryFieldId"] = table["fields"][0]["id"]
        return table

    def create_field(self, base, table_id, field):
        self.calls.append(("create_field", field["name"]))
        table = self._table(table_id)
        made = self._new_field(table, field)
        if field["type"] == "multipleRecordLinks":
            target = self._table(field["options"]["linkedTableId"])
            name, n = table["name"], 1
            while any(f["name"] == name for f in target["fields"]):
                n += 1
                name = f"{table['name']} {n}"
            inverse = self._new_field(target, {
                "name": name, "type": "multipleRecordLinks",
                "options": {"linkedTableId": table_id, "inverseLinkFieldId": made["id"]}})
            made["options"]["inverseLinkFieldId"] = inverse["id"]
        return made

    def rename_field(self, base, table_id, field_id, name):
        self.calls.append(("rename_field", name))
        table = self._table(table_id)
        if any(f["name"] == name and f["id"] != field_id for f in table["fields"]):
            raise AirtableError(f"DUPLICATE_OR_EMPTY_FIELD_NAME {name}")
        next(f for f in table["fields"] if f["id"] == field_id)["name"] = name

    def add_select_choices(self, base, table_id, field, choices, multiple):
        self.calls.append(("add_select_choices", field))
        f = next(f for f in self._table(table_id)["fields"] if f["name"] == field)
        f["options"]["choices"] += [{"name": c} for c in choices]

    def _records(self, table):
        table_id = next(t["id"] for t in self._tables if table in (t["id"], t["name"]))
        return self.records[table_id]

    def list_records(self, base, table, fields=None):
        return self._records(table)

    def upsert_records(self, base, table, key, rows):
        self.calls.append(("upsert_records", table))
        records = self._records(table)
        for row in rows:
            match = next((r for r in records if r["fields"].get(key) == row[key]), None)
            if match:
                match["fields"].update(row)
            else:
                records.append({"id": self._id("rec"), "fields": dict(row)})
