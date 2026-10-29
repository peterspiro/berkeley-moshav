"""
Tests for util.gdrive_config's group naming: full_group_name() and
folder_name_for_group_type().
"""

import pytest

from util.gdrive_config import folder_name_for_group_type, full_group_name


@pytest.mark.parametrize("name, kind, full", [
    ("Membership", "circle", "Membership Circle"),
    ("Technology Circle", "circle", "Technology Circle"),
    ("Outreach", "committee", "Outreach Working Group"),
    ("Book Club", "club", "Book Club"),
    ("Garden Pod", "circle", "Garden Pod"),
    ("garden pod ", "circle", "garden pod"),
    ("Podcast", "circle", "Podcast Circle"),
])
def test_full_group_name(name, kind, full):
    assert full_group_name(name, kind) == full


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        full_group_name("Garden Pod", "everybody")


def test_folder_names_still_get_the_type():
    assert folder_name_for_group_type("garden pod", "circle") == "Garden Pod Circle"
    assert folder_name_for_group_type("outreach", "committee") == "Outreach Working Group"
