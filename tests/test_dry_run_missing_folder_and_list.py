"""
A dry run must report a missing mailing list even for a group that also
has no Drive folder (ensure_email_lists() only visits foldered groups, and
a dry run links none).

All names and IDs are fictional.
"""

import update_groups_in_google_and_hierarchy as m


def _run(capsys, list_name):
    group_info = {"7": {"name": "Care Circle", "kind": "circle", "availability": "members",
                        "url": "/groups/7", "list_name": list_name, "list_domain": None}}
    m.ensure_group_folders(
        page=None, base_url="", drive_service=None, drive_id="D",
        group_info=group_info, linked_group_ids=set(),
        documents_url_by_group_id={}, google_file_id_by_group_id={},
        item_type_by_group_id={}, get_drive_folders=lambda: [],
        folder_owner={}, folder_name_by_group_id={}, dry_run=True,
    )
    return capsys.readouterr().out


def test_missing_folder_and_list_both_reported(capsys):
    out = _run(capsys, list_name=None)
    assert "has no associated Drive folder" in out
    assert "also has no mailing list configured" in out


def test_missing_folder_only_when_list_exists(capsys):
    out = _run(capsys, list_name="care")
    assert "has no associated Drive folder" in out
    assert "mailing list" not in out
