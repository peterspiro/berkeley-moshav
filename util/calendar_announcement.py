"""
The email telling a group's members about its new calendar and Zoom room.

update_groups.py sends it to the group's email list,
from the signed-in account (CALENDAR_OWNER), when it creates a group's
calendar, after writing the group's footer: how to add the calendar, and
how meeting organizers move the group's meetings onto it. Google Groups
appends the footer, with the calendar and Zoom links, when it delivers the
message. Replies go to ALERT_EMAIL (the Technology Circle), not to the
list. Or, with draft, it's opened in the user's own mail program for them
to review and send (util/mail_draft.py), in plain text and without the
Reply-To.

Groups made by add_new_group.py don't get it: its welcome message covers the
same ground.

A community-wide calendar's admin group gets a version of its own
(build_admin_calendar_announcement()): how to schedule community-wide
events, rather than how to move a group's meetings. add_new_group.py's
welcome message for an admin group uses the same text
(admin_calendar_paragraphs()), and so does the admins' guide,
community-calendar-admins-guide.md, which both link once it's a Google Doc
(ADMIN_GUIDE_URL).
"""

import base64
import html
from email.message import EmailMessage
from pathlib import Path

from util.gather_utils import log
from util.google_group_footer import calendar_url
from util.mail_draft import open_draft

# Who answers questions about calendars and meetings (the Technology Circle);
# the same address the group_meetings Apps Script sends its alerts to.
ALERT_EMAIL = "calendaring-alerts@berkeleymoshav.org"

# How to put a group's meetings on its calendar, in the BM Meetings FAQ
# (linked from add_new_group.py's welcome message). Must be viewable by
# every group member.
GROUP_CALENDAR_HOWTO_URL = (
    "https://docs.google.com/document/d/1rr8WqPnsLKw-tgVea40NkyTxlwLFVVLnXbL3UVFQcZY"
    "/edit?tab=t.0#heading=h.5orsgyhrirdv"
)


# The community calendar admins' guide (community-calendar-admins-guide.md,
# made into a Google Doc viewable by anyone with the link), linked from the
# emails to admin groups; until it's set, they don't link it.
ADMIN_GUIDE_URL = ""

# How long before a community-wide event its reminder goes out, by default
# (DEFAULT_REMINDER_LEADS in group_meetings/event_rules.gs).
REMINDER_LEAD_TEXT = "24 hours"
# The gap community-wide meetings must leave (MIN_GAP_MINUTES in
# group_meetings/group_meetings.gs).
MIN_GAP_MINUTES = 15


# Google Calendar's "calendar event" icon (Material Symbols "event"), shown
# in the HTML version where {icon} appears in a step, as an image attached
# to the email.
ICON_PATH = Path(__file__).parent / "calendar_event_icon.png"
ICON_CID = "calendar-event-icon"
_ICON_HTML = (f' <img src="cid:{ICON_CID}" width="18" height="18" alt="(calendar event icon)" '
              'style="vertical-align:middle">')

_ORGANIZER_STEPS = [
    "On Google calendar, open the existing meeting for editing.",
    "Select your circle’s calendar in the list of calendars.  This list is next to the "
    "calendar event icon{icon} and most likely currently displays your name (if the "
    "meeting is currently on your personal calendar).",
    "Delete any Zoom info from the location and description fields.  Your circle’s "
    "permanent Zoom link will be added automatically a few minutes after you click Save.",
    "Delete individual guests (including yourself).  Your circle’s email list will be "
    "added automatically a few minutes after you click Save.  (Do retain any additional "
    "guests not on that email list.)",
    "Click Save.  You may want to choose not to send cancellation emails to removed "
    "Google Calendar guests.",
]


def _text_list(steps: list[str]) -> str:
    return "".join(f"    {i}. {step}\n" for i, step in enumerate(steps, start=1))


def _html_list(items: list[str]) -> str:
    """items are HTML already."""
    return "<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>"


