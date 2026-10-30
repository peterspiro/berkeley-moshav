"""
Zoom API access for the groups' permanent meeting rooms.

Uses a Server-to-Server OAuth app in the community's Zoom account (its
credentials live in the repo's zoom_credentials file — see
util/credentials.py and README.md). Every
group room is a recurring meeting with no fixed time (type 3), so its link
never changes and members only ever click it. New rooms are made under
ROOMS_USER; a room can be moved (transferred in Zoom, keeping its link) to
another of ROOM_USERS, to keep its meetings from clashing with others'.

Room settings (ROOM_SETTINGS) let a meeting run with no host present:
participants can join before the host at any time, there's no waiting room
(a host key can't be used with one), recording is never automatic, and the
passcode is embedded in the join link. Passcodes are digits only
(new_passcode()), since members joining by meeting ID type them in, and
sometimes read them out over the phone.
"""

import re
import secrets
import string
import time

import requests

API_BASE = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"

# The licensed Zoom user that new group rooms are made under, and every
# licensed user that may own one (a Zoom user can run one meeting at a time).
ROOMS_USER = "circle-zoom-hosts@berkeleymoshav.org"
ROOM_USERS = (ROOMS_USER, "berkeley@urbanmoshav.org")

ROOM_TOPIC_SUFFIX = " Meeting Room"
MEETING_TYPE_NO_FIXED_TIME = 3

ROOM_SETTINGS = {
    "join_before_host": True,
    "jbh_time": 0,                 # participants may join any time before the start
    "waiting_room": False,
    "auto_recording": "none",
    "meeting_authentication": False,
    "approval_type": 2,            # no registration
}

# Digits only, to be easy to type. Zoom allows up to 10 characters.
PASSCODE_LENGTH = 6


_MEETING_ID_RE = re.compile(r"/j/(\d+)")


def room_topic(group_name: str) -> str:
    return group_name + ROOM_TOPIC_SUFFIX


def meeting_id_from_url(join_url: str | None) -> str | None:
    """The numeric meeting ID in a zoom.us/j/<id> link, or None."""
    m = _MEETING_ID_RE.search(join_url or "")
    return m.group(1) if m else None


def room_settings_differences(meeting: dict, topic: str) -> dict:
    """Return the PATCH body needed to bring meeting (a GET /meetings
    result) to topic and ROOM_SETTINGS — {} if it already matches."""
    body = {}
    if meeting.get("topic") != topic:
        body["topic"] = topic
    current = meeting.get("settings") or {}
    settings = {k: v for k, v in ROOM_SETTINGS.items() if current.get(k) != v}
    if settings:
        body["settings"] = settings
    return body


def new_passcode() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(PASSCODE_LENGTH))


def is_numeric_passcode(passcode: str | None) -> bool:
    """True if the passcode is digits only, as new_passcode() makes them."""
    return bool(passcode) and passcode.isdigit()


class ZoomError(RuntimeError):
    pass


class ZoomClient:
    """Minimal Zoom REST client. The access token is fetched on first use
    and refreshed shortly before it expires (they last an hour)."""

    def __init__(self, account_id: str, client_id: str, client_secret: str,
                 session: requests.Session | None = None):
        self._account_id = account_id
        self._auth = (client_id, client_secret)
        self._session = session or requests.Session()
        self._token = None
        self._token_expires = 0.0

    def _access_token(self) -> str:
        if self._token is None or time.time() > self._token_expires - 60:
            resp = self._session.post(
                TOKEN_URL,
                params={"grant_type": "account_credentials", "account_id": self._account_id},
                auth=self._auth,
                timeout=30,
            )
            if resp.status_code != 200:
                raise ZoomError(f"Zoom sign-in failed ({resp.status_code}): {resp.text}")
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = time.time() + data.get("expires_in", 3600)
        return self._token

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        resp = self._session.request(
            method, API_BASE + path,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=30, **kwargs,
        )
        if resp.status_code >= 400 and resp.status_code != 404:
            raise ZoomError(f"Zoom {method} {path} failed ({resp.status_code}): {resp.text}")
        return resp

    def get_meeting(self, meeting_id: str) -> dict | None:
        """The meeting, or None if Zoom has no such meeting (never created,
        deleted, or expired after a year unused)."""
        resp = self._request("GET", f"/meetings/{meeting_id}")
        return None if resp.status_code == 404 else resp.json()

    def create_room(self, topic: str, user: str = ROOMS_USER) -> dict:
        resp = self._request("POST", f"/users/{user}/meetings", json={
            "topic": topic,
            "type": MEETING_TYPE_NO_FIXED_TIME,
            "password": new_passcode(),
            "settings": ROOM_SETTINGS,
        })
        if resp.status_code == 404:
            raise ZoomError(f"Zoom user {user} not found")
        return resp.json()

    def update_meeting(self, meeting_id: str, body: dict) -> None:
        resp = self._request("PATCH", f"/meetings/{meeting_id}", json=body)
        if resp.status_code == 404:
            raise ZoomError(f"Zoom meeting {meeting_id} not found")

    def list_users(self) -> list[dict]:
        """Every active user in the account ({"id", "email", ...})."""
        return self._list_pages("/users", "users", {"status": "active"})

    def list_meetings(self, user: str) -> list[dict]:
        """Every scheduled meeting the user owns, of any type ({"id",
        "topic", "type", "start_time", ...})."""
        return self._list_pages(f"/users/{user}/meetings", "meetings", {"type": "scheduled"})

    def list_account_meetings(self) -> list[dict]:
        """Every scheduled meeting in the account, each once (a recurring
        one can be listed per occurrence), with "host_email" set to its
        owner's address."""
        meetings = {}
        for user in self.list_users():
            for m in self.list_meetings(user["id"]):
                meetings.setdefault(str(m["id"]), {**m, "host_email": user["email"]})
        return list(meetings.values())

    def _list_pages(self, path: str, key: str, params: dict) -> list[dict]:
        items = []
        params = {**params, "page_size": 300}
        while True:
            resp = self._request("GET", path, params=params)
            if resp.status_code == 404:
                raise ZoomError(f"Zoom GET {path}: not found")
            data = resp.json()
            items += data.get(key, [])
            if not data.get("next_page_token"):
                return items
            params["next_page_token"] = data["next_page_token"]

    def delete_meeting(self, meeting_id: str) -> None:
        """Delete the meeting (every occurrence of a recurring one), without
        Zoom's cancellation emails. A meeting that's already gone is fine."""
        self._request("DELETE", f"/meetings/{meeting_id}", params={
            "schedule_for_reminder": "false", "cancel_meeting_reminder": "false"})

    def last_used(self, meeting_id: str) -> str | None:
        """Start time (ISO 8601) of the meeting's latest past instance, or
        None if it has never been used."""
        resp = self._request("GET", f"/past_meetings/{meeting_id}/instances")
        if resp.status_code == 404:
            return None
        starts = [m["start_time"] for m in resp.json().get("meetings", []) if m.get("start_time")]
        return max(starts) if starts else None

    def set_passcode(self, meeting_id: str, passcode: str) -> None:
        self.update_meeting(meeting_id, {"password": passcode})
