"""
Tests for moving an entry on the Circle Structure page: move_node and
is_descendant in util/hierarchy_wiki.py, and change_parent_circle.py's
resolve_move. All names are fictional.
"""

import textwrap

import pytest

from change_parent_circle import resolve_move
from util.hierarchy_wiki import move_node, parse_hierarchy, render_hierarchy

PAGE = textwrap.dedent("""\
    - Full Community (Circle of Everyone / Top Circle / HOA): [Members](/groups/1)
        - Operations Circle: [Members](/groups/2)
            - Garden Working Group: [Members](/groups/3) | a note
                - Seeds Working Group: [Members](/groups/4)
        - Property Circle: [Members](/groups/5)
            - Roof Working Group: [Members](/groups/6)
""")


def test_moves_entry_with_its_sub_circles():
    root = parse_hierarchy(PAGE)
    garden, prop = resolve_move(root, "garden", "prop")
    move_node(garden, prop)
    assert render_hierarchy(root) == textwrap.dedent("""\
        - Full Community (Circle of Everyone / Top Circle / HOA): [Members](/groups/1)
            - Operations Circle: [Members](/groups/2)
            - Property Circle: [Members](/groups/5)
                - Garden Working Group: [Members](/groups/3) | a note
                    - Seeds Working Group: [Members](/groups/4)
                - Roof Working Group: [Members](/groups/6)
        """)
    assert garden.parent is prop


def test_moves_entry_to_the_root():
    root = parse_hierarchy(PAGE)
    roof, top = resolve_move(root, "roof", "full")
    move_node(roof, top)
    assert [c.name for c in top.children] == ["Operations Circle", "Property Circle",
                                              "Roof Working Group"]


@pytest.mark.parametrize("circle, parent, message", [
    ("garden", "seeds", "beneath it"),
    ("garden", "garden", "itself"),
    ("full", "prop", "top of the Circle Structure"),
    ("nothing", "prop", "No hierarchy entry"),
    ("garden", "zzz", "No hierarchy entry"),
])
def test_refuses_impossible_moves(circle, parent, message):
    with pytest.raises(ValueError, match=message):
        resolve_move(parse_hierarchy(PAGE), circle, parent)


def test_ambiguous_prefix_is_refused():
    page = PAGE + "        - Gardening Club: [Members](/groups/7)\n"
    with pytest.raises(ValueError, match="multiple"):
        resolve_move(parse_hierarchy(page), "garden", "prop")


def test_move_node_refuses_a_cycle():
    root = parse_hierarchy(PAGE)
    ops = root.children[0]
    with pytest.raises(ValueError):
        move_node(ops, ops.children[0])
