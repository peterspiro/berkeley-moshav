#!/usr/bin/env python3
"""Unit tests for preprocess.py"""

import io
import json
import os
import sys
import tempfile
import unittest

from preprocess import (
    UnionFind,
    derive_household_name,
    normalize_name,
    parse_child_name,
    parse_unit,
    preprocess,
)


class TestUnionFind(unittest.TestCase):
    def test_basic_union(self):
        uf = UnionFind()
        uf.union("A", "B")
        uf.union("B", "C")
        self.assertEqual(uf.find("A"), uf.find("C"))

    def test_separate_groups(self):
        uf = UnionFind()
        uf.union("A", "B")
        uf.find("C")
        self.assertNotEqual(uf.find("A"), uf.find("C"))

    def test_groups(self):
        uf = UnionFind()
        uf.union("A", "B")
        uf.find("C")
        groups = uf.groups()
        self.assertEqual(len(groups), 2)


class TestHelpers(unittest.TestCase):
    def test_derive_household_name_alphabetical(self):
        self.assertEqual(derive_household_name(["Spiro", "Rosenblum"]), "Rosenblum-Spiro")

    def test_derive_household_name_deduplicates(self):
        self.assertEqual(derive_household_name(["Smith", "Smith"]), "Smith")

    def test_derive_household_name_truncates(self):
        name = derive_household_name(["A" * 20, "B" * 20])
        self.assertLessEqual(len(name), 32)

    def test_parse_unit_integer(self):
        self.assertEqual(parse_unit("411"), (411, None))

    def test_parse_unit_with_suffix(self):
        self.assertEqual(parse_unit("20-2A"), (20, "2A"))

    def test_parse_unit_blank(self):
        self.assertEqual(parse_unit(""), (None, None))

    def test_parse_child_name(self):
        self.assertEqual(parse_child_name("Alice Smith"), ("Alice", "Smith"))
        self.assertEqual(parse_child_name("Bob"), ("Bob", ""))


def _make_tsv(rows: list[dict], fieldnames: list[str] | None = None) -> str:
    import csv
    fieldnames = fieldnames or [
        "First Name", "Last Name", "Phone", "Email Address",
        "Current Location", "Local?", "Kids", "Others in the Household",
        "Status", "Unit #", "Link to Bio",
    ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fieldnames})
    return out.getvalue()


def _write_tsv(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False)
    f.write(content)
    f.close()
    return f.name


