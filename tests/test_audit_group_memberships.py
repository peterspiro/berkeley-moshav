"""
Tests for audit_group_memberships.py's address normalization, direct-role
logic, membership comparison, and report formatting.

All names and addresses are fictional.
"""

from audit_group_memberships import (
    GroupState,
    UserIndex,
    compare_group,
    direct_role,
    format_group_report,
    normalize_email,
)
from util.gather_utils import GatherUser

DANA = GatherUser("1", "Dana", "Smith", "Dana Smith", email="dana@example.org",
                  google_email="dana.smith@gmail.com")
SAM = GatherUser("2", "Sam", "Lee", "Sam Lee", email="sam@example.org")
KID = GatherUser("3", "Kit", "Smith", "Kit Smith", child=True, email="kit@example.org")
PAT = GatherUser("4", "Pat", "Jones", "Pat Jones", email="pat@example.org",
                 google_email="pat@gmail.com")
USERS = UserIndex([DANA, SAM, KID, PAT])


def _state(**kw):
    args = dict(group_id="9", name="Landscape Circle", everybody=False,
                gather_member_ids={"1", "2"}, drive_members=[], google_members=[])
    args.update(kw)
    return GroupState(**args)


def _rows(state, ignore=frozenset()):
    return compare_group(state, USERS, set(ignore))[0]


def _other(state, ignore=frozenset()):
    return compare_group(state, USERS, set(ignore))[1]


class TestNormalizeEmail:
    def test_gmail_dots_and_plus(self):
        assert normalize_email("Dana.Smith+lists@GMail.com") == "danasmith@gmail.com"

    def test_googlemail_domain(self):
        assert normalize_email("dana.smith@googlemail.com") == "danasmith@gmail.com"

    def test_other_domains_keep_dots(self):
        assert normalize_email("Dana.Smith@Example.org") == "dana.smith@example.org"


class TestDirectRole:
    def test_no_details_uses_role(self):
        assert direct_role({"role": "writer"}) == "writer"

    def test_inherited_only_is_none(self):
        p = {"role": "organizer", "permissionDetails": [{"role": "organizer", "inherited": True}]}
        assert direct_role(p) is None

    def test_direct_role_ignores_higher_inherited_one(self):
        # A folder Content manager who is also a shared-drive Manager.
        p = {"role": "organizer", "permissionDetails": [
            {"role": "organizer", "inherited": True},
            {"role": "fileOrganizer", "inherited": False}]}
        assert direct_role(p) == "fileOrganizer"

    def test_highest_direct_role(self):
        p = {"role": "writer", "permissionDetails": [
            {"role": "reader", "inherited": False}, {"role": "writer", "inherited": False}]}
        assert direct_role(p) == "writer"


