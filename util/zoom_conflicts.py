"""
Which meetings in a group's Zoom room would clash with others' under each
Zoom user.

A Zoom user can run only one meeting at a time, so meetings in rooms of the
same user mustn't overlap. gather_meetings() collects the meetings the
group_meetings Apps Script checks for overlaps (checkOverlaps in
group_meetings/group_meetings.gs), over the same OVERLAP_WEEKS:

  - events on group calendars that link a meeting in our Zoom account,
    owned by that meeting's Zoom user; and
  - every Zoom user's meetings scheduled at a fixed time (recurring ones
    split into their occurrences).

A meeting seen more than once (the same event on two calendars, or a
fixed-time Zoom meeting that's also on a calendar) is counted once.

conflicts_under() then finds, for a room and a Zoom user, the pairs of
meetings that would overlap, or be less than MIN_GAP_MINUTES apart, if the
room were under that user: each of the room's meetings against the user's
other meetings. own_conflicts() finds the room's meetings that clash with
each other, which no move fixes.
"""

import datetime
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from zoneinfo import ZoneInfo

from util.gather_utils import log
from util.meeting_rooms import CALENDAR_TIME_ZONE
from util.zoom_utils import MEETING_TYPE_NO_FIXED_TIME

# Keep in step with OVERLAP_WEEKS and MIN_GAP_MINUTES in
# group_meetings/group_meetings.gs.
OVERLAP_WEEKS = 8
MIN_GAP_MINUTES = 15

MEETING_TYPE_SCHEDULED = 2
MEETING_TYPE_RECURRING_FIXED_TIME = 8

# As ANY_ZOOM_ID_RE in group_meetings/event_rules.gs: only links count.
_ZOOM_LINK_ID_RE = re.compile(r"zoom\.us/j/(\d+)")


@dataclass(frozen=True)
class Meeting:
    """One meeting: key tells it apart (a calendar event's iCalUID and
    start, or a fixed-time Zoom meeting's ID and start); meeting_id is the
    Zoom meeting it's held in, owned by the Zoom user owner. places name
    where it's found: the groups whose calendars it's on, or "scheduled in
    Zoom"."""
    key: str
    meeting_id: str
    owner: str
    start: datetime.datetime
    end: datetime.datetime
    title: str
    places: tuple[str, ...]


