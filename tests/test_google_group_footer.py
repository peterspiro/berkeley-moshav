"""
Unit tests for util.google_group_footer and the single-link validation in
util.gdrive_config — the folder/shared-drive distinction the Groups–Drive
sync depends on.

All names, emails, and IDs are fictional.
"""

import re
from pathlib import Path

import pytest

from util.gdrive_config import check_single_link_per_group
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
    parse_footer_calendar_id,
    parse_footer_folder_id,
    parse_footer_link,
    parse_footer_zoom_url,
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
                                   calendar_id=CAL_ID, zoom_url=ZOOM_URL)
        svc = _FakeSettingsService({"customFooterText": block, "includeCustomFooter": "true"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "FID", "https://x.coop", "New Name", ITEM_TYPE_FOLDER
        )
        assert updates["customFooterText"] == build_footer_block(
            "9", "FID", "https://x.coop", "New Name", calendar_id=CAL_ID, zoom_url=ZOOM_URL)

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
        import update_groups_in_google_and_hierarchy as m
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


def _entry(group_id, folder_name, item_type, group_name="Grp"):
    return dict(
        folder_name=folder_name, group_id=group_id, group_name=group_name,
        item_type=item_type, item_id=None, google_file_id=None,
    )


class TestCheckSingleLinkPerGroup:
    def test_ok_when_each_group_has_one_item(self):
        entries = [
            _entry("1", "Alpha Circle", ITEM_TYPE_FOLDER),
            _entry("2", "Beta Drive", ITEM_TYPE_SHARED_DRIVE),
        ]
        check_single_link_per_group(entries)  # no raise

    def test_empty_is_ok(self):
        check_single_link_per_group([])

    def test_raises_when_group_linked_to_two_items(self):
        entries = [
            _entry("1", "Alpha Circle", ITEM_TYPE_FOLDER, "Alpha"),
            _entry("1", "Alpha Drive", ITEM_TYPE_SHARED_DRIVE, "Alpha"),
        ]
        with pytest.raises(ValueError) as exc:
            check_single_link_per_group(entries)
        assert "Alpha" in str(exc.value)
        assert "group_id=1" in str(exc.value)

    def test_raises_lists_all_offenders(self):
        entries = [
            _entry("1", "A1", ITEM_TYPE_FOLDER, "GroupA"),
            _entry("1", "A2", ITEM_TYPE_FOLDER, "GroupA"),
            _entry("2", "B1", ITEM_TYPE_FOLDER, "GroupB"),
            _entry("2", "B2", ITEM_TYPE_SHARED_DRIVE, "GroupB"),
            _entry("3", "C1", ITEM_TYPE_FOLDER, "GroupC"),  # fine, only one
        ]
        with pytest.raises(ValueError) as exc:
            check_single_link_per_group(entries)
        msg = str(exc.value)
        assert "GroupA" in msg and "GroupB" in msg
        assert "GroupC" not in msg
