"""
Unit tests for reading the old Circle Structure wiki page's tree, in
util/hierarchy_wiki.py.

All names and links are fictional.
"""

import textwrap

from util.hierarchy_wiki import _parse_rest, iter_nodes, parse_hierarchy


# ── _parse_rest: classifying managed vs. unmanaged segments ──────────────────

class TestParseRest:
    def test_managed_links_extracted(self):
        name, members, docs, wiki, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2)"
        )
        assert name == "Care Circle"
        assert members == "/groups/1"
        assert docs == "/gdrive/item/2"
        assert wiki == ""
        assert extras == []

    def test_wiki_link_extracted(self):
        name, members, docs, wiki, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2) "
            "| [Wiki](/wiki/care-circle-wiki)"
        )
        assert wiki == "/wiki/care-circle-wiki"
        assert extras == []

    def test_extra_link_captured(self):
        *_, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2) "
            "| [Notes](/wiki/care-notes)"
        )
        assert extras == ["[Notes](/wiki/care-notes)"]

    def test_wiki_not_captured_as_extra(self):
        # The managed [Wiki] link goes to wiki_url, not extras; a genuinely
        # unmanaged link alongside it still lands in extras.
        _, _, _, wiki, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [Wiki](/wiki/care-circle-wiki) "
            "| [Notes](/wiki/care-notes)"
        )
        assert wiki == "/wiki/care-circle-wiki"
        assert extras == ["[Notes](/wiki/care-notes)"]

    def test_extra_link_without_managed_links(self):
        name, members, docs, wiki, extras = _parse_rest("Care Circle: [Notes](/wiki/care-notes)")
        assert name == "Care Circle"
        assert (members, docs, wiki) == ("", "", "")
        assert extras == ["[Notes](/wiki/care-notes)"]

    def test_multiple_extras_kept_in_order(self):
        *_, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [A](/a) | [B](/b)"
        )
        assert extras == ["[A](/a)", "[B](/b)"]

    def test_plain_name_has_no_extras(self):
        name, members, docs, wiki, extras = _parse_rest("Care Circle")
        assert name == "Care Circle"
        assert (members, docs, wiki, extras) == ("", "", "", [])


# ── Round-trip: extras survive parse -> render ───────────────────────────────


class TestParseHierarchy:
    def test_tree_and_group_ids(self):
        root = parse_hierarchy(textwrap.dedent("""\
            - Full Community (Top Circle / HOA): [Members](/groups/1)
                - Land Circle: [Members](/groups/2)
                      - Garden: [Members](/groups/3)
                - Tech Circle
        """))
        assert root.group_id == "1"
        assert [c.name for c in root.children] == ["Land Circle", "Tech Circle"]
        garden = root.children[0].children[0]
        assert garden.group_id == "3" and garden.parent.group_id == "2"
        assert root.children[1].group_id is None
        assert [n.name for n in iter_nodes(root)] == [
            "Full Community (Top Circle / HOA)", "Land Circle", "Garden", "Tech Circle"]
