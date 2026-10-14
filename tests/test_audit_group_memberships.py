"""
Tests for audit_group_memberships.py's address normalization, membership
comparison, and report formatting.

All names and addresses are fictional.
"""

from audit_group_memberships import (
    DriveMember,
    GroupState,
    UserIndex,
    compare_group,
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


class TestNormalizeEmail:
    def test_gmail_dots_and_plus(self):
        assert normalize_email("Dana.Smith+lists@GMail.com") == "danasmith@gmail.com"

    def test_googlemail_domain(self):
        assert normalize_email("dana.smith@googlemail.com") == "danasmith@gmail.com"

    def test_other_domains_keep_dots(self):
        assert normalize_email("Dana.Smith@Example.org") == "dana.smith@example.org"


class TestCompareGroup:
    def test_consistent_group_has_no_rows(self):
        state = _state(drive_members=[DriveMember("danasmith@gmail.com"),
                                      DriveMember("sam@example.org")],
                       google_members=["dana.smith@gmail.com", "sam@example.org"])
        assert compare_group(state, USERS, set()) == []

    def test_any_user_address_matches(self):
        # Dana's regular email in the Google Group still counts as Dana.
        state = _state(gather_member_ids={"1"},
                       drive_members=[DriveMember("dana.smith@gmail.com")],
                       google_members=["dana@example.org"])
        assert compare_group(state, USERS, set()) == []

    def test_missing_from_google_group(self):
        state = _state(gather_member_ids={"1"},
                       drive_members=[DriveMember("dana.smith@gmail.com")])
        [row] = compare_group(state, USERS, set())
        assert (row.in_gather, row.in_drive, row.in_google) == (True, True, False)

    def test_stranger_in_drive_and_group(self):
        state = _state(gather_member_ids=set(),
                       drive_members=[DriveMember("old@example.org")],
                       google_members=["old@example.org"])
        [row] = compare_group(state, USERS, set())
        assert row.label == "old@example.org"
        assert (row.in_gather, row.in_drive, row.in_google) == (False, True, True)

    def test_no_google_email_note(self):
        state = _state(gather_member_ids={"2"})
        [row] = compare_group(state, USERS, set())
        assert "no Google email in Gather" in row.notes

    def test_other_drive_role_note(self):
        state = _state(gather_member_ids={"2"}, drive_roles={"sam@example.org": "writer"})
        [row] = compare_group(state, USERS, set())
        assert "has Drive role 'writer' instead" in row.notes

    def test_other_drive_role_note_for_non_member(self):
        # Not in Gather, but in the Google Group with a non-syncing Drive role.
        state = _state(gather_member_ids=set(), google_members=["old@example.org"],
                       drive_roles={"old@example.org": "organizer"})
        [row] = compare_group(state, USERS, set())
        assert (row.in_gather, row.in_drive, row.in_google) == (False, False, True)
        assert "has Drive role 'organizer' instead" in row.notes

    def test_other_drive_role_note_for_non_member_user(self):
        # A Gather user who isn't a member of this group.
        state = _state(gather_member_ids=set(), google_members=["pat@gmail.com"],
                       drive_roles={"pat@gmail.com": "writer"})
        [row] = compare_group(state, USERS, set())
        assert row.label.startswith("Pat Jones")
        assert "has Drive role 'writer' instead" in row.notes
        assert "no Google email in Gather" not in row.notes

    def test_inherited_note(self):
        state = _state(gather_member_ids=set(),
                       drive_members=[DriveMember("admin@example.org", inherited=True)],
                       google_members=["admin@example.org"])
        [row] = compare_group(state, USERS, set())
        assert "Drive access inherited from the shared drive" in row.notes

    def test_ignore(self):
        state = _state(gather_member_ids=set(), google_members=["Runner@Example.org"])
        assert compare_group(state, USERS, {normalize_email("runner@example.org")}) == []

    def test_unreadable_source_is_left_out(self):
        # No Drive item: only Gather vs Google Group is compared.
        state = _state(gather_member_ids={"1"}, drive_members=None,
                       google_members=["dana@example.org"])
        assert compare_group(state, USERS, set()) == []

    def test_everybody_uses_adults_and_flags_possible_opt_out(self):
        state = _state(everybody=True, gather_member_ids=set(),
                       drive_members=[DriveMember("dana.smith@gmail.com"),
                                      DriveMember("sam@example.org")],
                       google_members=["dana.smith@gmail.com", "sam@example.org"])
        [row] = compare_group(state, USERS, set())
        assert row.label.startswith("Pat Jones")  # Kit (a child) is excluded
        assert "may have opted out" in row.notes


class TestFormatGroupReport:
    def test_layout(self):
        state = _state(list_email="landscape@example.org", item_label="folder 'Landscape'",
                       problems=["footer has no Drive link"],
                       gather_member_ids={"1"}, drive_members=None)
        text = format_group_report(state, compare_group(state, USERS, set()))
        lines = text.splitlines()
        assert lines[0] == "Landscape Circle   (landscape@example.org · folder 'Landscape')"
        assert lines[1] == "  ! footer has no Drive link"
        assert "Gather  Drive  Google Group" in lines[2]
        assert "Dana Smith <dana.smith@gmail.com>" in lines[3]
        assert "✓" in lines[3] and "–" in lines[3] and "✗" in lines[3]
