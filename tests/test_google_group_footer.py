"""
Unit tests for util.google_group_footer and the single-link validation in
util.gdrive_config — the folder/shared-drive distinction the Groups–Drive
sync depends on.

All names, emails, and IDs are fictional.
"""

import pytest

from util.gdrive_config import check_single_link_per_group
from util.google_group_footer import (
    FOOTER_MARKER,
    ITEM_TYPE_FOLDER,
    ITEM_TYPE_SHARED_DRIVE,
    build_footer_block,
    compute_group_footer_updates,
    parse_footer_folder_id,
    parse_footer_link,
)


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
        assert FOOTER_MARKER in block
        assert "Foo Circle Gather group: https://x.coop/groups/42" in block
        assert "Foo Circle Google Docs folder: " \
               "https://drive.google.com/drive/folders/FOLDERID" in block
        assert "Shared Drive:" not in block

    def test_shared_drive_label_and_url(self):
        block = build_footer_block("7", "DRIVEID", "https://x.coop", "Bar Working Group",
                                   ITEM_TYPE_SHARED_DRIVE)
        assert "Bar Working Group Shared Drive: " \
               "https://drive.google.com/drive/folders/DRIVEID" in block
        assert "Google Docs folder:" not in block

    def test_default_item_type_is_folder(self):
        assert build_footer_block("1", "ID", "https://x.coop", "Baz") == \
            build_footer_block("1", "ID", "https://x.coop", "Baz", ITEM_TYPE_FOLDER)

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

    def test_parse_footer_folder_id_returns_id_for_both(self):
        folder = build_footer_block("1", "FID", "https://x.coop", "F", ITEM_TYPE_FOLDER)
        drive = build_footer_block("1", "DID", "https://x.coop", "F", ITEM_TYPE_SHARED_DRIVE)
        assert parse_footer_folder_id(folder) == "FID"
        assert parse_footer_folder_id(drive) == "DID"
        assert parse_footer_folder_id("no link here") is None


class TestComputeGroupFooterUpdates:
    def test_writes_shared_drive_block_when_absent(self):
        svc = _FakeSettingsService({"customFooterText": "", "includeCustomFooter": "false"})
        updates = compute_group_footer_updates(
            svc, "grp@x.coop", "9", "DRIVEID", "https://x.coop", "Foo", ITEM_TYPE_SHARED_DRIVE
        )
        assert "Foo Shared Drive: https://drive.google.com/drive/folders/DRIVEID" \
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
