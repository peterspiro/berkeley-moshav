"""
A small client for Airtable's Web API: the calls the maintenance tracker's
scripts need to read and change a base's tables and fields, and its records.

What the API can and can't do to a base's structure (tried, November 2026):

- It creates tables and fields, including links, formulas, autonumbers, and
  created-time fields. A table can't be created with a formula or
  created-time field in it; they're added afterwards.
- It can't delete a table or a field, change a field's type, or create a
  view or a form. Those are done by hand in Airtable.
- It can't add an option to a select field directly. Writing a record with
  `typecast` adds any option the record uses, so add_select_choices writes
  a record per new option and deletes it again.
"""

import time

import requests

API = "https://api.airtable.com/v0"
# Airtable allows 5 requests a second per base, and makes a client that
# goes over wait 30 seconds.
MIN_INTERVAL = 0.22
RETRY_AFTER_429 = 31
BATCH = 10  # records per write request


class AirtableError(Exception):
    pass


class Airtable:
    def __init__(self, token: str, session=None, sleep=time.sleep):
        self._session = session or requests.Session()
        self._headers = {"Authorization": f"Bearer {token}"}
        self._sleep = sleep
        self._last = 0.0

    def _request(self, method: str, path: str, **kwargs):
        for attempt in range(3):
            wait = self._last + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            r = self._session.request(method, f"{API}/{path}",
                                      headers=self._headers, timeout=60, **kwargs)
            if r.status_code != 429:
                break
            self._sleep(RETRY_AFTER_429)
        if r.status_code >= 400:
            raise AirtableError(f"{method} {path}: {r.status_code} {r.text[:500]}")
        return r.json()

    # --- structure -------------------------------------------------------

    def bases(self) -> list[dict]:
        """Every base the token can reach: [{id, name, permissionLevel}]."""
        bases, offset = [], None
        while True:
            page = self._request("GET", "meta/bases", params={"offset": offset} if offset else None)
            bases += page["bases"]
            offset = page.get("offset")
            if not offset:
                return bases

    def find_base(self, name: str) -> str:
        """The ID of the base named `name`. The scripts find bases by name so
        that no base ID has to be kept in the (public) repo."""
        matches = [b["id"] for b in self.bases() if b["name"] == name]
        if len(matches) != 1:
            raise AirtableError(
                f"Expected one base named {name!r} that this token can reach; found {len(matches)}.")
        return matches[0]

    def tables(self, base: str) -> list[dict]:
        return self._request("GET", f"meta/bases/{base}/tables")["tables"]

    def create_table(self, base: str, name: str, fields: list[dict], description: str = "") -> dict:
        body = {"name": name, "fields": fields}
        if description:
            body["description"] = description
        return self._request("POST", f"meta/bases/{base}/tables", json=body)

    def create_field(self, base: str, table_id: str, field: dict) -> dict:
        return self._request("POST", f"meta/bases/{base}/tables/{table_id}/fields", json=field)

    def rename_field(self, base: str, table_id: str, field_id: str, name: str) -> dict:
        return self._request("PATCH", f"meta/bases/{base}/tables/{table_id}/fields/{field_id}",
                             json={"name": name})

    def add_select_choices(self, base: str, table_id: str, field: str, choices: list[str],
                           multiple: bool) -> None:
        """Add options to a select field, by creating and deleting a record
        per option (see the module's comment). The records exist for under
        a second each, with only this field set."""
        for i in range(0, len(choices), BATCH):
            records = [{"fields": {field: [c] if multiple else c}} for c in choices[i:i + BATCH]]
            made = self.create_records(base, table_id, records)
            self.delete_records(base, table_id, [r["id"] for r in made])

    # --- records ---------------------------------------------------------

    def list_records(self, base: str, table: str, fields: list[str] | None = None) -> list[dict]:
        records, offset = [], None
        while True:
            params = {"pageSize": 100}
            if fields:
                params["fields[]"] = fields
            if offset:
                params["offset"] = offset
            page = self._request("GET", f"{base}/{table}", params=params)
            records += page["records"]
            offset = page.get("offset")
            if not offset:
                return records

    def create_records(self, base: str, table: str, records: list[dict]) -> list[dict]:
        made = []
        for i in range(0, len(records), BATCH):
            made += self._request("POST", f"{base}/{table}",
                                  json={"records": records[i:i + BATCH], "typecast": True})["records"]
        return made

    def upsert_records(self, base: str, table: str, key: str, rows: list[dict]) -> None:
        """Create or update a record per row, matching rows to records by
        the `key` field's value."""
        for i in range(0, len(rows), BATCH):
            self._request("PATCH", f"{base}/{table}", json={
                "performUpsert": {"fieldsToMergeOn": [key]},
                "records": [{"fields": row} for row in rows[i:i + BATCH]],
                "typecast": True,
            })

    def update_records(self, base: str, table: str, records: list[dict]) -> None:
        """Change the given fields of existing records: [{id, fields}]."""
        for i in range(0, len(records), BATCH):
            self._request("PATCH", f"{base}/{table}",
                          json={"records": records[i:i + BATCH], "typecast": True})

    def delete_records(self, base: str, table: str, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            self._request("DELETE", f"{base}/{table}", params={"records[]": ids[i:i + BATCH]})
