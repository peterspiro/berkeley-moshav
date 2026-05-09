"""Tests for gather_photos.py parsing and matching logic."""

import pytest

from gather_photos import (
    _H2Parser,
    _PhotoLinkParser,
    find_members_for_names,
    get_first_names_from_page,
    scrape_community_photos,
)


# ── _H2Parser ─────────────────────────────────────────────────────────────────

class TestH2Parser:
    def _parse(self, html):
        p = _H2Parser()
        p.feed(html)
        return p.h2_text

    def test_simple(self):
        assert self._parse("<h2>HILARY &amp; NOAH</h2>") == "HILARY & NOAH"

    def test_class_attribute(self):
        assert (
            self._parse('<h2 class="wsite-content-title">SARAH</h2>') == "SARAH"
        )

    def test_first_h2_only(self):
        assert self._parse("<h2>FIRST</h2><h2>SECOND</h2>") == "FIRST"

    def test_no_h2(self):
        assert self._parse("<h1>Title</h1><p>text</p>") is None

    def test_nested_tags_inside_h2(self):
        assert self._parse("<h2><strong>JOHN</strong> &amp; JANE</h2>") == "JOHN & JANE"

    def test_strips_whitespace(self):
        assert self._parse("<h2>  ALICE  </h2>") == "ALICE"


# ── get_first_names_from_page (via _H2Parser) ─────────────────────────────────

class TestGetFirstNamesFromPage:
    def _names(self, html):
        """Parse first names from HTML without an HTTP request."""
        from gather_photos import _H2Parser
        import re

        p = _H2Parser()
        p.feed(html)
        if not p.h2_text:
            return []
        parts = re.split(r"\s*&\s*|\s+AND\s+", p.h2_text, flags=re.IGNORECASE)
        names = []
        for part in parts:
            words = part.strip().split()
            if words:
                word = words[0].strip(".,;:!?\"'")
                if word:
                    names.append(word.title())
        return [n for n in names if n]

    def test_two_names_ampersand(self):
        assert self._names("<h2>HILARY &amp; NOAH</h2>") == ["Hilary", "Noah"]

    def test_single_name(self):
        assert self._names("<h2>SARAH</h2>") == ["Sarah"]

    def test_two_names_and_word(self):
        assert self._names("<h2>JOHN AND JANE</h2>") == ["John", "Jane"]

    def test_name_with_last_name(self):
        # "HILARY SMITH & NOAH JONES" — only first names extracted
        assert self._names("<h2>HILARY SMITH &amp; NOAH JONES</h2>") == ["Hilary", "Noah"]

    def test_already_title_case(self):
        assert self._names("<h2>Alice &amp; Bob</h2>") == ["Alice", "Bob"]

    def test_no_h2_returns_empty(self):
        assert self._names("<p>No heading</p>") == []


# ── _PhotoLinkParser ──────────────────────────────────────────────────────────

