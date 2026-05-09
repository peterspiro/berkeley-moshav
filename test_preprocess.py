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
        self.assertEqual(derive_household_name(["Beta", "Alpha"]), "Alpha-Beta")

    def test_derive_household_name_deduplicates(self):
        self.assertEqual(derive_household_name(["Smith", "Smith"]), "Smith")

    def test_derive_household_name_truncates(self):
        name = derive_household_name(["A" * 20, "B" * 20])
        self.assertLessEqual(len(name), 32)

    def test_parse_unit_integer(self):
        self.assertEqual(parse_unit("100"), (100, None))

    def test_parse_unit_with_suffix(self):
        self.assertEqual(parse_unit("20-2A"), (20, "2A"))

    def test_parse_unit_blank(self):
        self.assertEqual(parse_unit(""), (None, None))

    def test_is_international_phone(self):
        self.assertTrue(is_international_phone("+44 20 7946 0958"))
        self.assertTrue(is_international_phone("+33 1 23 45 67"))
        self.assertFalse(is_international_phone("+1 510-555-0101"))  # US with +1
        self.assertFalse(is_international_phone("510-555-0101"))     # US domestic
        self.assertFalse(is_international_phone(""))

    def test_strip_pronunciation(self):
        self.assertEqual(strip_pronunciation("Aria (ar-EE-uh)"), "Aria")
        self.assertEqual(strip_pronunciation("Robin"), "Robin")
        self.assertEqual(strip_pronunciation("Estee Solomon Gray"), "Estee Solomon Gray")

    def test_parse_others_child_by_age(self):
        entries = parse_others("Kael (12), Wren (10)")
        self.assertEqual(entries, [("Kael", "12"), ("Wren", "10")])

    def test_parse_others_child_by_infant(self):
        entries = parse_others("River (infant)")
        self.assertEqual(entries, [("River", "infant")])

    def test_parse_others_adult_with_relation(self):
        entries = parse_others("Mark (son); Jane (DIL); Sam (20s)")
        self.assertEqual(entries, [
            ("Mark", "son"),
            ("Jane", "DIL"),
            ("Sam", "20s"),
        ])

    def test_parse_others_adult_no_qualifier(self):
        entries = parse_others("Robin Blue, Alex Green")
        self.assertEqual(entries, [
            ("Robin Blue", None),
            ("Alex Green", None),
        ])

    def test_parse_others_child_multi_given_name(self):
        entries = parse_others("Luna Liliana (2)")
        self.assertEqual(entries, [("Luna Liliana", "2")])

    def test_parse_others_inter_household_qualifier(self):
        # Multi-word proper-name qualifier — name still returned, qualifier preserved
        entries = parse_others("Dana Mills (parents of Chris Daly)")
        self.assertEqual(entries, [("Dana Mills", "parents of Chris Daly")])

    def test_resolve_adult_two_names(self):
        name_to_row = {"Robin Blue": {}, "Alex Green": {}}
        result = resolve_adult_name("Robin Blue", "Green", name_to_row, [])
        self.assertEqual(result, "Robin Blue")

    def test_resolve_adult_three_names_ignores_middle(self):
        name_to_row = {"Grace Hardy": {}}
        result = resolve_adult_name("Grace Ann Hardy", "Hardy", name_to_row, [])
        self.assertEqual(result, "Grace Hardy")

    def test_resolve_adult_first_name_only_matches_same_last(self):
        name_to_row = {"Quinn Norris": {}, "Pat Norris": {}}
        result = resolve_adult_name("Quinn", "Norris", name_to_row, [])
        self.assertEqual(result, "Quinn Norris")

    def test_resolve_adult_first_name_only_unique_match(self):
        name_to_row = {"Mark Elder-Young": {}, "Robin Blue": {}}
        result = resolve_adult_name("Mark", "Elder", name_to_row, [])
        self.assertEqual(result, "Mark Elder-Young")

    def test_resolve_adult_not_found_returns_none(self):
        name_to_row = {"Robin Blue": {}}
        result = resolve_adult_name("Morgan", "Wells", name_to_row, [])
        self.assertIsNone(result)

    def test_resolve_adult_ambiguous_warns_and_returns_none(self):
        name_to_row = {"Alex Smith": {}, "Alex Jones": {}}
        warnings = []
        result = resolve_adult_name("Alex", "Brown", name_to_row, warnings)
        self.assertIsNone(result)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Alex", warnings[0])


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
                "First Name": "Alex", "Last Name": "Green",
                "Email Address": "alex@example.com", "Phone": "510-555-0101",
                "Unit #": "101", "Others in the Household": "Robin Blue",
                "Status": "Member",
            },
            {
                "First Name": "Robin", "Last Name": "Blue",
                "Email Address": "robin@example.com", "Phone": "510-555-0102",
                "Unit #": "101", "Others in the Household": "Alex Green",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        self.assertEqual(hh["household_name"], "Blue-Green")
        self.assertEqual(hh["unit_num"], 101)
        self.assertIsNone(hh["unit_suffix"])
        self.assertEqual(len(hh["members"]), 2)
        emails = {m["email"] for m in hh["members"]}
        self.assertIn("alex@example.com", emails)
        self.assertIn("robin@example.com", emails)

    def test_no_duplicates_on_second_run(self):
        rows = [
            {
                "First Name": "Alex", "Last Name": "Green",
                "Email Address": "alex@example.com", "Phone": "510-555-0101",
                "Unit #": "101", "Others in the Household": "Robin Blue",
                "Status": "Member",
            },
            {
                "First Name": "Robin", "Last Name": "Blue",
                "Email Address": "robin@example.com", "Phone": "510-555-0102",
                "Unit #": "101", "Others in the Household": "Alex Green",
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
                "First Name": "Pat", "Last Name": "Norris",
                "Email Address": "pat@example.com", "Phone": "",
                "Unit #": "201",
                "Others in the Household": "Quinn (spouse), Kael (12), Wren (10)",
                "Status": "Member",
            },
            {
                "First Name": "Quinn", "Last Name": "Norris",
                "Email Address": "quinn@example.com", "Phone": "",
                "Unit #": "201",
                "Others in the Household": "Pat (spouse), Kael (12), Wren (10)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        # 2 adults + 2 children (Kael and Wren deduped across both parents)
        self.assertEqual(len(hh["members"]), 4)
        children = [m for m in hh["members"] if m["child"]]
        self.assertEqual(len(children), 2)
        child_names = {m["first_name"] for m in children}
        self.assertIn("Kael", child_names)
        self.assertIn("Wren", child_names)
        for child in children:
            self.assertFalse(child["full_access"])
            self.assertEqual(child["email"], "")
            self.assertEqual(child["last_name"], "Norris")

    def test_children_from_others_column_infant(self):
        path = self._tsv_file([
            {
                "First Name": "Casey", "Last Name": "Brown",
                "Email Address": "casey@example.com", "Phone": "",
                "Unit #": "301",
                "Others in the Household": "Taylor Gray, River (infant)",
                "Status": "Member",
            },
            {
                "First Name": "Taylor", "Last Name": "Gray",
                "Email Address": "taylor@example.com", "Phone": "",
                "Unit #": "301",
                "Others in the Household": "Casey Brown, River (infant)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        children = [m for m in result[0]["members"] if m["child"]]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["first_name"], "River")
        self.assertEqual(children[0]["last_name"], "Brown-Gray")

    def test_child_last_name_equals_household_name(self):
        path = self._tsv_file([
            {
                "First Name": "Kim", "Last Name": "East",
                "Email Address": "kim@example.com", "Phone": "",
                "Unit #": "401",
                "Others in the Household": "Lee West (spouse), Pax (2), Ocean (0)",
                "Status": "Member",
            },
            {
                "First Name": "Lee", "Last Name": "West",
                "Email Address": "lee@example.com", "Phone": "",
                "Unit #": "401",
                "Others in the Household": "Kim (spouse), Pax (2), Ocean (0)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        hh = result[0]
        self.assertEqual(hh["household_name"], "East-West")
        children = [m for m in hh["members"] if m["child"]]
        self.assertEqual(len(children), 2)
        for child in children:
            self.assertEqual(child["last_name"], "East-West")

    def test_child_multi_given_name(self):
        path = self._tsv_file([
            {
                "First Name": "Sam", "Last Name": "Radley",
                "Email Address": "sam@example.com", "Phone": "",
                "Unit #": "501",
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
                "First Name": "Jane", "Last Name": "Elder",
                "Email Address": "jane@example.com", "Phone": "",
                "Unit #": "601", "Others in the Household": "Mark (son)",
                "Status": "Member",
            },
            {
                "First Name": "Mark", "Last Name": "Elder-Young",
                "Email Address": "mark@example.com", "Phone": "",
                "Unit #": "601", "Others in the Household": "Jane (mother)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["members"]), 2)

    def test_adult_three_names_ignores_middle(self):
        path = self._tsv_file([
            {
                "First Name": "Frank", "Last Name": "Hardy",
                "Email Address": "frank@example.com", "Phone": "",
                "Unit #": "701", "Others in the Household": "Grace Ann Hardy",
                "Status": "Member",
            },
            {
                "First Name": "Grace", "Last Name": "Hardy",
                "Email Address": "grace@example.com", "Phone": "",
                "Unit #": "701", "Others in the Household": "Frank Hardy",
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
                "First Name": "Ben", "Last Name": "Cooper",
                "Email Address": "ben@example.com", "Phone": "",
                "Unit #": "801",
                "Others in the Household": "Dana Mills (parents of Chris Daly)",
                "Status": "Member",
            },
            {
                "First Name": "Dana", "Last Name": "Mills",
                "Email Address": "dana@example.com", "Phone": "",
                "Unit #": "801",
                "Others in the Household": "Ben Cooper (parents of Chris Daly)",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["members"]), 2)
        self.assertEqual(result[0]["household_name"], "Cooper-Mills")

    def test_unresolved_adult_not_created(self):
        """A name in Others that matches no row is silently skipped."""
        path = self._tsv_file([
            {
                "First Name": "Nina", "Last Name": "Wells",
                "Email Address": "nina@example.com", "Phone": "",
                "Unit #": "901", "Others in the Household": "Morgan",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        # Only Nina — no phantom entry for Morgan
        self.assertEqual(len(result[0]["members"]), 1)

    def test_pronunciation_guide_stripped_from_first_name(self):
        path = self._tsv_file([
            {
                "First Name": "Aria (ar-EE-uh)", "Last Name": "Vale",
                "Email Address": "aria@example.com", "Phone": "",
                "Unit #": "102", "Others in the Household": "Noa Stern",
                "Status": "Member",
            },
            {
                "First Name": "Noa", "Last Name": "Stern",
                "Email Address": "noa@example.com", "Phone": "",
                "Unit #": "102", "Others in the Household": "Aria Vale",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        adults = [m for m in result[0]["members"] if not m["child"]]
        first_names = {m["first_name"] for m in adults}
        self.assertIn("Aria", first_names)  # pronunciation guide stripped

    def test_status_member_and_consultant_included(self):
        path = self._tsv_file([
            {
                "First Name": "Chris", "Last Name": "Daly",
                "Email Address": "chris@example.com", "Phone": "",
                "Unit #": "202", "Others in the Household": "Ezra (15)",
                "Status": "Member & Consultant",
            },
        ])
        result = preprocess(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["household_name"], "Daly")
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

    def test_international_phone_stored_in_pronouns(self):
        path = self._tsv_file([
            {
                "First Name": "Alex", "Last Name": "Green",
                "Email Address": "alex@example.com",
                "Phone": "+44 20 7946 0958",
                "Unit #": "101", "Others in the Household": "",
                "Status": "Member",
            },
        ])
        result = preprocess(path)
        member = result[0]["members"][0]
        self.assertEqual(member["phone"], "")
        self.assertEqual(member["pronouns"], "+44 20 7946 0958")

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
