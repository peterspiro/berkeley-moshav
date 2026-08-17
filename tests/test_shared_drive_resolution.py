"""
Tests for resolving a Gather group's linked shared drive to its driveId by
name (Drive API) when the /gdrive/config row exposes no ID and no footer
carries it yet — so the footer step becomes eligible and a dry run reports it.

All names, emails, and IDs are fictional.
"""

import update_groups_in_google_and_hierarchy as m
from util.gdrive_config import list_shared_drives
from util.google_group_footer import ITEM_TYPE_FOLDER, ITEM_TYPE_SHARED_DRIVE


# ── list_shared_drives ────────────────────────────────────────────────────────

class _Exec:
    def __init__(self, page):
        self._page = page

    def execute(self):
        return self._page


class _FakeDrivesResource:
    def __init__(self, service):
        self._service = service

    def list(self, **kwargs):
        # list_shared_drives calls drive_service.drives().list() fresh each
        # page, so the page cursor must live on the service, not the resource
        # (otherwise it would reset every iteration and loop forever).
        page = self._service._pages[self._service._calls]
        self._service._calls += 1
        return _Exec(page)


class _FakeDriveService:
    def __init__(self, pages):
        self._pages = pages
        self._calls = 0

    def drives(self):
        return _FakeDrivesResource(self)


class TestListSharedDrives:
    def test_paginates_and_maps_id_name(self):
        pages = [
            {"drives": [{"id": "A", "name": "Alpha"}], "nextPageToken": "t"},
            {"drives": [{"id": "B", "name": "Beta"}]},
        ]
        result = list_shared_drives(_FakeDriveService(pages))
        assert result == [{"id": "A", "name": "Alpha"}, {"id": "B", "name": "Beta"}]

    def test_empty(self):
        assert list_shared_drives(_FakeDriveService([{"drives": []}])) == []


# ── fetch_documents_url_by_group_id shared-drive resolution ────────────────────

def _entry(group_id, folder_name, item_type, google_file_id=None, item_id=None):
    return dict(folder_name=folder_name, group_id=group_id, group_name=folder_name,
                item_type=item_type, item_id=item_id, google_file_id=google_file_id)


class TestFetchDocumentsSharedDrive:
    def test_shared_drive_resolved_by_name(self):
        # Row exposes no ID, no footer (email absent) -> must resolve by name.
        config_entries = [_entry("1", "Community Drive", ITEM_TYPE_SHARED_DRIVE)]
        shared_drives = [{"id": "DRIVEXYZ", "name": "Community Drive"}]

        docs, linked, gfids, itypes = m.fetch_documents_url_by_group_id(
            page=None, base_url="", drive_id="D",
            config_entries=config_entries, drive_folders=[],
            shared_drives=shared_drives, settings_service=None,
            email_by_group_id={},
        )

        assert gfids["1"] == "DRIVEXYZ"
        assert itypes["1"] == ITEM_TYPE_SHARED_DRIVE
        assert docs["1"] == "/gdrive/item/DRIVEXYZ"
        assert linked == {"1"}

    def test_shared_drive_row_id_wins_without_name_lookup(self):
        # If the row already exposes the driveId, it's used directly.
        config_entries = [_entry("1", "Community Drive", ITEM_TYPE_SHARED_DRIVE,
                                 google_file_id="ROWID")]
        docs, linked, gfids, itypes = m.fetch_documents_url_by_group_id(
            page=None, base_url="", drive_id="D",
            config_entries=config_entries, drive_folders=[],
            shared_drives=[{"id": "OTHER", "name": "Community Drive"}],
            settings_service=None, email_by_group_id={},
        )
        assert gfids["1"] == "ROWID"

    def test_folder_still_resolved_by_name_against_tree(self):
        # Regression: a folder row resolves against the walked drive tree,
        # not the shared-drives list.
        config_entries = [_entry("2", "Alpha Circle", ITEM_TYPE_FOLDER)]
        drive_folders = [{"id": "FID", "name": "Alpha Circle", "path": ["Alpha Circle"]}]
        docs, linked, gfids, itypes = m.fetch_documents_url_by_group_id(
            page=None, base_url="", drive_id="D",
            config_entries=config_entries, drive_folders=drive_folders,
            shared_drives=[], settings_service=None, email_by_group_id={},
        )
        assert gfids["2"] == "FID"
        assert itypes["2"] == ITEM_TYPE_FOLDER