class TestPhotoLinkParser:
    BASE = "https://www.example.org/meet-the-community.html"

    def _parse(self, html):
        p = _PhotoLinkParser(self.BASE)
        p.feed(html)
        return p.photos

    def test_basic_link_with_image(self):
        html = '<a href="/alice"><img src="/photo/alice.jpg"></a>'
        photos = self._parse(html)
        assert len(photos) == 1
        assert photos[0]["page_url"] == "https://www.example.org/alice"
        assert photos[0]["img_url"] == "https://www.example.org/photo/alice.jpg"

    def test_external_links_excluded(self):
        html = '<a href="https://other.com/page"><img src="/photo.jpg"></a>'
        assert self._parse(html) == []

    def test_self_link_excluded(self):
        html = f'<a href="{self.BASE}"><img src="/photo.jpg"></a>'
        assert self._parse(html) == []

    def test_anchor_only_excluded(self):
        html = '<a href="#section"><img src="/photo.jpg"></a>'
        assert self._parse(html) == []

    def test_deduplication_keeps_first(self):
        html = (
            '<a href="/alice"><img src="/photo1.jpg"></a>'
            '<a href="/alice"><img src="/photo2.jpg"></a>'
        )
        photos = self._parse(html)
        assert len(photos) == 1
        assert photos[0]["img_url"] == "https://www.example.org/photo1.jpg"

    def test_multiple_distinct_pages(self):
        html = (
            '<a href="/alice"><img src="/alice.jpg"></a>'
            '<a href="/bob"><img src="/bob.jpg"></a>'
        )
        photos = self._parse(html)
        assert len(photos) == 2
        page_urls = {p["page_url"] for p in photos}
        assert page_urls == {
            "https://www.example.org/alice",
            "https://www.example.org/bob",
        }

    def test_first_image_per_anchor(self):
        html = '<a href="/alice"><img src="/a1.jpg"><img src="/a2.jpg"></a>'
        photos = self._parse(html)
        assert len(photos) == 1
        assert photos[0]["img_url"] == "https://www.example.org/a1.jpg"

    def test_protocol_relative_img(self):
        html = '<a href="/alice"><img src="//cdn.example.org/photo.jpg"></a>'
        photos = self._parse(html)
        assert photos[0]["img_url"] == "https://cdn.example.org/photo.jpg"

    def test_data_src_fallback(self):
        html = '<a href="/alice"><img data-src="/lazy.jpg"></a>'
        photos = self._parse(html)
        assert photos[0]["img_url"] == "https://www.example.org/lazy.jpg"


# ── find_members_for_names ────────────────────────────────────────────────────

def _make_household(members_spec):
    """Build a minimal household dict from a list of (first, last, email) tuples."""
    members = [
        {"first_name": f, "last_name": l, "email": e, "child": False}
        for f, l, e in members_spec
    ]
    return {"household_name": f"{members_spec[0][1]}", "members": members}


HOUSEHOLDS = [
    _make_household([("Hilary", "Smith", "hilary@example.com"),
                     ("Noah", "Smith", "noah@example.com")]),
    _make_household([("Sarah", "Jones", "sarah@example.com")]),
    _make_household([("Alice", "Brown", "alice@example.com"),
                     ("Bob", "Brown", "bob@example.com"),
                     ("Charlie", "Brown", "charlie@example.com")]),
]


class TestFindMembersForNames:
    def test_single_name_unique_match(self):
        members = find_members_for_names(HOUSEHOLDS, ["Sarah"])
        assert len(members) == 1
        assert members[0]["first_name"] == "Sarah"

    def test_single_name_no_match(self, capfd):
        members = find_members_for_names(HOUSEHOLDS, ["Unknown"])
        assert members == []
        out = capfd.readouterr().out
        assert "No member found" in out

    def test_single_name_ambiguous(self, capfd):
        # Add a duplicate first name across households
        households = HOUSEHOLDS + [
            _make_household([("Sarah", "Wilson", "sarah2@example.com")])
        ]
        members = find_members_for_names(households, ["Sarah"])
        assert members == []
        out = capfd.readouterr().out
        assert "Ambiguous" in out

    def test_two_names_same_household(self):
        members = find_members_for_names(HOUSEHOLDS, ["Hilary", "Noah"])
        assert len(members) == 2
        firsts = {m["first_name"] for m in members}
        assert firsts == {"Hilary", "Noah"}

    def test_two_names_returns_only_named(self):
        # Alice and Bob are in a 3-person household; Charlie should not appear
        members = find_members_for_names(HOUSEHOLDS, ["Alice", "Bob"])
        assert len(members) == 2
        firsts = {m["first_name"] for m in members}
        assert firsts == {"Alice", "Bob"}
        assert "Charlie" not in firsts

    def test_two_names_no_household(self, capfd):
        members = find_members_for_names(HOUSEHOLDS, ["Hilary", "Sarah"])
        assert members == []
        out = capfd.readouterr().out
        assert "No household found" in out

    def test_case_insensitive(self):
        members = find_members_for_names(HOUSEHOLDS, ["hilary", "noah"])
        assert len(members) == 2
