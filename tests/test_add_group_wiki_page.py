"""
Unit tests for resolving a group from a unique name prefix, as
add_group_wiki_page.py does.

All names and links are fictional.
"""

import pytest

from add_group_wiki_page import resolve_group_by_prefix
from util.gather_utils import GatherGroup


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