class TestCompareGroup:
    def test_consistent_group_has_no_rows(self):
        state = _state(drive_members=["danasmith@gmail.com", "sam@example.org"],
                       google_members=["dana.smith@gmail.com", "sam@example.org"])
        assert _rows(state) == []

    def test_any_user_address_matches(self):
        # Dana's regular email in the Google Group still counts as Dana.
        state = _state(gather_member_ids={"1"}, drive_members=["dana.smith@gmail.com"],
                       google_members=["dana@example.org"])
        assert _rows(state) == []

    def test_missing_from_google_group(self):
        state = _state(gather_member_ids={"1"}, drive_members=["dana.smith@gmail.com"])
        [row] = _rows(state)
        assert (row.in_gather, row.in_drive, row.in_google) == (True, True, False)

    def test_stranger_in_drive_and_group(self):
        state = _state(gather_member_ids=set(), drive_members=["old@example.org"],
                       google_members=["old@example.org"])
        [row] = _rows(state)
        assert row.label == "old@example.org"
        assert (row.in_gather, row.in_drive, row.in_google) == (False, True, True)

    def test_no_google_email_note(self):
        state = _state(gather_member_ids={"2"})
        [row] = _rows(state)
        assert "no Google email in Gather" in row.notes

    def test_other_drive_role_note(self):
        state = _state(gather_member_ids={"2"}, drive_access=[("sam@example.org", "writer")])
        [row] = _rows(state)
        assert "has Drive role Contributor instead" in row.notes

    def test_other_drive_role_note_for_non_member(self):
        # Not in Gather, but in the Google Group with a non-syncing Drive role.
        state = _state(gather_member_ids=set(), google_members=["old@example.org"],
                       drive_access=[("old@example.org", "organizer")])
        [row] = _rows(state)
        assert (row.in_gather, row.in_drive, row.in_google) == (False, False, True)
        assert "has Drive role Manager instead" in row.notes

    def test_other_drive_role_note_for_non_member_user(self):
        # A Gather user who isn't a member of this group.
        state = _state(gather_member_ids=set(), google_members=["pat@gmail.com"],
                       drive_access=[("pat@gmail.com", "writer")])
        [row] = _rows(state)
        assert row.label.startswith("Pat Jones")
        assert "has Drive role Contributor instead" in row.notes
        assert "no Google email in Gather" not in row.notes

    def test_ignore(self):
        state = _state(gather_member_ids=set(), google_members=["Runner@Example.org"])
        assert _rows(state, {normalize_email("runner@example.org")}) == []

    def test_unreadable_source_is_left_out(self):
        # No Drive item: only Gather vs Google Group is compared.
        state = _state(gather_member_ids={"1"}, drive_members=None,
                       google_members=["dana@example.org"])
        assert _rows(state) == []

    def test_everybody_uses_adults_and_flags_possible_opt_out(self):
        state = _state(everybody=True, gather_member_ids=set(),
                       drive_members=["dana.smith@gmail.com", "sam@example.org"],
                       google_members=["dana.smith@gmail.com", "sam@example.org"])
        [row] = _rows(state)
        assert row.label.startswith("Pat Jones")  # Kit (a child) is excluded
        assert "may have opted out" in row.notes


    def test_everybody_role_note_replaces_opt_out_note(self):
        state = _state(everybody=True, gather_member_ids=set(),
                       drive_members=["sam@example.org", "pat@gmail.com"],
                       google_members=["sam@example.org", "pat@gmail.com"],
                       drive_access=[("dana.smith@gmail.com", "organizer")])
        [row] = _rows(state)
        assert row.notes == ["has Drive role Manager instead"]


class TestOtherAccess:
    def test_non_member_with_other_role_listed(self):
        state = _state(everybody=True, gather_member_ids=set(),
                       drive_members=["dana.smith@gmail.com", "sam@example.org",
                                      "pat@gmail.com"],
                       google_members=["dana.smith@gmail.com", "sam@example.org",
                                       "pat@gmail.com"],
                       drive_access=[("contractor@example.com", "organizer"),
                                     ("dana.smith@gmail.com", "reader")])
        assert _rows(state) == []
        assert _other(state) == [("contractor@example.com", "Manager")]

    def test_gather_member_not_listed(self):
        # A member with the wrong role gets a row with a note instead.
        state = _state(gather_member_ids={"2"}, drive_access=[("sam@example.org", "writer")])
        assert _other(state) == []

    def test_non_member_gather_user_labelled_by_name(self):
        state = _state(gather_member_ids=set(), drive_access=[("pat@gmail.com", "writer")])
        assert _other(state) == [("Pat Jones <pat@gmail.com>", "Contributor")]

    def test_ignored(self):
        state = _state(gather_member_ids=set(), drive_access=[("admin@example.org", "organizer")])
        assert _other(state, {"admin@example.org"}) == []


class TestFormatGroupReport:
    def test_layout(self):
        state = _state(list_email="landscape@example.org", item_label="folder 'Landscape'",
                       problems=["footer has no Drive link"],
                       gather_member_ids={"1"}, drive_members=None)
        rows, other = compare_group(state, USERS, set())
        lines = format_group_report(state, rows, other).splitlines()
        assert lines[0] == "Landscape Circle   (landscape@example.org · folder 'Landscape')"
        assert lines[1] == "  ! footer has no Drive link"
        assert "Gather  Drive  Google Group" in lines[2]
        assert "Dana Smith <dana.smith@gmail.com>" in lines[3]
        assert "✓" in lines[3] and "–" in lines[3] and "✗" in lines[3]

    def test_other_access_section(self):
        state = _state(gather_member_ids=set(),
                       drive_access=[("contractor@example.com", "organizer")])
        rows, other = compare_group(state, USERS, set())
        lines = format_group_report(state, rows, other).splitlines()
        assert lines[1] == "  Other access (not Gather members, not synced to the list):"
        assert lines[2] == "    contractor@example.com  Manager"
