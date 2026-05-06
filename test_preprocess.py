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
    is_international_phone,
    normalize_name,
    parse_others,
    parse_unit,
    preprocess,
    resolve_adult_name,
    strip_pronunciation,
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

    def test_is_international_phone(self):
        self.assertTrue(is_international_phone("+44 079 616 86029"))
        self.assertTrue(is_international_phone("+33 1 23 45 67"))
        self.assertFalse(is_international_phone("+1 510-501-7441"))   # US with +1
        self.assertFalse(is_international_phone("510-501-7441"))      # US domestic
        self.assertFalse(is_international_phone(""))

    def test_strip_pronunciation(self):
        self.assertEqual(strip_pronunciation("Ayala (a-ya-LA)"), "Ayala")
        self.assertEqual(strip_pronunciation("Peter"), "Peter")
        self.assertEqual(strip_pronunciation("Estee Solomon Gray"), "Estee Solomon Gray")

    def test_parse_others_child_by_age(self):
        entries = parse_others("Noah (12), Aviva (10)")
        self.assertEqual(entries, [("Noah", "12"), ("Aviva", "10")])

    def test_parse_others_child_by_infant(self):
        entries = parse_others("Julian (infant)")
        self.assertEqual(entries, [("Julian", "infant")])

    def test_parse_others_adult_with_relation(self):
        entries = parse_others("Michael (son); Julie (DIL); Sam (20s)")
        self.assertEqual(entries, [
            ("Michael", "son"),
            ("Julie", "DIL"),
            ("Sam", "20s"),
        ])

    def test_parse_others_adult_no_qualifier(self):
        entries = parse_others("Sandra Rosenblum, Peter Spiro")
        self.assertEqual(entries, [
            ("Sandra Rosenblum", None),
            ("Peter Spiro", None),
        ])

    def test_parse_others_child_multi_given_name(self):
        entries = parse_others("Luna Liliana (2)")
        self.assertEqual(entries, [("Luna Liliana", "2")])

    def test_parse_others_inter_household_qualifier(self):
        # Multi-word proper-name qualifier — name still returned, qualifier preserved
        entries = parse_others("Laura Nelson (parents of Josh Abrams)")
        self.assertEqual(entries, [("Laura Nelson", "parents of Josh Abrams")])

    def test_resolve_adult_two_names(self):
        name_to_row = {"Sandra Rosenblum": {}, "Peter Spiro": {}}
        result = resolve_adult_name("Sandra Rosenblum", "Spiro", name_to_row, [])
        self.assertEqual(result, "Sandra Rosenblum")

    def test_resolve_adult_three_names_ignores_middle(self):
        name_to_row = {"Anita Hersh": {}}
        result = resolve_adult_name("Anita Tanay Hersh", "Hersh", name_to_row, [])
        self.assertEqual(result, "Anita Hersh")

    def test_resolve_adult_first_name_only_matches_same_last(self):
        name_to_row = {"Yona Abrams": {}, "Josh Abrams": {}}
        result = resolve_adult_name("Yona", "Abrams", name_to_row, [])
        self.assertEqual(result, "Yona Abrams")

    def test_resolve_adult_first_name_only_unique_match(self):
        name_to_row = {"Michael Saxe-Taller": {}, "Sandra Rosenblum": {}}
        result = resolve_adult_name("Michael", "Taller", name_to_row, [])
        self.assertEqual(result, "Michael Saxe-Taller")

    def test_resolve_adult_not_found_returns_none(self):
        name_to_row = {"Sandra Rosenblum": {}}
        result = resolve_adult_name("Garith", "Reznick", name_to_row, [])
        self.assertIsNone(result)

    def test_resolve_adult_ambiguous_warns_and_returns_none(self):
        name_to_row = {"Julie Smith": {}, "Julie Jones": {}}
        warnings = []
        result = resolve_adult_name("Julie", "Brown", name_to_row, warnings)
        self.assertIsNone(result)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Julie", warnings[0])


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

    def test_children_from_others_column_by_age(self):
        path = self._tsv_file([
            {
                "First Name": "Josh", "Last Name": "Abrams",
                "Email Address": "josh@example.com", "Phone": "",
                "Unit #": "211", "Others in the Household": "Yona (spouse), Noah (12), Aviva (10)",
                "Status": "Member",
            },
            {
                "First Name": "Yona", "Last Name": "Abrams",
                "Email Address": "yona@example.com", "Phone": "",
                "Unit #": "211", "Others in the Household": "Josh (spouse), Noah (12), Aviva (10)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        # 2 adults + 2 children (Noah and Aviva deduped across both parents)
        self.assertEqual(len(hh["members"]), 4)
        children = [m for m in hh["members"] if m["child"]]
        self.assertEqual(len(children), 2)
        child_names = {m["first_name"] for m in children}
        self.assertIn("Noah", child_names)
        self.assertIn("Aviva", child_names)
        for child in children:
            self.assertFalse(child["full_access"])
            self.assertEqual(child["email"], "")
            self.assertEqual(child["last_name"], "Abrams")

    def test_children_from_others_column_infant(self):
        path = self._tsv_file([
            {
                "First Name": "Hilary", "Last Name": "Jacobsen",
                "Email Address": "h@example.com", "Phone": "",
                "Unit #": "410",
                "Others in the Household": "Noah Brod, Julian (infant)",
                "Status": "Member",
            },
            {
                "First Name": "Noah", "Last Name": "Brod",
                "Email Address": "n@example.com", "Phone": "",
                "Unit #": "410",
                "Others in the Household": "Hilary Jacobsen, Julian (infant)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        children = [m for m in result[0]["members"] if m["child"]]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["first_name"], "Julian")
        self.assertEqual(children[0]["last_name"], "Brod-Jacobsen")

    def test_child_last_name_equals_household_name(self):
        path = self._tsv_file([
            {
                "First Name": "Kalvin", "Last Name": "Wang",
                "Email Address": "k@example.com", "Phone": "",
                "Unit #": "110",
                "Others in the Household": "Monica Lee (spouse), Pax (2), Ocean (0)",
                "Status": "Member",
            },
            {
                "First Name": "Monica", "Last Name": "Lee",
                "Email Address": "m@example.com", "Phone": "",
                "Unit #": "110",
                "Others in the Household": "Kalvin (spouse), Pax (2), Ocean (0)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        self.assertEqual(hh["household_name"], "Lee-Wang")
        children = [m for m in hh["members"] if m["child"]]
        self.assertEqual(len(children), 2)
        for child in children:
            self.assertEqual(child["last_name"], "Lee-Wang")

    def test_child_multi_given_name(self):
        path = self._tsv_file([
            {
                "First Name": "Cosmin", "Last Name": "Radoi",
                "Email Address": "c@example.com", "Phone": "",
                "Unit #": "311",
                "Others in the Household": "Luna Liliana (2), Ash (0)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        children = [m for m in result[0]["members"] if m["child"]]
        child_names = {m["first_name"] for m in children}
        self.assertIn("Luna Liliana", child_names)
        self.assertIn("Ash", child_names)

    def test_adults_with_relation_qualifier_grouped(self):
        path = self._tsv_file([
            {
                "First Name": "Dolores", "Last Name": "Taller",
                "Email Address": "d@example.com", "Phone": "",
                "Unit #": "207", "Others in the Household": "Michael (son)",
                "Status": "Member",
            },
            {
                "First Name": "Michael", "Last Name": "Saxe-Taller",
                "Email Address": "m@example.com", "Phone": "",
                "Unit #": "207", "Others in the Household": "Dolores (mother)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["members"]), 2)

    def test_adult_three_names_ignores_middle(self):
        path = self._tsv_file([
            {
                "First Name": "Stephen", "Last Name": "Hersh",
                "Email Address": "s@example.com", "Phone": "",
                "Unit #": "303", "Others in the Household": "Anita Tanay Hersh",
                "Status": "Member",
            },
            {
                "First Name": "Anita", "Last Name": "Hersh",
                "Email Address": "a@example.com", "Phone": "",
                "Unit #": "303", "Others in the Household": "Stephen Hersh",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["members"]), 2)

    def test_adult_inter_household_qualifier_still_grouped(self):
        """'Name (parents of X)' — the named person is still in the same household."""
        path = self._tsv_file([
            {
                "First Name": "Dan", "Last Name": "Alpert",
                "Email Address": "d@example.com", "Phone": "",
                "Unit #": "301",
                "Others in the Household": "Laura Nelson (parents of Josh Abrams)",
                "Status": "Member",
            },
            {
                "First Name": "Laura", "Last Name": "Nelson",
                "Email Address": "l@example.com", "Phone": "",
                "Unit #": "301",
                "Others in the Household": "Dan Alpert (parents of Josh Abrams)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["members"]), 2)
        self.assertEqual(result[0]["household_name"], "Alpert-Nelson")

    def test_unresolved_adult_not_created(self):
        """A name in Others that matches no row is silently skipped."""
        path = self._tsv_file([
            {
                "First Name": "Olga", "Last Name": "Reznick",
                "Email Address": "o@example.com", "Phone": "",
                "Unit #": "401", "Others in the Household": "Garith",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        # Only Olga — no phantom entry for Garith
        self.assertEqual(len(result[0]["members"]), 1)

    def test_pronunciation_guide_stripped_from_first_name(self):
        path = self._tsv_file([
            {
                "First Name": "Ayala (a-ya-LA)", "Last Name": "Jonas",
                "Email Address": "a@example.com", "Phone": "",
                "Unit #": "304", "Others in the Household": "Yacov Ovadya",
                "Status": "Member",
            },
            {
                "First Name": "Yacov", "Last Name": "Ovadya",
                "Email Address": "y@example.com", "Phone": "",
                "Unit #": "304", "Others in the Household": "Ayala Jonas",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        adults = [m for m in result[0]["members"] if not m["child"]]
        first_names = {m["first_name"] for m in adults}
        self.assertIn("Ayala", first_names)  # pronunciation guide stripped

    def test_status_member_and_consultant_included(self):
        path = self._tsv_file([
            {
                "First Name": "Roger", "Last Name": "Studley",
                "Email Address": "r@example.com", "Phone": "",
                "Unit #": "202", "Others in the Household": "Ezra (15)",
                "Status": "Member & Consultant",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["household_name"], "Studley")
        children = [m for m in result[0]["members"] if m["child"]]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["first_name"], "Ezra")

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

    def test_international_phone_omitted_from_member(self):
        path = self._tsv_file([
            {
                "First Name": "Eliana", "Last Name": "Lorch",
                "Email Address": "eliana@example.com",
                "Phone": "+44 079 616 86029",
                "Unit #": "406", "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(result[0]["members"][0]["phone"], "")

    def test_kids_column_ignored(self):
        """The Kids column should have no effect on child detection."""
        path = self._tsv_file([
            {
                "First Name": "Alice", "Last Name": "Smith",
                "Email Address": "alice@example.com", "Phone": "",
                "Unit #": "101",
                "Kids": "Timmy Smith, Jane Smith",  # old format, should be ignored
                "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        # No children: Kids column is ignored, Others has no age-qualified entries
        children = [m for m in result[0]["members"] if m["child"]]
        self.assertEqual(len(children), 0)


if __name__ == "__main__":
    unittest.main()
