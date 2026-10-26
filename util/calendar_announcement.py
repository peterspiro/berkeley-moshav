"""
The email telling a group's members about its new calendar and Zoom room.

update_groups_in_google_and_hierarchy.py sends it to the group's email list,
from the signed-in account (CALENDAR_OWNER), when it creates a group's
calendar, after writing the group's footer: how to add the calendar, and
how meeting organizers move the group's meetings onto it. Google Groups
appends the footer, with the calendar and Zoom links, when it delivers the
message. Replies go to ALERT_EMAIL (the Technology Circle), not to the
list.

Groups made by add_new_group.py don't get it: its welcome message covers the
same ground.
"""

import base64
import html
from email.message import EmailMessage

from util.gather_utils import log
from util.google_group_footer import calendar_url

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


_ORGANIZER_STEPS = [
    "On Google calendar, open the existing meeting for editing.",
    "Select your circle’s calendar in the list of calendars (next to the calendar icon).",
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
        f"{_text_list(_ORGANIZER_STEPS)}"
    )
    link = f'<a href="{html.escape(calendar_link)}">this link</a>'
    rich = (
        f"<p>Hello {html.escape(group_name, quote=False)},</p>\n"
        "<p>You now have your own Google calendar and Zoom meeting room.</p>\n"
        "<p>Everyone:<br>\nPlease add the calendar as follows:</p>\n"
        f"{_html_list([f'Click {link}', 'Click Add'])}\n"
        "<p>Meeting organizers:<br>\n"
        "Please move the group's meetings to your new calendar:</p>\n"
        f"{_html_list([html.escape(step, quote=False) for step in _ORGANIZER_STEPS])}\n"
    )
    return subject, text, rich


def send_email(gmail_service, to: str, subject: str, text: str, rich: str,
               reply_to: str) -> None:
    """Send an email from the signed-in account, as plain text with an HTML
    version (rich) that mail programs show instead. Unlike plain text,
    which gets wrapped at about 78 characters on the way, HTML is reflowed
    to fit the reader's window."""
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message["Reply-To"] = reply_to
    message.set_content(text)
    message.add_alternative(rich, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _indented(text: str) -> str:
    return "\n".join(f"      {line}" if line else "" for line in text.split("\n"))


def announce_calendar(gmail_service, group_name: str, list_email: str,
                      calendar_id: str | None, dry_run: bool) -> None:
    """Email the group's list about its new calendar and Zoom room (or, in
    a dry run, show the email it would send; calendar_id is None there if
    the calendar would be created)."""
    link = calendar_url(calendar_id) if calendar_id else "(the new calendar's link)"
    subject, body, rich = build_calendar_announcement(group_name, link)
    if dry_run:
        print(f"  [dry-run] would email {list_email}: \"{subject}\"")
        print(_indented(f"To: {list_email}\nReply-To: {ALERT_EMAIL}\nSubject: {subject}\n\n"
                        f"{body.rstrip()}\n\n(Google Groups appends the group's footer, with "
                        f"the calendar and Zoom links, when it delivers the message.)"))
        log("INFO", "would_announce_calendar", f"{group_name}: {list_email}")
        return
    send_email(gmail_service, list_email, subject, body, rich, ALERT_EMAIL)
    print(f"  + emailed {list_email}: \"{subject}\"")
    log("INFO", "announce_calendar", f"{group_name}: {list_email}")
