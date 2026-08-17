"""
Unit tests for the Circle Hierarchy tree parse/render helpers in
util/hierarchy_wiki.py, focused on retaining row content the script
doesn't manage (extra links or notes added by hand).

All names and links are fictional.
"""

import textwrap

from util.hierarchy_wiki import (
    _parse_rest,
    parse_hierarchy,
    render_hierarchy,
    remove_node,
    iter_nodes,
)


# ── _parse_rest: classifying managed vs. unmanaged segments ──────────────────

class TestParseRest:
    def test_managed_links_extracted(self):
        name, members, docs, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2)"
        )
        assert name == "Care Circle"
        assert members == "/groups/1"
        assert docs == "/gdrive/item/2"
        assert extras == []

    def test_extra_link_captured(self):
        _, _, _, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2) "
            "| [Notes](/wiki/care-notes)"
        )
        assert extras == ["[Notes](/wiki/care-notes)"]

    def test_extra_link_without_managed_links(self):
        name, members, docs, extras = _parse_rest("Care Circle: [Notes](/wiki/care-notes)")
        assert name == "Care Circle"
        assert members == ""
        assert docs == ""
        assert extras == ["[Notes](/wiki/care-notes)"]

    def test_multiple_extras_kept_in_order(self):
        _, _, _, extras = _parse_rest(
            "Care Circle: [Members](/groups/1) | [A](/a) | [B](/b)"
        )
        assert extras == ["[A](/a)", "[B](/b)"]

    def test_plain_name_has_no_extras(self):
        name, members, docs, extras = _parse_rest("Care Circle")
        assert name == "Care Circle"
        assert (members, docs, extras) == ("", "", [])


# ── Round-trip: extras survive parse -> render ───────────────────────────────

class TestRoundTrip:
    def test_extra_link_preserved_alongside_managed_links(self):
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2) | [Notes](/wiki/care-notes)
        """)
        out = render_hierarchy(parse_hierarchy(md))
        assert "[Members](/groups/1)" in out
        assert "[Documents](/gdrive/item/2)" in out
        assert "[Notes](/wiki/care-notes)" in out

    def test_extra_link_preserved_without_managed_links(self):
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Care Circle: [Notes](/wiki/care-notes)
        """)
        out = render_hierarchy(parse_hierarchy(md))
        # The row keeps its extra link (and its ": " prefix) rather than
        # collapsing to a bare "- Care Circle".
        assert "- Care Circle: [Notes](/wiki/care-notes)" in out

    def test_managed_links_emitted_before_extras(self):
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Care Circle: [Notes](/wiki/care-notes) | [Members](/groups/1) | [Documents](/gdrive/item/2)
        """)
        out = render_hierarchy(parse_hierarchy(md))
        line = next(l for l in out.splitlines() if "Care Circle" in l)
        assert line.index("[Members]") < line.index("[Documents]") < line.index("[Notes]")

    def test_idempotent_after_first_render(self):
        # A page whose extras are already after the managed links is stable:
        # rendering it again produces identical output.
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Care Circle: [Members](/groups/1) | [Documents](/gdrive/item/2) | [Notes](/wiki/care-notes)
                - Housing Circle: [Members](/groups/3)
        """)
        once = render_hierarchy(parse_hierarchy(md))
        twice = render_hierarchy(parse_hierarchy(once))
        assert once == twice

    def test_reordered_extras_are_stable_after_first_pass(self):
        # An extra placed before the managed links is moved to the end on the
        # first render, then stays put on every subsequent render.
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Care Circle: [Notes](/wiki/care-notes) | [Members](/groups/1)
        """)
        once = render_hierarchy(parse_hierarchy(md))
        twice = render_hierarchy(parse_hierarchy(once))
        assert once == twice


# ── Extras travel with a node through reparenting ────────────────────────────

class TestReparenting:
    def test_extras_survive_remove_node(self):
        md = textwrap.dedent("""\
            - Root: [Members](/groups/0)
                - Middle Circle: [Members](/groups/1)
                    - Leaf Circle: [Members](/groups/2) | [Notes](/wiki/leaf-notes)
        """)
        root = parse_hierarchy(md)
        middle = next(n for n in iter_nodes(root) if n.name == "Middle Circle")
        remove_node(middle)  # Leaf Circle is reparented up to Root
        out = render_hierarchy(root)
        assert "Middle Circle" not in out
        assert "- Leaf Circle: [Members](/groups/2) | [Notes](/wiki/leaf-notes)" in out