def _parse_time(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def zoom_link_ids(event: dict) -> list[str]:
    """The IDs of the Zoom meetings the event's location and description
    link, in order, each once."""
    text = f"{event.get('location') or ''} {event.get('description') or ''}"
    return list(dict.fromkeys(_ZOOM_LINK_ID_RE.findall(text)))


def owners_by_meeting(account_meetings: Iterable[dict]) -> dict[str, tuple[str, int]]:
    """{meeting ID: (owner's email, meeting type)} for every meeting in
    the account (ZoomClient.list_account_meetings())."""
    return {str(m["id"]): ((m.get("host_email") or "").lower(), m.get("type"))
            for m in account_meetings}


def event_meetings(events: Iterable[dict], place: str,
                   owners: dict[str, tuple[str, int]]) -> list[Meeting]:
    """The events (a calendar's, with recurring ones split into
    occurrences) held in a meeting in our Zoom account, as Meetings found
    at place. An event that links several is held in the first. All-day
    and cancelled events are left out."""
    out = []
    for event in events:
        if event.get("status") == "cancelled" or not (event.get("start") or {}).get("dateTime"):
            continue
        for mid in zoom_link_ids(event):
            if mid not in owners:
                continue  # not a meeting in our Zoom account
            owner, kind = owners[mid]
            start = _parse_time(event["start"]["dateTime"])
            # A fixed-time Zoom meeting is one meeting wherever it appears;
            # a room hosts many, told apart by their calendar events.
            key = (f"{event.get('iCalUID')}@{start.timestamp():.0f}"
                   if kind == MEETING_TYPE_NO_FIXED_TIME
                   else f"zoom:{mid}@{start.timestamp():.0f}")
            out.append(Meeting(key, mid, owner, start, _parse_time(event["end"]["dateTime"]),
                               event.get("summary") or "(untitled)", (place,)))
            break
    return out


def fixed_time_meetings(account_meetings: Iterable[dict], get_meeting: Callable[[str], dict | None],
                        now: datetime.datetime, until: datetime.datetime) -> list[Meeting]:
    """The account's meetings scheduled at a fixed time that haven't ended
    by now and start before until, a recurring one's occurrences each as a
    Meeting. get_meeting(id) gives a recurring meeting's occurrences."""
    out = []
    for m in account_meetings:
        kind = m.get("type")
        if kind == MEETING_TYPE_RECURRING_FIXED_TIME:
            occurrences = [o for o in (get_meeting(str(m["id"])) or {}).get("occurrences") or []
                           if o.get("status") != "deleted"]
        elif kind == MEETING_TYPE_SCHEDULED:
            occurrences = [{"start_time": m.get("start_time"), "duration": m.get("duration")}]
        else:
            continue
        for o in occurrences:
            if not o.get("start_time"):
                continue
            start = _parse_time(o["start_time"])
            end = start + datetime.timedelta(minutes=o.get("duration") or m.get("duration") or 60)
            if end < now or start > until:
                continue
            out.append(Meeting(f"zoom:{m['id']}@{start.timestamp():.0f}", str(m["id"]),
                               (m.get("host_email") or "").lower(), start, end,
                               m.get("topic") or "(untitled)", ("scheduled in Zoom",)))
    return out


def merge_meetings(meetings: Iterable[Meeting]) -> list[Meeting]:
    """The meetings with each key once, keeping every place it was found."""
    by_key: dict[str, Meeting] = {}
    for m in meetings:
        prev = by_key.get(m.key)
        if prev is None:
            by_key[m.key] = m
        else:
            by_key[m.key] = replace(prev, places=tuple(dict.fromkeys(prev.places + m.places)))
    return sorted(by_key.values(), key=lambda m: (m.start, m.end, m.key))


def _list_events(calendar_service, calendar_id: str, now: datetime.datetime,
                 until: datetime.datetime) -> list[dict]:
    events, page_token = [], None
    while True:
        resp = calendar_service.events().list(
            calendarId=calendar_id, singleEvents=True, timeMin=now.isoformat(),
            timeMax=until.isoformat(), maxResults=250, pageToken=page_token).execute()
        events += resp.get("items", [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return events


def gather_meetings(calendar_service, zoom, groups: Iterable[dict],
                    now: datetime.datetime) -> list[Meeting]:
    """Every meeting in the next OVERLAP_WEEKS that group_meetings checks
    for overlaps. groups are [{"name", "calendar_id"}] (a group with no
    calendar_id is skipped); a calendar that can't be read is skipped with
    a warning."""
    until = now + datetime.timedelta(weeks=OVERLAP_WEEKS)
    account = zoom.list_account_meetings()
    owners = owners_by_meeting(account)
    meetings = fixed_time_meetings(account, zoom.get_meeting, now, until)
    for g in groups:
        if not g.get("calendar_id"):
            continue
        try:
            events = _list_events(calendar_service, g["calendar_id"], now, until)
        except Exception as err:
            print(f"  ! couldn't read {g['name']}'s calendar, so its meetings aren't "
                  f"checked: {err}")
            log("WARN", "list_events", f"{g['name']}: {err}")
            continue
        meetings += event_meetings(events, g["name"], owners)
    return merge_meetings(meetings)


def _clash(a: Meeting, b: Meeting, gap: datetime.timedelta) -> bool:
    return a.start < b.end + gap and b.start < a.end + gap


def _ordered(a: Meeting, b: Meeting) -> tuple[Meeting, Meeting]:
    return (a, b) if (a.start, a.end) <= (b.start, b.end) else (b, a)


def conflicts_under(meetings: list[Meeting], room_id: str, user: str,
                    min_gap_minutes: int = MIN_GAP_MINUTES) -> list[tuple[Meeting, Meeting]]:
    """Pairs (earlier first, soonest first) of one of the room's meetings
    and another meeting of user's that would overlap, or be less than
    min_gap_minutes apart, if the room were under user."""
    gap = datetime.timedelta(minutes=min_gap_minutes)
    user = user.lower()
    room = [m for m in meetings if m.meeting_id == room_id]
    others = [m for m in meetings if m.owner == user and m.meeting_id != room_id]
    pairs = [_ordered(r, o) for r in room for o in others if _clash(r, o, gap)]
    return sorted(pairs, key=lambda p: (p[0].start, p[1].start))


def own_conflicts(meetings: list[Meeting], room_id: str,
                  min_gap_minutes: int = MIN_GAP_MINUTES) -> list[tuple[Meeting, Meeting]]:
    """Pairs of the room's own meetings that overlap or are less than
    min_gap_minutes apart: they clash under any Zoom user."""
    gap = datetime.timedelta(minutes=min_gap_minutes)
    room = [m for m in meetings if m.meeting_id == room_id]
    return [(a, b) for i, a in enumerate(room) for b in room[i + 1:] if _clash(a, b, gap)]


def _local(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(ZoneInfo(CALENDAR_TIME_ZONE))


def _clock(dt: datetime.datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def format_range(start: datetime.datetime, end: datetime.datetime) -> str:
    """"Tue Nov 25, 7:00 PM–8:30 PM", in the calendars' time zone."""
    s, e = _local(start), _local(end)
    return f"{s:%a %b} {s.day}, {_clock(s)}–{_clock(e)}"


def describe_conflict(a: Meeting, b: Meeting) -> list[str]:
    """Lines describing a conflict: what's wrong, then each meeting."""
    if b.start < a.end:
        what = "overlap"
    else:
        minutes = round((b.start - a.end).total_seconds() / 60)
        what = ("back to back" if minutes == 0
                else f"only {minutes} minute{'' if minutes == 1 else 's'} apart")
    line = lambda m: f'  "{m.title}" ({", ".join(m.places)}), {format_range(m.start, m.end)}'
    return [what + ":", line(a), line(b)]
