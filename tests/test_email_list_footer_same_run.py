"""
Regression test: a group that gets its mailing list assigned in step 0b
(by picking an existing Google Group) must have a usable email address in
the same run, so step 0d writes its footer without needing a second run.

Before the fix, the group's list_domain still held whatever Gather's
domain dropdown showed while no list existed (e.g. a placeholder), so
gather_group_email() rejected the new address and the footer was skipped.

All names, emails, and IDs are fictional.
"""

import update_groups_in_google_and_hierarchy as m


def test_selected_existing_group_has_email_in_same_run(monkeypatch):
    group_info = {
        "7": {"name": "Care Circle", "kind": "circle", "availability": "members",
              "url": "/groups/7", "list_name": None, "list_domain": "Select a domain"},
    }
    checked: set[str] = set()
    assert m.build_email_by_group_id(group_info, checked) == {}

    chosen = {"email": f"care@{m.DOMAIN}", "name": "Care Circle"}
    monkeypatch.setattr(m, "list_all_groups", lambda svc: [chosen])
    monkeypatch.setattr(m, "find_matching_folders", lambda name, groups: [chosen])
    monkeypatch.setattr(m, "prompt_group_choice_for_email_list",
                        lambda matches: ("select", matches[0]))
    monkeypatch.setattr(m, "set_gather_group_email_list", lambda *a, **k: None)

    m.ensure_email_lists(
        page=None, base_url="", dir_service=None, settings_service=None,
        group_info=group_info, linked_group_ids={"7"},
        folder_name_by_group_id={"7": "Care Circle"}, dry_run=False,
    )

    assert m.build_email_by_group_id(group_info, checked) == {"7": f"care@{m.DOMAIN}"}
