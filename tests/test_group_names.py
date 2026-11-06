"""
Tests for util.gdrive_config's group naming: full_group_name() and
folder_name_for_group_type().
"""

import pytest

from util.gdrive_config import (
    folder_name_for_group_type,
    full_group_name,
    google_group_name_for_group_type,
    parent_folder_id_for_group_type,
)


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


class TestAdminGroups:
    def test_no_type_suffix(self):
        assert full_group_name("Community Meetings", "squad") == "Community Meetings"
        assert folder_name_for_group_type("community meetings", "squad") == "Community Meetings"

    def test_google_group_name_ends_in_admins(self):
        assert google_group_name_for_group_type("Community Meetings", "squad") == \
            "Community Meetings Admins"
        assert google_group_name_for_group_type("Community Meetings Admins", "squad") == \
            "Community Meetings Admins"

    def test_other_groups_google_group_named_like_the_folder(self):
        assert google_group_name_for_group_type("Technology Circle", "circle") == \
            "Technology Circle"


class FakeDrive:
    """Just enough of the Drive API for find_folder_id_under_parent: one
    top-level folder per name in names."""

    def __init__(self, names):
        self.names = names

    def files(self):
        return self

    def list(self, q, **kwargs):
        self.q = q
        return self

    def execute(self):
        name = next((n for n in self.names if f"name='{n}'" in self.q), None)
        return {"files": [{"id": f"id-{name}", "name": name}] if name else []}


def test_admin_group_folders_go_under_meeting_admin():
    drive = FakeDrive(["Clubs", "Meeting Admin"])
    assert parent_folder_id_for_group_type(drive, "D", "squad") == "id-Meeting Admin"
    assert parent_folder_id_for_group_type(drive, "D", "club") == "id-Clubs"
    assert parent_folder_id_for_group_type(drive, "D", "circle") == "D"


def test_missing_meeting_admin_folder_raises():
    with pytest.raises(ValueError, match="Meeting Admin"):
        parent_folder_id_for_group_type(FakeDrive([]), "D", "squad")
