"""
Unit tests for the pure naming helpers in util/wiki_utils.py.

All names are fictional.
"""

from util.wiki_utils import wiki_slug, wiki_title


class TestWikiSlug:
    def test_basic(self):
        assert wiki_slug("Membership") == "membership-wiki"

    def test_multi_word(self):
        assert wiki_slug("Landscape Working Group") == "landscape-working-group-wiki"

    def test_accents_folded(self):
        assert wiki_slug("Café") == "cafe-wiki"

    def test_ampersand_becomes_dash(self):
        assert wiki_slug("Process & Governance") == "process-governance-wiki"

    def test_always_ends_in_wiki(self):
        assert wiki_slug("Alpha Circle").endswith("-wiki")

    def test_leading_trailing_punctuation_stripped(self):
        assert wiki_slug("  Care Circle!  ") == "care-circle-wiki"


class TestWikiTitle:
    def test_basic(self):
        assert wiki_title("Membership") == "Membership Wiki"

    def test_preserves_exact_name(self):
        assert wiki_title("Landscape Working Group") == "Landscape Working Group Wiki"
