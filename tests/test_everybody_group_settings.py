"""
Tests for the Availability = Everybody exemptions in
update_groups.py: such groups keep their Google
Group whoCanModerateContent setting and their members' roles untouched.

All names, emails, and IDs are fictional.
"""

import update_groups as m


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


class TestNewGroupNotYetVisible:
    """A group created moments earlier can be unknown to the Groups Settings
    API for a while; ensure_group_settings waits for it."""

    class _NotFound(Exception):
        def __init__(self):
            super().__init__("<HttpError 404 ... Unable to lookup group>")
            self.resp = type("R", (), {"status": 404})()

    class _Settings:
        def __init__(self, misses):
            self.misses = misses
            self.updated = None
            self._op = None

        def groups(self):
            return self

        def get(self, groupUniqueId):
            self._op = "get"
            return self

        def update(self, groupUniqueId, body):
            self._op = "update"
            self.updated = body
            return self

        def execute(self):
            if self._op == "get":
                if self.misses:
                    self.misses -= 1
                    raise TestNewGroupNotYetVisible._NotFound()
                return {"whoCanPostMessage": "ALL_MEMBERS_CAN_POST"}
            return self.updated

    def test_retries_until_the_group_appears(self, monkeypatch):
        import util.google_group_utils as g
        monkeypatch.setattr(g.time, "sleep", lambda s: None)
        settings = self._Settings(misses=2)
        assert g.ensure_group_settings(settings, "new@x.org",
                                       {"whoCanPostMessage": "ANYONE_CAN_POST"}) == \
            {"whoCanPostMessage": "ANYONE_CAN_POST"}
        assert settings.updated == {"whoCanPostMessage": "ANYONE_CAN_POST"}

    def test_gives_up_after_max_wait(self, monkeypatch):
        import pytest
        import util.google_group_utils as g
        monkeypatch.setattr(g.time, "sleep", lambda s: None)
        with pytest.raises(self._NotFound):
            g.ensure_group_settings(self._Settings(misses=99), "new@x.org",
                                    {"whoCanPostMessage": "ANYONE_CAN_POST"}, max_wait=0)


class TestAlsoInviteAddresses:
    def test_parsing(self):
        from util.google_group_utils import also_invite_addresses
        assert also_invite_addresses(["planning, Pat@Example.com", "x@y.org pat@example.com"]) \
            == ["planning@berkeleymoshav.org", "pat@example.com", "x@y.org"]
        assert also_invite_addresses(["a@x.org", "none"]) == []

    def test_bad_address(self):
        import pytest
        from util.google_group_utils import also_invite_addresses
        with pytest.raises(ValueError):
            also_invite_addresses(["pat@localhost"])
