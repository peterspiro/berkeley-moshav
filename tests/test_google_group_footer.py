"""
Unit tests for util.google_group_footer and the choice, in util.gdrive_config,
of the Drive item a group linked to several follows — the folder/shared-drive distinction the Groups–Drive
sync depends on.

All names, emails, and IDs are fictional.
"""

import re
from pathlib import Path

import pytest

from util.gdrive_config import ambiguous_links_message, choose_membership_links
from util.google_group_footer import (
    FOOTER_MARKER,
    ITEM_TYPE_FOLDER,
    ITEM_TYPE_SHARED_DRIVE,
    build_footer_block,
    calendar_id_from_cid,
    calendar_url,
    compute_group_footer_updates,
    describe_footer_updates,
    footer_updates,
    parse_addresses,
    parse_footer_also_invite,
    parse_footer_audience,
    parse_footer_calendar_id,
    parse_footer_host_key_url,
    parse_footer_no_zoom,
    parse_footer_folder_id,
    parse_footer_link,
    format_meeting_id,
    parse_footer_zoom_passcode,
    parse_footer_zoom_url,
    replace_footer_zoom,
)

GROUPS_DRIVE_SYNC_GS = Path(__file__).resolve().parent.parent / "groups_drive_sync" / "groups_drive_sync.gs"

CAL_ID = "c_0123abcd@group.calendar.google.com"
ZOOM_URL = "https://us06web.zoom.us/j/81234567890?pwd=aBc.1"


class _FakeSettingsService:
    """Minimal stand-in for the Groups Settings API service: returns a
    canned group settings dict from groups().get(...).execute()."""

    def __init__(self, settings):
        self._settings = settings

    def groups(self):
        return self

    def get(self, groupUniqueId):
        return self

    def execute(self):
        return self._settings


class TestBuildFooterBlock:
    def test_folder_label_and_url(self):
        block = build_footer_block("42", "FOLDERID", "https://x.coop", "Foo Circle",
                                   ITEM_TYPE_FOLDER)
        assert block == (
            f"{FOOTER_MARKER}\n"
            "Foo Circle's:\n"
            "Gather group: https://x.coop/groups/42\n"
            "Google Docs folder: https://drive.google.com/drive/folders/FOLDERID"
        )

    def test_shared_drive_label_and_url(self):
        block = build_footer_block("7", "DRIVEID", "https://x.coop", "Bar Working Group",
                                   ITEM_TYPE_SHARED_DRIVE)
        assert "\nShared Drive: https://drive.google.com/drive/folders/DRIVEID" in block
        assert "Google Docs folder:" not in block

    def test_default_item_type_is_folder(self):
        assert build_footer_block("1", "ID", "https://x.coop", "Baz") == \
            build_footer_block("1", "ID", "https://x.coop", "Baz", ITEM_TYPE_FOLDER)

    def test_calendar_and_zoom_lines_follow_drive_line(self):
        block = build_footer_block("42", "FID", "https://x.coop", "Foo Circle",
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL)
        assert block.splitlines()[1:] == [
            "Foo Circle's:",
            "Gather group: https://x.coop/groups/42",
            "Google Docs folder: https://drive.google.com/drive/folders/FID",
            f"Google calendar: {calendar_url(CAL_ID)}",
            f"Zoom meeting: {ZOOM_URL}",
        ]

    def test_unknown_item_type_raises(self):
        with pytest.raises(ValueError):
            build_footer_block("1", "ID", "https://x.coop", "Baz", "sharepoint")


