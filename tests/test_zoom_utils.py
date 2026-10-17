"""
Tests for util.zoom_utils: the standard room settings, the requests sent to
Zoom's API, and generated passcodes and host keys. No network: a fake
session records each request and returns canned responses.

All IDs and addresses are fictional.
"""

import pytest

from util.zoom_utils import (
    API_BASE,
    MEETING_TYPE_NO_FIXED_TIME,
    ROOM_SETTINGS,
    ROOMS_USER,
    TOKEN_URL,
    ZoomClient,
    ZoomError,
    _is_weak_host_key,
    meeting_id_from_url,
    new_host_key,
    new_passcode,
    room_settings_differences,
    room_topic,
)


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = str(self._data)

    def json(self):
        return self._data


class FakeSession:
    """Answers the token request, then each API request from `responses`
    ({(method, path): _Resp or list of _Resp}), recording every call."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.token_requests = 0

    def post(self, url, params=None, auth=None, timeout=None):
        assert url == TOKEN_URL
        self.token_requests += 1
        assert params == {"grant_type": "account_credentials", "account_id": "ACCT"}
        assert auth == ("CID", "SECRET")
        return _Resp(200, {"access_token": "TOKEN", "expires_in": 3600})

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        assert url.startswith(API_BASE)
        assert headers == {"Authorization": "Bearer TOKEN"}
        path = url[len(API_BASE):]
        self.calls.append((method, path, kwargs))
        resp = self.responses.get((method, path), _Resp(204))
        if isinstance(resp, list):
            return resp.pop(0)
        return resp


def _client(responses=None):
    session = FakeSession(responses)
    return ZoomClient("ACCT", "CID", "SECRET", session=session), session


class TestHelpers:
    def test_room_topic(self):
        assert room_topic("Technology Circle") == "Technology Circle Meeting Room"

    def test_meeting_id_from_url(self):
        assert meeting_id_from_url("https://us06web.zoom.us/j/81234567890?pwd=abc") == "81234567890"
        assert meeting_id_from_url(None) is None
        assert meeting_id_from_url("https://example.org") is None

    def test_settings_differences_empty_when_standard(self):
        meeting = {"topic": "Foo Meeting Room", "settings": dict(ROOM_SETTINGS, host_video=True)}
        assert room_settings_differences(meeting, "Foo Meeting Room") == {}

    def test_settings_differences_lists_only_changes(self):
        meeting = {"topic": "Old Meeting Room",
                   "settings": dict(ROOM_SETTINGS, waiting_room=True, auto_recording="cloud")}
        assert room_settings_differences(meeting, "Foo Meeting Room") == {
            "topic": "Foo Meeting Room",
            "settings": {"waiting_room": False, "auto_recording": "none"},
        }

    def test_room_settings_allow_meetings_without_a_host(self):
        assert ROOM_SETTINGS["join_before_host"] is True
        assert ROOM_SETTINGS["jbh_time"] == 0
        assert ROOM_SETTINGS["waiting_room"] is False
        assert ROOM_SETTINGS["auto_recording"] == "none"

    def test_passcode(self):
        p = new_passcode()
        assert len(p) == 10 and p.isalnum()
        assert new_passcode() != p

    @pytest.mark.parametrize("key", ["111111", "123456", "654321", "000000"])
    def test_weak_host_keys(self, key):
        assert _is_weak_host_key(key)

    @pytest.mark.parametrize("key", ["135792", "112233", "908172"])
    def test_ok_host_keys(self, key):
        assert not _is_weak_host_key(key)

    def test_new_host_key(self):
        for _ in range(50):
            k = new_host_key()
            assert len(k) == 6 and k.isdigit() and not _is_weak_host_key(k)


class TestZoomClient:
    def test_get_meeting_missing_is_none(self):
        zoom, _ = _client({("GET", "/meetings/1"): _Resp(404, {"code": 3001})})
        assert zoom.get_meeting("1") is None

    def test_get_meeting(self):
        zoom, session = _client({("GET", "/meetings/1"): _Resp(200, {"id": 1})})
        assert zoom.get_meeting("1") == {"id": 1}
        zoom.get_meeting("1")
        assert session.token_requests == 1  # token reused

    def test_errors_raise(self):
        zoom, _ = _client({("GET", "/meetings/1"): _Resp(400, {"message": "bad"})})
        with pytest.raises(ZoomError):
            zoom.get_meeting("1")

    def test_create_room(self):
        zoom, session = _client(
            {("POST", f"/users/{ROOMS_USER}/meetings"): _Resp(201, {"id": 5})})
        assert zoom.create_room("Foo Meeting Room") == {"id": 5}
        method, path, kwargs = session.calls[0]
        assert kwargs["json"] == {
            "topic": "Foo Meeting Room",
            "type": MEETING_TYPE_NO_FIXED_TIME,
            "settings": ROOM_SETTINGS,
        }

    def test_list_rooms_pages_and_keeps_only_type_3(self):
        path = f"/users/{ROOMS_USER}/meetings"
        zoom, session = _client({("GET", path): [
            _Resp(200, {"meetings": [{"id": 1, "type": 3}, {"id": 2, "type": 2}],
                        "next_page_token": "P2"}),
            _Resp(200, {"meetings": [{"id": 3, "type": 3}], "next_page_token": ""}),
        ]})
        assert [r["id"] for r in zoom.list_rooms()] == [1, 3]
        assert session.calls[1][2]["params"]["next_page_token"] == "P2"

    def test_last_used(self):
        zoom, _ = _client({("GET", "/past_meetings/1/instances"): _Resp(200, {"meetings": [
            {"start_time": "2026-01-02T10:00:00Z"}, {"start_time": "2026-03-01T10:00:00Z"}]})})
        assert zoom.last_used("1") == "2026-03-01T10:00:00Z"

    def test_last_used_never(self):
        zoom, _ = _client({("GET", "/past_meetings/1/instances"): _Resp(200, {"meetings": []})})
        assert zoom.last_used("1") is None

    def test_set_passcode_and_host_key(self):
        zoom, session = _client()
        zoom.set_passcode("1", "abc123")
        zoom.set_host_key("135792")
        assert session.calls == [
            ("PATCH", "/meetings/1", {"json": {"password": "abc123"}}),
            ("PATCH", f"/users/{ROOMS_USER}", {"json": {"host_key": "135792"}}),
        ]
