"""
Tests for the HierarchyIntegrityError guard in
update_groups_in_google_and_hierarchy.sync_hierarchy: a hierarchy row whose
Gather group is gone (deleted or deactivated) but which still carries a
[Wiki] link must abort the run rather than be silently removed.

All names and links are fictional.
"""

import textwrap

import pytest

from update_groups_in_google_and_hierarchy import (
    HierarchyIntegrityError,
    sync_hierarchy,
)
from util.hierarchy_wiki import iter_nodes, parse_hierarchy

_MD_WITH_WIKI = textwrap.dedent("""\
    - Root: [Members](/groups/0)
        - Care Circle: [Members](/groups/1) | [Wiki](/wiki/care-circle-wiki)
""")

_MD_WITHOUT_WIKI = textwrap.dedent("""\
    - Root: [Members](/groups/0)
        - Care Circle: [Members](/groups/1)
""")


def _sync(root, *, group_info=None, deactivated=(), excluded=()):
    sync_hierarchy(
        root,
        group_info or {},
        {},                      # documents_url_by_group_id
        set(),                   # linked_group_ids
        set(excluded),           # excluded_ids
        set(deactivated),        # deactivated_ids
        dry_run=True,
    )


class TestDeletedGroupGuard:
    def test_deleted_group_with_wiki_link_raises(self):
        root = parse_hierarchy(_MD_WITH_WIKI)  # group 1 absent from group_info → deleted
        with pytest.raises(HierarchyIntegrityError, match="care-circle-wiki"):
            _sync(root)

    def test_deleted_group_without_wiki_link_does_not_raise(self):
        root = parse_hierarchy(_MD_WITHOUT_WIKI)
        _sync(root)  # dry-run: reports it would prompt to delete, no raise
        assert any(n.name == "Care Circle" for n in iter_nodes(root))


class TestDeactivatedGroupGuard:
    def test_deactivated_group_with_wiki_link_raises(self):
        root = parse_hierarchy(_MD_WITH_WIKI)
        with pytest.raises(HierarchyIntegrityError, match="care-circle-wiki"):
            _sync(root, deactivated={"1"})

    def test_deactivated_group_without_wiki_link_does_not_raise(self):
        root = parse_hierarchy(_MD_WITHOUT_WIKI)
        _sync(root, deactivated={"1"})  # dry-run: reports removal, no raise


class TestLiveGroupNotGuarded:
    def test_live_group_with_wiki_link_is_fine(self):
        root = parse_hierarchy(_MD_WITH_WIKI)
        # Group 1 still exists in Gather → not a removal → guard doesn't fire.
        _sync(root, group_info={"1": {"name": "Care Circle"}})
        assert any(n.name == "Care Circle" for n in iter_nodes(root))


class TestSingleGroupMode:
    def test_other_rows_untouched_when_only_group_id_set(self):
        # Group 1 is absent from group_info (as in a -g run on another group);
        # with only_group_id set it must not be treated as deleted.
        root = parse_hierarchy(_MD_WITH_WIKI)
        sync_hierarchy(
            root, {"2": {"name": "Other"}}, {}, set(), set(), set(),
            dry_run=True, only_group_id="2",
        )
        assert any(n.name == "Care Circle" for n in iter_nodes(root))

    def test_target_row_still_renamed(self):
        root = parse_hierarchy(_MD_WITHOUT_WIKI)
        sync_hierarchy(
            root, {"1": {"name": "Caring Circle"}}, {}, set(), set(), set(),
            dry_run=True, only_group_id="1",
        )
        assert any(n.name == "Caring Circle" for n in iter_nodes(root))