class TestPreprocess(unittest.TestCase):
    def setUp(self):
        self._tmpfiles = []

    def tearDown(self):
        for f in self._tmpfiles:
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass

    def _tsv_file(self, rows, fieldnames=None):
        path = _write_tsv(_make_tsv(rows, fieldnames))
        self._tmpfiles.append(path)
        return path

    def test_single_household_two_adults(self):
        path = self._tsv_file([
            {
                "First Name": "Peter", "Last Name": "Spiro",
                "Email Address": "paspiro@gmail.com", "Phone": "510-501-7441",
                "Unit #": "411", "Others in the Household": "Sandra Rosenblum",
                "Status": "Member",
            },
            {
                "First Name": "Sandra", "Last Name": "Rosenblum",
                "Email Address": "sandrarosenblum@yahoo.com", "Phone": "510-684-0794",
                "Unit #": "411", "Others in the Household": "Peter Spiro",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        self.assertEqual(hh["household_name"], "Rosenblum-Spiro")
        self.assertEqual(hh["unit_num"], 411)
        self.assertIsNone(hh["unit_suffix"])
        self.assertEqual(len(hh["members"]), 2)
        emails = {m["email"] for m in hh["members"]}
        self.assertIn("paspiro@gmail.com", emails)
        self.assertIn("sandrarosenblum@yahoo.com", emails)

    def test_no_duplicates_on_second_run(self):
        """Running preprocess twice on the same data yields the same households."""
        rows = [
            {
                "First Name": "Peter", "Last Name": "Spiro",
                "Email Address": "paspiro@gmail.com", "Phone": "510-501-7441",
                "Unit #": "411", "Others in the Household": "Sandra Rosenblum",
                "Status": "Member",
            },
            {
                "First Name": "Sandra", "Last Name": "Rosenblum",
                "Email Address": "sandrarosenblum@yahoo.com", "Phone": "510-684-0794",
                "Unit #": "411", "Others in the Household": "Peter Spiro",
                "Status": "Member",
            },
        ]
        path = self._tsv_file(rows)
        r1 = preprocess(path)
        r2 = preprocess(path)
        self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True))

    def test_kids_column(self):
        path = self._tsv_file([
            {
                "First Name": "Peter", "Last Name": "Spiro",
                "Email Address": "paspiro@gmail.com", "Phone": "510-501-7441",
                "Unit #": "411", "Kids": "Timmy Spiro, Jane Spiro",
                "Others in the Household": "Sandra Rosenblum",
                "Status": "Member",
            },
            {
                "First Name": "Sandra", "Last Name": "Rosenblum",
                "Email Address": "sandrarosenblum@yahoo.com", "Phone": "510-684-0794",
                "Unit #": "411", "Others in the Household": "Peter Spiro",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        # 2 adults + 2 kids
        self.assertEqual(len(hh["members"]), 4)
        children = [m for m in hh["members"] if m["child"]]
        self.assertEqual(len(children), 2)
        child_names = {m["first_name"] for m in children}
        self.assertIn("Timmy", child_names)
        self.assertIn("Jane", child_names)
        for child in children:
            self.assertFalse(child["full_access"])
            self.assertEqual(child["email"], "")

    def test_kids_not_duplicated_when_listed_by_both_parents(self):
        path = self._tsv_file([
            {
                "First Name": "Peter", "Last Name": "Spiro",
                "Email Address": "p@example.com", "Phone": "555-1111",
                "Unit #": "411", "Kids": "Timmy Spiro",
                "Others in the Household": "Sandra Rosenblum",
                "Status": "Member",
            },
            {
                "First Name": "Sandra", "Last Name": "Rosenblum",
                "Email Address": "s@example.com", "Phone": "555-2222",
                "Unit #": "411", "Kids": "Timmy Spiro",
                "Others in the Household": "Peter Spiro",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        children = [m for m in result[0]["members"] if m["child"]]
        self.assertEqual(len(children), 1)

    def test_transitive_household_grouping(self):
        """A→B, B→C should put all three in one household."""
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "A",
                "Email Address": "a@example.com", "Phone": "",
                "Unit #": "1", "Others in the Household": "Bob B",
                "Status": "Member",
            },
            {
                "First Name": "Bob", "Last Name": "B",
                "Email Address": "b@example.com", "Phone": "",
                "Unit #": "1", "Others in the Household": "Carol C",
                "Status": "Member",
            },
            {
                "First Name": "Carol", "Last Name": "C",
                "Email Address": "c@example.com", "Phone": "",
                "Unit #": "1", "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["members"]), 3)

    def test_separate_households(self):
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "",
                "Unit #": "101", "Others in the Household": "",
                "Status": "Member",
            },
            {
                "First Name": "Bob", "Last Name": "Jones",
                "Email Address": "bob@example.com", "Phone": "",
                "Unit #": "202", "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 2)
        names = {h["household_name"] for h in result}
        self.assertIn("Jones", names)
        self.assertIn("Smith", names)

    def test_unit_with_suffix(self):
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "",
                "Unit #": "20-2A", "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(result[0]["unit_num"], 20)
        self.assertEqual(result[0]["unit_suffix"], "2A")

    def test_adults_have_full_access(self):
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "555-1234",
                "Unit #": "101", "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        adult = result[0]["members"][0]
        self.assertFalse(adult["child"])
        self.assertTrue(adult["full_access"])

    def test_status_filter_excludes_non_members(self):
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "",
                "Unit #": "101", "Others in the Household": "",
                "Status": "Member",
            },
            {
                "First Name": "Bob", "Last Name": "Jones",
                "Email Address": "bob@example.com", "Phone": "",
                "Unit #": "202", "Others in the Household": "",
                "Status": "Applicant",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["household_name"], "Smith")

    def test_status_filter_case_insensitive(self):
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "",
                "Unit #": "101", "Others in the Household": "",
                "Status": "MEMBER",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)

    def test_status_filter_empty_returns_nothing(self):
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "",
                "Unit #": "101", "Others in the Household": "",
                "Status": "",
            },
        ])
        result = preprocess(path)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
