"""
Tests that update_groups_in_google_and_hierarchy.sync_hierarchy reports its
Circle Structure edits (renames, Documents links) as "would ..." in a dry
run, like the script's other steps, and plainly in a real run.

All names and links are fictional.
"""

import textwrap

import pytest

import update_groups_in_google_and_hierarchy as m
from util.hierarchy_wiki import iter_nodes, parse_hierarchy

_MD = textwrap.dedent("""\
    - Root: [Members](/groups/0)
        - Care Circle: [Members](/groups/1)
""")


@pytest.fixture
def logged(monkeypatch):
    events = []
    monkeypatch.setattr(m, "log", lambda level, event, detail: events.append(event))
    return events


def _sync(dry_run, documents_url_by_group_id=None):
    root = parse_hierarchy(_MD)
    m.sync_hierarchy(
        root, {"1": {"name": "Caring Circle"}}, documents_url_by_group_id or {},
        set(), set(), set(), dry_run=dry_run,
    )
    return root


def test_dry_run_rename_says_would(logged, capsys):
    root = _sync(dry_run=True)
    out = capsys.readouterr().out
    assert "[dry-run] Circle Structure: would rename 'Care Circle' -> 'Caring Circle'" in out
    assert logged == ["would_rename"]
    # The in-memory tree is still updated, so later steps see the new name.
    assert any(n.name == "Caring Circle" for n in iter_nodes(root))


def test_real_run_rename(logged, capsys):
    _sync(dry_run=False)
    out = capsys.readouterr().out
    assert "Circle Structure: Rename 'Care Circle' -> 'Caring Circle'" in out
    assert "[dry-run]" not in out
    assert logged == ["rename"]


def test_dry_run_documents_link_says_would(logged, capsys):
    _sync(dry_run=True, documents_url_by_group_id={"1": "/gdrive/item/5"})
    out = capsys.readouterr().out
    assert "[dry-run] Circle Structure: would add Documents link for 'Caring Circle'" in out
    assert logged == ["would_rename", "would_add_documents_link"]