class TestParseFooterLink:
    def test_round_trip_folder(self):
        block = build_footer_block("42", "FOLDERID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER)
        assert parse_footer_link(block) == ("FOLDERID", ITEM_TYPE_FOLDER)

    def test_round_trip_shared_drive(self):
        block = build_footer_block("42", "DRIVEID", "https://x.coop", "Foo", ITEM_TYPE_SHARED_DRIVE)
        assert parse_footer_link(block) == ("DRIVEID", ITEM_TYPE_SHARED_DRIVE)

    def test_none_when_no_managed_block(self):
        assert parse_footer_link("Just some human-written footer text.") is None
        assert parse_footer_link("") is None
        assert parse_footer_link(None) is None

    def test_preserved_prefix_is_kept(self):
        block = build_footer_block("42", "FOLDERID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER)
        footer = "Human wrote this.\n\n" + block
        assert parse_footer_link(footer) == ("FOLDERID", ITEM_TYPE_FOLDER)

    def test_both_links_raise(self):
        block = (
            f"{FOOTER_MARKER}\n"
            "Foo Google Docs folder: https://drive.google.com/drive/folders/AAA\n"
            "Foo Shared Drive: https://drive.google.com/drive/folders/BBB"
        )
        with pytest.raises(ValueError):
            parse_footer_link(block)

    def test_old_layout_still_parses(self):
        block = (
            f"{FOOTER_MARKER}\n"
            "Foo Circle Gather group: https://x.coop/groups/42\n"
            "Foo Circle Shared Drive: https://drive.google.com/drive/folders/DRIVEID"
        )
        assert parse_footer_link(block) == ("DRIVEID", ITEM_TYPE_SHARED_DRIVE)

    def test_parse_footer_folder_id_returns_id_for_both(self):
        folder = build_footer_block("1", "FID", "https://x.coop", "F", ITEM_TYPE_FOLDER)
        drive = build_footer_block("1", "DID", "https://x.coop", "F", ITEM_TYPE_SHARED_DRIVE)
        assert parse_footer_folder_id(folder) == "FID"
        assert parse_footer_folder_id(drive) == "DID"
        assert parse_footer_folder_id("no link here") is None


class TestCalendarAndZoomLinks:
    @pytest.mark.parametrize("cal_id", [
        CAL_ID,
        "someone@berkeleymoshav.org",
        "en.usa#holiday@group.v.calendar.google.com",
        "a?>",  # base64 of this contains "+" and "/"
    ])
    def test_calendar_url_round_trip(self, cal_id):
        url = calendar_url(cal_id)
        assert url.startswith("https://calendar.google.com/calendar/u/0?cid=")
        assert "=" not in url.split("cid=", 1)[1]
        block = build_footer_block("1", "FID", "https://x.coop", "Foo", calendar_id=cal_id)
        assert parse_footer_calendar_id(block) == cal_id

    def test_cid_decoding_accepts_padding_and_urlsafe_alphabet(self):
        assert calendar_id_from_cid("YT8-") == "a?>"
        assert calendar_id_from_cid("Zm9v") == "foo"
        assert calendar_id_from_cid("Zm8=") == "fo"
        assert calendar_id_from_cid("Zm8") == "fo"
        assert calendar_id_from_cid("!!!") is None

    def test_zoom_url_keeps_passcode(self):
        block = build_footer_block("1", "FID", "https://x.coop", "Foo", zoom_url=ZOOM_URL)
        assert parse_footer_zoom_url(block) == ZOOM_URL

    def test_none_when_absent(self):
        block = build_footer_block("1", "FID", "https://x.coop", "Foo")
        assert parse_footer_calendar_id(block) is None
        assert parse_footer_zoom_url(block) is None
        assert parse_footer_calendar_id(None) is None
        assert parse_footer_zoom_url("") is None

    def test_links_outside_managed_block_are_ignored(self):
        footer = (f"Zoom meeting: https://zoom.us/j/1\n\n"
                  + build_footer_block("1", "FID", "https://x.coop", "Foo"))
        assert parse_footer_zoom_url(footer) is None


class TestZoomDetails:
    def test_meeting_id_and_passcode_line(self):
        block = build_footer_block("1", "FID", "https://x.coop", "Foo",
                                   zoom_url=ZOOM_URL, zoom_passcode="123456")
        assert block.splitlines()[-2:] == [
            f"Zoom meeting: {ZOOM_URL}",
            "Meeting ID 812 3456 7890, passcode 123456",
        ]
        assert parse_footer_zoom_passcode(block) == "123456"
        assert parse_footer_zoom_url(block) == ZOOM_URL

    def test_no_passcode_no_line(self):
        block = build_footer_block("1", "FID", "https://x.coop", "Foo", zoom_url=ZOOM_URL)
        assert "Meeting ID" not in block
        assert parse_footer_zoom_passcode(block) is None

    @pytest.mark.parametrize("raw,shown", [
        ("81234567890", "812 3456 7890"),
        ("8123456789", "812 345 6789"),
        ("812345678", "812345678"),
    ])
    def test_format_meeting_id(self, raw, shown):
        assert format_meeting_id(raw) == shown


class TestCalendarAudience:
    """A community-wide calendar's admin group's footer names the calendar's
    audience."""

    def block(self, audience="all.members@berkeleymoshav.org"):
        return build_footer_block(
            "7", "F", "https://g", "Community Meetings", calendar_id="c@group.calendar.google.com",
            zoom_url="https://zoom.us/j/81234567890?pwd=x", zoom_passcode="123456",
            audience=audience)

    def test_written_last(self):
        assert self.block().endswith("\nCalendar audience: all.members@berkeleymoshav.org")

    def test_read_back(self):
        assert parse_footer_audience("Notes\n\n" + self.block()) == \
            "all.members@berkeleymoshav.org"

    def test_absent_for_other_groups(self):
        assert parse_footer_audience(self.block(audience=None)) is None
        assert "audience" not in self.block(audience=None)

    def test_kept_when_not_given(self):
        current = {"customFooterText": self.block(), "includeCustomFooter": "true"}
        assert footer_updates(
            current, "7", "F", "https://g", "Community Meetings") == {}

    def test_change_described(self):
        old = self.block()
        updates = footer_updates({"customFooterText": old, "includeCustomFooter": "true"},
                                 "7", "F", "https://g", "Community Meetings",
                                 audience="everyone@berkeleymoshav.org")
        assert parse_footer_audience(updates["customFooterText"]) == "everyone@berkeleymoshav.org"
        assert describe_footer_updates(old, updates) == "calendar audience"

    def test_host_key_link(self):
        block = build_footer_block("7", "F", "https://g", "Community Meetings Admins",
                                   host_key_url="https://docs.google.com/document/d/k/edit",
                                   audience="a@x.org")
        assert block.endswith("\nZoom host key: https://docs.google.com/document/d/k/edit"
                              "\nCalendar audience: a@x.org")
        assert parse_footer_host_key_url(block) == "https://docs.google.com/document/d/k/edit"
        assert parse_footer_zoom_url(block) is None
        current = {"customFooterText": block, "includeCustomFooter": "true"}
        assert footer_updates(current, "7", "F", "https://g", "Community Meetings Admins") == {}

    def test_kept_when_zoom_replaced(self):
        new = replace_footer_zoom(self.block(), "https://zoom.us/j/81234567890?pwd=y", "654321")
        assert parse_footer_audience(new) == "all.members@berkeleymoshav.org"


class TestNoZoom:
    """An admin group made without a Zoom room says so in its footer."""

    def block(self, **kw):
        return build_footer_block("7", "F", "https://g", "Social Events Admins",
                                  calendar_id="c@group.calendar.google.com", no_zoom=True,
                                  audience="a@x.org", **kw)

    def test_written_in_place_of_the_room(self):
        block = self.block()
        assert "\nZoom meeting: none\nCalendar audience: a@x.org" in block
        assert parse_footer_no_zoom(block)
        assert parse_footer_zoom_url(block) is None

    def test_room_wins(self):
        block = self.block(zoom_url="https://zoom.us/j/81234567890?pwd=x")
        assert not parse_footer_no_zoom(block)

    def test_kept_when_not_given(self):
        current = {"customFooterText": self.block(), "includeCustomFooter": "true"}
        assert footer_updates(current, "7", "F", "https://g", "Social Events Admins") == {}

    def test_dropped_when_a_room_is_given(self):
        old = self.block()
        updates = footer_updates({"customFooterText": old, "includeCustomFooter": "true"},
                                 "7", "F", "https://g", "Social Events Admins",
                                 zoom_url="https://zoom.us/j/81234567890?pwd=x",
                                 zoom_passcode="123456")
        new = updates["customFooterText"]
        assert not parse_footer_no_zoom(new)
        assert parse_footer_zoom_url(new) == "https://zoom.us/j/81234567890?pwd=x"
        assert describe_footer_updates(old, updates) == "Zoom: none removed; Zoom link"

    def test_other_groups_never_have_it(self):
        assert not parse_footer_no_zoom(build_footer_block("1", "F", "https://g", "Tech Circle"))


class TestAlsoInvite:
    def block(self, also=("list@example.org", "someone@example.com")):
        return build_footer_block("7", "F", "https://g", "Community Meetings Admins",
                                  audience="a@x.org", also_invite=list(also))

    def test_written_after_the_audience(self):
        assert self.block().endswith("\nCalendar audience: a@x.org"
                                     "\nAlso invite: list@example.org, someone@example.com")

    def test_read_back(self):
        assert parse_footer_also_invite(self.block()) == ["list@example.org",
                                                          "someone@example.com"]
        assert parse_footer_also_invite(self.block(also=())) == []

    def test_kept_unless_given(self):
        current = {"customFooterText": self.block(), "includeCustomFooter": "true"}
        assert footer_updates(current, "7", "F", "https://g", "Community Meetings Admins") == {}
        updates = footer_updates(current, "7", "F", "https://g", "Community Meetings Admins",
                                 also_invite=[])
        assert parse_footer_also_invite(updates["customFooterText"]) == []
        assert describe_footer_updates(self.block(), updates) == "also invite"

    def test_parse_addresses(self):
        assert parse_addresses("a@x.org, B@y.com  c@z.net,a@x.org, not-an-address") == [
            "a@x.org", "b@y.com", "c@z.net"]


class TestReplaceFooterZoom:
    NEW_URL = "https://us06web.zoom.us/j/81234567890?pwd=NEW"

    def test_replaces_link_and_passcode_only(self):
        block = build_footer_block("1", "FID", "https://x.coop", "Foo",
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL, zoom_passcode="123456")
        footer = "Human text\n\n" + block
        assert replace_footer_zoom(footer, self.NEW_URL, "654321") == "Human text\n\n" + \
            build_footer_block("1", "FID", "https://x.coop", "Foo", calendar_id=CAL_ID,
                               zoom_url=self.NEW_URL, zoom_passcode="654321")

    def test_adds_passcode_line_when_missing(self):
        block = build_footer_block("1", "FID", "https://x.coop", "Foo", zoom_url=ZOOM_URL)
        new = replace_footer_zoom(block, self.NEW_URL, "654321")
        assert parse_footer_zoom_passcode(new) == "654321"
        assert parse_footer_zoom_url(new) == self.NEW_URL

    def test_no_zoom_link_raises(self):
        with pytest.raises(ValueError):
            replace_footer_zoom(build_footer_block("1", "FID", "https://x.coop", "Foo"),
                                ZOOM_URL, "1")


class TestGroupsDriveSyncCompatibility:
    """groups_drive_sync.gs reads the Drive link out of the footer with its
    own regexes; they must keep matching what build_footer_block writes."""

    @staticmethod
    def _gs_regexes():
        source = GROUPS_DRIVE_SYNC_GS.read_text()
        found = dict(re.findall(r"\[ITEM_TYPE_(\w+)\]:\s*/(.+)/,", source))
        assert set(found) == {"FOLDER", "SHARED_DRIVE"}
        # JS escapes "/" inside a regex literal; Python's re accepts "\/" too.
        return {name: re.compile(pattern) for name, pattern in found.items()}

    @pytest.mark.parametrize("item_type,gs_name,other", [
        (ITEM_TYPE_FOLDER, "FOLDER", "SHARED_DRIVE"),
        (ITEM_TYPE_SHARED_DRIVE, "SHARED_DRIVE", "FOLDER"),
    ])
    def test_gs_regexes_match_new_layout(self, item_type, gs_name, other):
        regexes = self._gs_regexes()
        block = build_footer_block("42", "ITEM_ID-x_1", "https://x.coop", "Foo Circle",
                                   item_type, CAL_ID, ZOOM_URL)
        m = regexes[gs_name].search(block)
        assert m and m.group(1) == "ITEM_ID-x_1"
        assert not regexes[other].search(block)


class TestComputeGroupFooterUpdates:
    def test_writes_shared_drive_block_when_absent(self):
        svc = _FakeSettingsService({"customFooterText": "", "includeCustomFooter": "false"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "DRIVEID", "https://x.coop", "Foo", ITEM_TYPE_SHARED_DRIVE
        )
        assert "\nShared Drive: https://drive.google.com/drive/folders/DRIVEID" \
            in updates["customFooterText"]
        assert updates["includeCustomFooter"] == "true"

    def test_no_update_when_already_correct(self):
        block = build_footer_block("9", "DRIVEID", "https://x.coop", "Foo", ITEM_TYPE_SHARED_DRIVE)
        svc = _FakeSettingsService({"customFooterText": block, "includeCustomFooter": "true"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "DRIVEID", "https://x.coop", "Foo", ITEM_TYPE_SHARED_DRIVE
        )
        assert updates == {}

    def test_switching_folder_to_shared_drive_updates_block(self):
        folder_block = build_footer_block("9", "OLD", "https://x.coop", "Foo", ITEM_TYPE_FOLDER)
        svc = _FakeSettingsService({"customFooterText": folder_block, "includeCustomFooter": "true"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "NEWDRIVE", "https://x.coop", "Foo", ITEM_TYPE_SHARED_DRIVE
        )
        assert "Shared Drive: https://drive.google.com/drive/folders/NEWDRIVE" \
            in updates["customFooterText"]
        assert "Google Docs folder:" not in updates["customFooterText"]

    def test_preserves_human_prefix(self):
        svc = _FakeSettingsService(
            {"customFooterText": "Contact us at hi@x.coop", "includeCustomFooter": "true"}
        )
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "FID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER
        )
        assert updates["customFooterText"].startswith("Contact us at hi@x.coop")
        assert FOOTER_MARKER in updates["customFooterText"]

    def test_old_layout_is_rewritten(self):
        old = (
            f"{FOOTER_MARKER}\n"
            "Foo Gather group: https://x.coop/groups/9\n"
            "Foo Google Docs folder: https://drive.google.com/drive/folders/FID"
        )
        svc = _FakeSettingsService({"customFooterText": old, "includeCustomFooter": "true"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "FID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER
        )
        assert updates["customFooterText"] == build_footer_block(
            "9", "FID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER)

    def test_keeps_existing_calendar_and_zoom_when_not_given(self):
        block = build_footer_block("9", "FID", "https://x.coop", "Old Name",
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL, zoom_passcode="123456")
        svc = _FakeSettingsService({"customFooterText": block, "includeCustomFooter": "true"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "FID", "https://x.coop", "New Name", ITEM_TYPE_FOLDER
        )
        assert updates["customFooterText"] == build_footer_block(
            "9", "FID", "https://x.coop", "New Name", calendar_id=CAL_ID, zoom_url=ZOOM_URL,
            zoom_passcode="123456")

    def test_no_update_when_calendar_and_zoom_unchanged(self):
        block = build_footer_block("9", "FID", "https://x.coop", "Foo",
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL)
        svc = _FakeSettingsService({"customFooterText": block, "includeCustomFooter": "true"})
        assert compute_group_footer_updates(
            svc, "grp@x.coop", "9", "FID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER
        ) == {}

    def test_given_calendar_and_zoom_replace_existing(self):
        block = build_footer_block("9", "FID", "https://x.coop", "Foo",
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL)
        svc = _FakeSettingsService({"customFooterText": block, "includeCustomFooter": "true"})
        new_zoom = "https://us06web.zoom.us/j/81234567890?pwd=NEW"
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "FID", "https://x.coop", "Foo", ITEM_TYPE_FOLDER,
            calendar_id="other@group.calendar.google.com", zoom_url=new_zoom,
        )
        footer = updates["customFooterText"]
        assert parse_footer_calendar_id(footer) == "other@group.calendar.google.com"
        assert parse_footer_zoom_url(footer) == new_zoom
        assert ZOOM_URL not in footer


def _old_layout(name="Foo", gather_id="9", item_id="FID"):
    return (
        f"{FOOTER_MARKER}\n"
        f"{name} Gather group: https://x.coop/groups/{gather_id}\n"
        f"{name} Google Docs folder: https://drive.google.com/drive/folders/{item_id}"
    )


class TestDescribeFooterUpdates:
    def _describe(self, current_footer, *args, include="true", **kwargs):
        current = {"customFooterText": current_footer, "includeCustomFooter": include}
        updates = footer_updates(current, *args, **kwargs)
        return describe_footer_updates(current_footer, updates)

    def test_old_layout_same_links(self):
        assert self._describe(_old_layout(), "9", "FID", "https://x.coop", "Foo") == \
            "new layout (links unchanged)"

    def test_drive_link_change(self):
        assert self._describe(_old_layout(), "9", "NEW", "https://x.coop", "Foo",
                              ITEM_TYPE_SHARED_DRIVE) == "Drive link -> shared_drive NEW"

    def test_gather_link_change(self):
        assert self._describe(_old_layout(), "10", "FID", "https://x.coop", "Foo") == \
            "Gather link -> https://x.coop/groups/10"

    def test_rename(self):
        block = build_footer_block("9", "FID", "https://x.coop", "Foo")
        assert self._describe(block, "9", "FID", "https://x.coop", "Bar") == \
            "group name -> 'Bar'"

    def test_calendar_and_zoom(self):
        block = build_footer_block("9", "FID", "https://x.coop", "Foo")
        assert self._describe(block, "9", "FID", "https://x.coop", "Foo",
                              calendar_id=CAL_ID, zoom_url=ZOOM_URL) == \
            "calendar link; Zoom link"

    def test_new_block_and_footer_turned_on(self):
        assert self._describe("Human text", "9", "FID", "https://x.coop", "Foo",
                              include="false") == "add link block; turn footer on"

    def test_nothing_to_change(self):
        block = build_footer_block("9", "FID", "https://x.coop", "Foo")
        assert self._describe(block, "9", "FID", "https://x.coop", "Foo") == ""


class _RecordingSettingsService:
    """Groups Settings stand-in that serves per-group settings and records
    update() bodies."""

    def __init__(self, settings_by_email):
        self._settings = settings_by_email
        self.updated = {}
        self._pending = None

    def groups(self):
        return self

    def get(self, groupUniqueId):
        self._pending = ("get", groupUniqueId, None)
        return self

    def update(self, groupUniqueId, body):
        self._pending = ("update", groupUniqueId, body)
        return self

    def execute(self):
        op, email, body = self._pending
        if op == "get":
            return self._settings[email]
        self.updated[email] = body
        return body


class TestEnsureGroupFooters:
    def _run(self, svc, dry_run, capsys):
        import update_groups as m
        m.ensure_group_footers(
            svc, "https://x.coop",
            {"9": {"name": "Foo", "kind": "circle"}},
            {"9": "FID"}, {"9": ITEM_TYPE_FOLDER}, {"9": "foo@x.coop"}, dry_run,
        )
        return capsys.readouterr().out

    def test_dry_run_reports_layout_only_change(self, capsys):
        svc = _RecordingSettingsService(
            {"foo@x.coop": {"customFooterText": _old_layout(), "includeCustomFooter": "true"}})
        out = self._run(svc, True, capsys)
        assert "would update footer: new layout (links unchanged)" in out
        assert svc.updated == {}

    def test_applies_update_and_keeps_calendar_and_zoom(self, capsys):
        block = build_footer_block("9", "FID", "https://x.coop", "Old",
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL)
        svc = _RecordingSettingsService(
            {"foo@x.coop": {"customFooterText": block, "includeCustomFooter": "true"}})
        out = self._run(svc, False, capsys)
        assert "group name -> 'Foo'" in out
        assert svc.updated["foo@x.coop"]["customFooterText"] == build_footer_block(
            "9", "FID", "https://x.coop", "Foo", calendar_id=CAL_ID, zoom_url=ZOOM_URL)

    def test_silent_when_up_to_date(self, capsys):
        block = build_footer_block("9", "FID", "https://x.coop", "Foo")
        svc = _RecordingSettingsService(
            {"foo@x.coop": {"customFooterText": block, "includeCustomFooter": "true"}})
        assert self._run(svc, False, capsys) == ""
        assert svc.updated == {}


def _entry(group_id, folder_name, item_type, group_name="Grp",
           item_id=None, google_file_id=None):
    return dict(
        folder_name=folder_name, group_id=group_id, group_name=group_name,
        item_type=item_type, item_id=item_id, google_file_id=google_file_id,
    )


def _no_footer(group_id):
    return None


class TestChooseMembershipLinks:
    def test_one_item_per_group_is_kept(self):
        entries = [
            _entry("1", "Alpha Circle", ITEM_TYPE_FOLDER),
            _entry("2", "Beta Drive", ITEM_TYPE_SHARED_DRIVE),
        ]
        called = []
        chosen, extras, ambiguous = choose_membership_links(
            entries, lambda gid: called.append(gid), {})
        assert chosen == entries
        assert extras == {} and ambiguous == {}
        assert called == []  # footers are only read for groups with several links

    def test_empty(self):
        assert choose_membership_links([], _no_footer, {}) == ([], {}, {})

    def test_only_shared_drive_is_chosen_over_folders(self):
        # The Full Community group: Viewer on the Shared Drive, Contributor
        # on one folder in it.
        drive = _entry("1", "Berkeley Moshav", ITEM_TYPE_SHARED_DRIVE, "Full Community")
        folder = _entry("1", "Community Projects", ITEM_TYPE_FOLDER, "Full Community")
        other = _entry("2", "Beta Circle", ITEM_TYPE_FOLDER)
        chosen, extras, ambiguous = choose_membership_links(
            [folder, other, drive], _no_footer, {})
        assert chosen == [other, drive]
        assert extras == {"1": [folder]}
        assert ambiguous == {}

    def test_footer_item_wins_by_row_link(self):
        drive = _entry("1", "Drive", ITEM_TYPE_SHARED_DRIVE, google_file_id="D1")
        folder = _entry("1", "Folder", ITEM_TYPE_FOLDER, google_file_id="F1")
        chosen, extras, _ = choose_membership_links(
            [drive, folder], lambda gid: "F1", {})
        assert chosen == [folder]
        assert extras == {"1": [drive]}

    def test_footer_item_wins_by_item_map(self):
        a = _entry("1", "A", ITEM_TYPE_FOLDER, item_id="11")
        b = _entry("1", "B", ITEM_TYPE_FOLDER, item_id="12")
        chosen, extras, ambiguous = choose_membership_links(
            [a, b], lambda gid: "FB", {"11": "FA", "12": "FB"})
        assert chosen == [b]
        assert extras == {"1": [a]}
        assert ambiguous == {}

    def test_footer_linking_neither_falls_back_to_shared_drive(self):
        drive = _entry("1", "Drive", ITEM_TYPE_SHARED_DRIVE, google_file_id="D1")
        folder = _entry("1", "Folder", ITEM_TYPE_FOLDER, google_file_id="F1")
        chosen, _, _ = choose_membership_links([folder, drive], lambda gid: "X", {})
        assert chosen == [drive]

    def test_ambiguous_without_footer_or_single_shared_drive(self):
        entries = [
            _entry("1", "A1", ITEM_TYPE_FOLDER, "GroupA"),
            _entry("1", "A2", ITEM_TYPE_FOLDER, "GroupA"),
            _entry("2", "B1", ITEM_TYPE_SHARED_DRIVE, "GroupB"),
            _entry("2", "B2", ITEM_TYPE_SHARED_DRIVE, "GroupB"),
            _entry("3", "C1", ITEM_TYPE_FOLDER, "GroupC"),
        ]
        chosen, extras, ambiguous = choose_membership_links(entries, _no_footer, {})
        assert chosen == [entries[4]]
        assert extras == {}
        assert ambiguous == {"1": entries[:2], "2": entries[2:4]}
        msg = ambiguous_links_message(ambiguous)
        assert "GroupA" in msg and "GroupB" in msg and "GroupC" not in msg
        assert "group_id=1" in msg and "'A2' (folder)" in msg
