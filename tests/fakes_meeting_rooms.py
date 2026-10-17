"""
Fake Google Calendar service and Zoom client for the meeting-room tests.
"""

import httplib2
from googleapiclient.errors import HttpError

OWNER = "admin@berkeleymoshav.org"


def _http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"")


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeCalendarService:
    """calendars: {id: summary}; acls: {id: [rule, ...]}. Records writes."""

    def __init__(self, calendars=None, acls=None, primary=OWNER):
        self.calendars_by_id = dict(calendars or {})
        self.acls = {cid: list(rules) for cid, rules in (acls or {}).items()}
        self.primary = primary
        self.writes = []
        self._next_id = 1

    # calendars()
    def calendars(self):
        return self

    def get(self, calendarId):
        def fn():
            if calendarId == "primary":
                return {"id": self.primary}
            if calendarId not in self.calendars_by_id:
                raise _http_error(404)
            return {"id": calendarId, "summary": self.calendars_by_id[calendarId]}
        return _Call(fn)

    def insert(self, body=None, calendarId=None, sendNotifications=None):
        if calendarId is None:  # calendars().insert
            def fn():
                cid = f"new{self._next_id}@group.calendar.google.com"
                self._next_id += 1
                self.calendars_by_id[cid] = body["summary"]
                self.writes.append(("create", body))
                return {"id": cid}
            return _Call(fn)

        def acl_insert():  # acl().insert
            self.acls.setdefault(calendarId, []).append({**body, "id": "rule"})
            self.writes.append(("acl_insert", calendarId, body, sendNotifications))
            return body
        return _Call(acl_insert)

    def patch(self, calendarId, body, ruleId=None, sendNotifications=None):
        def fn():
            if ruleId is None:
                self.calendars_by_id[calendarId] = body["summary"]
                self.writes.append(("rename", calendarId, body["summary"]))
            else:
                self.writes.append(("acl_patch", calendarId, ruleId, body["role"]))
            return body
        return _Call(fn)

    # calendarList()
    def calendarList(self):
        return _CalendarList(self)

    # acl()
    def acl(self):
        return _Acl(self)


class _CalendarList:
    def __init__(self, svc):
        self.svc = svc

    def list(self, minAccessRole=None, pageToken=None):
        items = [{"id": self.svc.primary, "summary": self.svc.primary, "primary": True}]
        items += [{"id": cid, "summary": s} for cid, s in self.svc.calendars_by_id.items()]
        return _Call(lambda: {"items": items})


class _Acl:
    def __init__(self, svc):
        self.svc = svc

    def list(self, calendarId):
        return _Call(lambda: {"items": list(self.svc.acls.get(calendarId, []))})

    def insert(self, calendarId, body, sendNotifications=None):
        return self.svc.insert(body=body, calendarId=calendarId,
                               sendNotifications=sendNotifications)

    def patch(self, calendarId, ruleId, body, sendNotifications=None):
        return self.svc.patch(calendarId, body, ruleId=ruleId,
                              sendNotifications=sendNotifications)


class FakeZoom:
    """meetings: {id(str): meeting dict}. Records writes."""

    def __init__(self, meetings=None, last_used=None):
        self.meetings = {str(k): v for k, v in (meetings or {}).items()}
        self.last_used_by_id = last_used or {}
        self.writes = []
        self.list_calls = 0

    def get_meeting(self, meeting_id):
        return self.meetings.get(str(meeting_id))

    def create_room(self, topic):
        mid = str(90000 + len(self.meetings))
        meeting = {"id": int(mid), "topic": topic, "join_url": f"https://zoom.us/j/{mid}?pwd=x",
                   "settings": {}, "host_email": "zoomhosts@berkeleymoshav.org"}
        from util.zoom_utils import ROOM_SETTINGS
        meeting["settings"] = dict(ROOM_SETTINGS)
        self.meetings[mid] = meeting
        self.writes.append(("create", topic))
        return meeting

    def update_meeting(self, meeting_id, body):
        self.writes.append(("update", meeting_id, body))

    def list_rooms(self):
        self.list_calls += 1
        return list(self.meetings.values())

    def last_used(self, meeting_id):
        return self.last_used_by_id.get(str(meeting_id))


def room(mid, topic, **kw):
    from util.zoom_utils import ROOM_SETTINGS
    return {"id": mid, "topic": topic, "join_url": f"https://zoom.us/j/{mid}?pwd=p",
            "settings": dict(ROOM_SETTINGS), "host_email": "zoomhosts@berkeleymoshav.org",
            "created_at": "2026-09-01T00:00:00Z", **kw}
