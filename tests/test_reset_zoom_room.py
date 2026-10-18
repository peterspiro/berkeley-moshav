"""
Tests for reset_zoom_room.py: finding the group, and resetting its room's
passcode and footer link.

All names, IDs, and addresses are fictional.
"""

import pytest

import reset_zoom_room as r
from tests.fakes_meeting_rooms import FakeZoom, room
from util.google_group_footer import (
    build_footer_block,
    parse_footer_zoom_passcode,
    parse_footer_zoom_url,
)

GROUPS = [
    {"email": "tech-circle@x.org", "name": "Technology Circle"},
    {"email": "tea@x.org", "name": "Tea Club"},
    {"email": "garden@x.org", "name": "Garden Circle"},
]


class TestFindGroup:
    def test_exact_address(self):
        assert r.find_group(GROUPS, "TEA@x.org")["name"] == "Tea Club"

    def test_local_part(self):
        assert r.find_group(GROUPS, "tea")["name"] == "Tea Club"

    def test_unique_name_prefix(self):
        assert r.find_group(GROUPS, "techn")["email"] == "tech-circle@x.org"

    def test_ambiguous(self):
        with pytest.raises(ValueError, match="several"):
            r.find_group(GROUPS, "te")

    def test_none(self):
        with pytest.raises(ValueError, match="no Google Group"):
            r.find_group(GROUPS, "zzz")


class _Settings:
    def __init__(self, footer):
        self.footer = footer
        self.updated = None
        self._body = None

    def groups(self):
        return self

    def get(self, groupUniqueId):
        self._body = None
        return self

    def update(self, groupUniqueId, body):
        self._body = body
        return self

    def execute(self):
        if self._body is None:
            return {"customFooterText": self.footer}
        self.updated = self._body
        return self._body


class _ResettingZoom(FakeZoom):
    def set_passcode(self, meeting_id, passcode):
        self.writes.append(("passcode", meeting_id, passcode))
        self.meetings[meeting_id]["join_url"] = f"https://zoom.us/j/{meeting_id}?pwd=NEW"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(r, "log", lambda *a, **k: None)


def _footer(url="https://zoom.us/j/7?pwd=p"):
    return "Notes\n\n" + build_footer_block("1", "F", "https://g", "Technology Circle",
                                            zoom_url=url)


def test_resets_passcode_and_footer(capsys):
    settings = _Settings(_footer())
    zoom = _ResettingZoom({7: room(7, "Technology Circle Meeting Room")})
    r.reset_room_passcode(settings, zoom, GROUPS[0], dry_run=False)
    _, _, passcode = zoom.writes[0]
    assert zoom.writes[0][:2] == ("passcode", "7") and passcode.isdigit()
    new_footer = settings.updated["customFooterText"]
    assert parse_footer_zoom_url(new_footer) == "https://zoom.us/j/7?pwd=NEW"
    assert parse_footer_zoom_passcode(new_footer) == passcode
    assert new_footer.startswith("Notes\n\n")
    assert "pwd=NEW" in capsys.readouterr().out


def test_dry_run_changes_nothing():
    settings = _Settings(_footer())
    zoom = _ResettingZoom({7: room(7, "Technology Circle Meeting Room")})
    r.reset_room_passcode(settings, zoom, GROUPS[0], dry_run=True)
    assert zoom.writes == [] and settings.updated is None


def test_no_room_in_footer_exits():
    settings = _Settings(build_footer_block("1", "F", "https://g", "Technology Circle"))
    with pytest.raises(SystemExit):
        r.reset_room_passcode(settings, _ResettingZoom(), GROUPS[0], dry_run=False)


def test_expired_room_exits():
    with pytest.raises(SystemExit, match="no longer exists"):
        r.reset_room_passcode(_Settings(_footer()), _ResettingZoom(), GROUPS[0], dry_run=False)
