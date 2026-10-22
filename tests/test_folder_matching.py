"""
Tests for util/folder_matching.py's acronym matching.

All names are fictional.
"""

from util.folder_matching import find_matching_names, group_acronym, mentions_acronym

DFL = "Development, Finance, & Legal Circle"


def test_group_acronym():
    assert group_acronym(DFL) == "dfl"
    assert group_acronym("Book Club") == "bc"
    assert group_acronym("Technology Circle") == "t"


def test_mentions_acronym():
    for name in ("DF&L meeting", "DF & L meeting", "Budget (D.F.L.)", "BM: DFL",
                 "dfl monthly"):
        assert mentions_acronym(DFL, name), name
    for name in ("DFLX meeting", "Dfly", "Development review", ""):
        assert not mentions_acronym(DFL, name), name


def test_single_letter_acronym_never_matches():
    assert not mentions_acronym("Technology Circle", "T meeting")


def test_find_matching_names_adds_acronym_matches_once():
    items = [{"id": "1", "name": "Development, Finance, & Legal"},
             {"id": "2", "name": "DF&L monthly"},
             {"id": "3", "name": "Book Club"}]
    assert [(m["id"], m["match_kind"]) for m in find_matching_names(DFL, items)] == [
        ("1", "strict"), ("2", "acronym")]