def build_calendar_announcement(group_name: str, calendar_link: str) -> tuple[str, str, str]:
    """Return (subject, plain-text body, HTML body) of the email to a
    group's list about its new calendar and Zoom room. calendar_link is
    the link that adds the calendar to the reader's own Google Calendar."""
    subject = f"{group_name}'s new calendar and Zoom room"
    text = (
        f"Hello {group_name},\n"
        "\n"
        "You now have your own Google calendar and Zoom meeting room.\n"
        "\n"
        "Everyone:\n"
        "    Please add the calendar as follows:\n"
        f"{_text_list([f'Click this link: {calendar_link}', 'Click Add'])}"
        "\n"
        "Meeting organizers:\n"
        "    Please move the group's meetings to your new calendar:\n"
        f"{_text_list([step.format(icon='') for step in _ORGANIZER_STEPS])}"
    )
    link = f'<a href="{html.escape(calendar_link)}">this link</a>'
    rich = (
        f"<p>Hello {html.escape(group_name, quote=False)},</p>\n"
        "<p>You now have your own Google calendar and Zoom meeting room.</p>\n"
        "<p><b>Everyone:</b><br>\nPlease add the calendar as follows:</p>\n"
        f"{_html_list([f'Click {link}', 'Click Add'])}\n"
        "<p><b>Meeting organizers:</b><br>\n"
        "Please move the group's meetings to your new calendar:</p>\n"
        f"{_html_list([html.escape(step, quote=False).format(icon=_ICON_HTML)
                       for step in _ORGANIZER_STEPS])}\n"
    )
    return subject, text, rich


def admin_calendar_paragraphs(calendar_name: str, audience: str,
                              zoom: bool = True) -> list[str]:
    """The paragraphs, in plain text, that tell a community-wide calendar's
    admins how its events are scheduled: shared by the email when the
    calendar is made and add_new_group.py's welcome message, after each
    one's own paragraph about who can add events (who_sees_events()). A line
    starting "- " is a list item. Without zoom (a calendar made with no Zoom
    room), nothing is said of a room, its host key, or clashes."""
    room_item = (
        "- a link to the calendar's Zoom room, if it doesn't already have another Zoom or "
        "Meet link (or says [no zoom] in the description);\n")
    paragraphs = [
        "Scheduling an event:\n"
        f"Create the event on the {calendar_name} calendar itself, not on your own "
        "calendar. (To add the calendar to your own Google Calendar, click its link in the "
        "footer.)",
        ("A few minutes after you save it, the event automatically gets:\n"
         f"{room_item}"
         f"- the whole community ({audience}) invited, each member getting the invitation "
         "by email, unless the event says [no invite].\n"
         "So there's no need to add Zoom details or guests yourself." if zoom else
         f"A few minutes after you save it, the whole community ({audience}) is invited "
         "automatically, each member getting the invitation by email, unless the event "
         "says [no invite]. So there's no need to add guests yourself."),
        f"Everyone invited is also emailed a reminder {REMINDER_LEAD_TEXT} before each "
        "event, and replies to it come to this list. You can change the reminder time by "
        "adding, for example, [remind 2 hours] or [no reminder] in its description.",
    ]
    if zoom:
        paragraphs += [
            "The Zoom room:\n"
            "The room is the calendar's permanent meeting place: its link never changes, "
            "and anyone with the link can join, with or without a host. For host features "
            "such as breakout rooms or recording, one can claim host with the host key, "
            "which is in the doc linked from the footer (\"Zoom host key\").",
            "We have several community-wide calendars, all of which have zoom rooms which "
            "belong to the same Zoom user, which can host only one meeting at a time. If an "
            f"event overlaps another community-wide event, or is less than {MIN_GAP_MINUTES} "
            "minutes from one, you'll get an email asking the admins involved to agree "
            "which one moves.",
        ]
    if ADMIN_GUIDE_URL:
        paragraphs.append(f"All of this, and more, is in the guide for community calendar "
                          f"admins:\n{ADMIN_GUIDE_URL}")
    return paragraphs


def who_sees_events(calendar_name: str) -> str:
    """The end of the paragraph saying who can add a community-wide
    calendar's events, and who sees them."""
    return (f"can add and change events on the {calendar_name} calendar. The rest of the "
            "community sees these events on their primary calendars.")


def _text_to_html(text: str) -> str:
    """Plain text as HTML: blank-line-separated paragraphs, lines starting
    "- " as a bulleted list (as textToHtml in group_meetings does)."""
    out = []
    for block in text.strip().split("\n\n"):
        lines, items = [], []
        for line in block.split("\n"):
            if line.startswith("- "):
                items.append(html.escape(line[2:], quote=False))
            elif items:
                out.append(f"<p>{'<br>'.join(lines)}</p>" if lines else "")
                out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
                lines, items = [html.escape(line, quote=False)], []
            else:
                lines.append(html.escape(line, quote=False))
        if lines:
            out.append(f"<p>{'<br>'.join(lines)}</p>")
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
    return "\n".join(p for p in out if p) + "\n"


