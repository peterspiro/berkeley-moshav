"""
Tests for the Availability = Everybody exemptions in
update_groups_in_google_and_hierarchy.py: such groups keep their Google
Group whoCanModerateContent setting and their members' roles untouched.

All names, emails, and IDs are fictional.
"""

import update_groups_in_google_and_hierarchy as m


def _info(name, availability, kind="circle"):
    return {"name": name, "kind": kind, "availability": availability,
            "url": "/groups/x", "list_name": None, "list_domain": None}


class TestIsEverybody:
    def test_everybody(self):
        assert m._is_everybody({"availability": "everybody"}) is True

    def test_case_insensitive_already_normalized(self):
        # fetch_group_info stores availability lowercased/stripped
        assert m._is_everybody({"availability": "closed"}) is False

    def test_missing_key(self):
        assert m._is_everybody({}) is False


class TestEnforceGroupSettingsEverybody:
    def test_everybody_group_skips_moderation_setting(self, monkeypatch):
        applied = {}

        def fake_ensure(settings_service, email, settings):
            applied[email] = settings
            return {}

        monkeypatch.setattr(m, "ensure_group_settings", fake_ensure)

        group_info = {
            "1": _info("Everyone", "everybody"),
            "2": _info("Finance", "closed"),
        }
        email_by_group_id = {"1": "everyone@x.coop", "2": "finance@x.coop"}

        m.enforce_group_settings(None, group_info, email_by_group_id, dry_run=False)

        # Everybody group: only Conversation history, no whoCanModerateContent.
        assert "whoCanModerateContent" not in applied["everyone@x.coop"]
        assert applied["everyone@x.coop"] == m.CONVERSATION_HISTORY_SETTINGS
        # Closed group: full enforced settings incl. moderation.
        assert "whoCanModerateContent" in applied["finance@x.coop"]
        assert applied["finance@x.coop"] == m.ENFORCED_GROUP_SETTINGS


class TestDemoteManagersEverybody:
    def test_everybody_group_is_not_visited(self, monkeypatch):
        visited = []

        def fake_list_members(dir_service, gemail):
            visited.append(gemail)
            return [{"email": "mgr@x.coop", "role": "MANAGER"}]

        demoted = []

        def fake_set_role(dir_service, gemail, email, role):
            demoted.append((gemail, email, role))

        monkeypatch.setattr(m, "list_group_members", fake_list_members)
        monkeypatch.setattr(m, "set_member_role", fake_set_role)

        group_info = {
            "1": _info("Everyone", "everybody"),
            "2": _info("Finance", "closed"),
        }
        email_by_group_id = {"1": "everyone@x.coop", "2": "finance@x.coop"}

        m.demote_managers_to_members(None, group_info, email_by_group_id, dry_run=False)

        # Everybody group is never even inspected; closed group is demoted.
        assert "everyone@x.coop" not in visited
        assert "finance@x.coop" in visited
        assert ("finance@x.coop", "mgr@x.coop", "MEMBER") in demoted
        assert all(gemail != "everyone@x.coop" for gemail, _, _ in demoted)
