"""
Unit tests for the pure helpers in add_group_wiki_page.py: resolving a
group from a unique name prefix, and locating its hierarchy node.

All names and links are fictional.
"""

import textwrap

import pytest

from add_group_wiki_page import find_node_by_group_id, resolve_group_by_prefix
from util.gather_utils import GatherGroup
from util.hierarchy_wiki import parse_hierarchy


def _group(group_id: str, name: str) -> GatherGroup:
    return GatherGroup(group_id=group_id, name=name, kind="", availability="", description="")


class TestResolveGroupByPrefix:
    def _groups(self):
        return [
            _group("1", "Care Circle"),
            _group("2", "Landscape Working Group"),
            _group("3", "Landscape Design Circle"),
        ]

    def test_unique_prefix(self):
        g = resolve_group_by_prefix(self._groups(), "Care")
        assert g.group_id == "1"

    def test_case_insensitive(self):
        assert resolve_group_by_prefix(self._groups(), "care").group_id == "1"

    def test_no_match_raises(self):
        with pytest.raises(ValueError, match="No Gather group"):
            resolve_group_by_prefix(self._groups(), "Housing")

    def test_ambiguous_prefix_raises(self):
        with pytest.raises(ValueError, match="multiple groups"):
            resolve_group_by_prefix(self._groups(), "Landscape")

    def test_full_name_is_a_valid_prefix(self):
        assert resolve_group_by_prefix(self._groups(), "Care Circle").group_id == "1"


class TestFindNodeByGroupId:
    def _root(self):
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2)
                - Housing Circle
        """)
        return parse_hierarchy(md)

    def test_found(self):
        node = find_node_by_group_id(self._root(), "1")
        assert node is not None and node.name == "Care Circle"

    def test_not_in_hierarchy_returns_none(self):
        assert find_node_by_group_id(self._root(), "99") is None

    def test_row_without_members_link_has_no_group_id(self):
        # "Housing Circle" has no [Members] link, so it isn't matchable by id.
        assert find_node_by_group_id(self._root(), "") is None