def build_admin_calendar_announcement(calendar_name: str, audience: str,
                                      zoom: bool = True) -> tuple[str, str, str]:
    """Return (subject, plain-text body, HTML body) of the email to a
    community-wide calendar's admin group about its new calendar and Zoom
    room (without zoom, a calendar with no room: just its calendar)."""
    if zoom:
        subject = f"The {calendar_name} calendar and Zoom room"
        what = (f"a Google calendar and a Zoom meeting room for {calendar_name}, for "
                "community-wide events. Their links are")
    else:
        subject = f"The {calendar_name} calendar"
        what = (f"a Google calendar for {calendar_name}, for community-wide events. Its "
                "link is")
    text = "\n\n".join([
        f"Hello {calendar_name} admins,",
        f"You now have {what} in the footer at the bottom of this message.",
        f"Everyone on this list {who_sees_events(calendar_name)}",
        *admin_calendar_paragraphs(calendar_name, audience, zoom),
        "Questions? Reply to this message.",
    ]) + "\n"
    return subject, text, _text_to_html(text)


def send_email(gmail_service, to: str, subject: str, text: str, rich: str,
               reply_to: str) -> None:
    """Send an email from the signed-in account, as plain text with an HTML
    version (rich) that mail programs show instead, with the icon attached
    for it if it shows it. Unlike plain text, which gets wrapped at about 78 characters on
    the way, HTML is reflowed to fit the reader's window."""
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message["Reply-To"] = reply_to
    message.set_content(text)
    message.add_alternative(rich, subtype="html")
    if f"cid:{ICON_CID}" in rich:
        message.get_payload()[1].add_related(
            ICON_PATH.read_bytes(), "image", "png", cid=f"<{ICON_CID}>",
            filename="calendar_event_icon.png", disposition="inline")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _indented(text: str) -> str:
    return "\n".join(f"      {line}" if line else "" for line in text.split("\n"))


def _show(list_email: str, subject: str, body: str, reply_to: str | None) -> None:
    reply_line = f"Reply-To: {reply_to}\n" if reply_to else ""
    print(_indented(f"To: {list_email}\n{reply_line}Subject: {subject}\n\n"
                    f"{body.rstrip()}\n\n(Google Groups appends the group's footer, with "
                    f"the calendar and Zoom links, when it delivers the message.)"))


def announce_calendar(gmail_service, group_name: str, list_email: str,
                      calendar_id: str | None, dry_run: bool, draft: bool = False,
                      audience: str | None = None, zoom: bool = True) -> None:
    """Email the group's list about its new calendar and Zoom room, or with
    draft, open the email as a draft in the user's mail program (or, in a
    dry run, show the email it would send or open; calendar_id is None
    there if the calendar would be created). With audience, the group is a
    community-wide calendar's admin group (group_name naming the calendar),
    and gets the admins' version (without zoom, for a calendar with no Zoom
    room)."""
    if audience:
        subject, body, rich = build_admin_calendar_announcement(group_name, audience, zoom)
    else:
        link = calendar_url(calendar_id) if calendar_id else "(the new calendar's link)"
        subject, body, rich = build_calendar_announcement(group_name, link)
    if dry_run:
        if draft:
            print(f"  [dry-run] would open a draft to {list_email} in your mail program: "
                  f"\"{subject}\"")
        else:
            print(f"  [dry-run] would email {list_email}: \"{subject}\"")
        _show(list_email, subject, body, None if draft else ALERT_EMAIL)
        log("INFO", "would_announce_calendar", f"{group_name}: {list_email}")
        return
    if draft:
        if open_draft(list_email, subject, body):
            print(f"  + opened a draft to {list_email} in your mail program: \"{subject}\" "
                  f"— review it and click Send")
            log("INFO", "announce_calendar_draft", f"{group_name}: {list_email}")
        else:
            print(f"  ! couldn't open a draft in a mail program; send this to {list_email} "
                  f"yourself:")
            _show(list_email, subject, body, None)
            log("WARN", "announce_calendar_draft", f"{group_name}: no mail program")
        return
    send_email(gmail_service, list_email, subject, body, rich, ALERT_EMAIL)
    print(f"  + emailed {list_email}: \"{subject}\"")
    log("INFO", "announce_calendar", f"{group_name}: {list_email}")
