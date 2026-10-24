"""
The email telling a group's members about its new calendar and Zoom room.

update_groups_in_google_and_hierarchy.py sends it to the group's email list,
from the signed-in account (CALENDAR_OWNER), when it creates a group's
calendar, after writing the group's footer: Google Groups appends the
footer, with the calendar and Zoom links, when it delivers the message.
Replies go to ALERT_EMAIL (the Technology Circle), not to the list.

Groups made by add_new_group.py don't get it: its welcome message covers the
same ground.
"""

import base64
from email.message import EmailMessage

from util.gather_utils import log

# Who answers questions about calendars and meetings (the Technology Circle);
# the same address the group_meetings Apps Script sends its alerts to.
ALERT_EMAIL = "calendaring-alerts@berkeleymoshav.org"

# How to put a group's meetings on its calendar. Must be viewable by every
# group member.
GROUP_CALENDAR_HOWTO_URL = (
    "https://docs.google.com/document/d/1rr8WqPnsLKw-tgVea40NkyTxlwLFVVLnXbL3UVFQcZY"
    "/edit?tab=t.0#heading=h.5orsgyhrirdv"
)


def build_calendar_announcement(group_name: str) -> tuple[str, str]:
    """Return (subject, body) of the email to a group's list about its new
    calendar and Zoom room."""
    subject = f"{group_name}'s new calendar and Zoom room"
    body = (
        f"Hi {group_name},\n"
        "\n"
        f"{group_name} now has its own Google calendar for its meetings and a "
        "permanent Zoom meeting room. Links to both are in the footer at the "
        "bottom of this message.\n"
        "\n"
        "To see the calendar in your own Google Calendar, click its link in the "
        "footer. Everyone in the group can add and change events on it.\n"
        "\n"
        "Please put the group's meetings on this calendar rather than on your "
        "own: everyone in the group is then invited automatically, and each "
        "meeting gets the Zoom room's details. Here's how:\n"
        f"{GROUP_CALENDAR_HOWTO_URL}\n"
        "\n"
        "The Zoom room is the group's permanent meeting place: its link never "
        "changes, and anyone with the link can join, with or without a host.\n"
        "\n"
        f"Questions? Reply to {ALERT_EMAIL}.\n"
    )
    return subject, body


def send_email(gmail_service, to: str, subject: str, body: str, reply_to: str) -> None:
    """Send a plain-text email from the signed-in account."""
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message["Reply-To"] = reply_to
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _indented(text: str) -> str:
    return "\n".join(f"      {line}" if line else "" for line in text.split("\n"))


def announce_calendar(gmail_service, group_name: str, list_email: str, dry_run: bool) -> None:
    """Email the group's list about its new calendar and Zoom room (or, in
    a dry run, show the email it would send)."""
    subject, body = build_calendar_announcement(group_name)
    if dry_run:
        print(f"  [dry-run] would email {list_email}: \"{subject}\"")
        print(_indented(f"To: {list_email}\nReply-To: {ALERT_EMAIL}\nSubject: {subject}\n\n"
                        f"{body.rstrip()}\n\n(Google Groups appends the group's footer, with "
                        f"the calendar and Zoom links, when it delivers the message.)"))
        log("INFO", "would_announce_calendar", f"{group_name}: {list_email}")
        return
    send_email(gmail_service, list_email, subject, body, ALERT_EMAIL)
    print(f"  + emailed {list_email}: \"{subject}\"")
    log("INFO", "announce_calendar", f"{group_name}: {list_email}")
