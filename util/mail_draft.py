"""
Opening an email as a draft in the user's own mail program, through a
mailto: link, for them to review and send.

A mailto: draft carries only the To address, subject, and a plain-text
body: no Reply-To, HTML, or attachments. It's sent from whatever account
the mail program uses.
"""

import webbrowser
from urllib.parse import quote


def mailto_url(to: str, subject: str, body: str) -> str:
    """Return a mailto: URL that opens a draft with these fields filled in.
    Line breaks are sent as CRLF, as RFC 6068 requires."""
    return (
        f"mailto:{quote(to, safe='@')}"
        f"?subject={quote(subject, safe='')}"
        f"&body={quote(body.replace(chr(10), chr(13) + chr(10)), safe='')}"
    )


def open_draft(to: str, subject: str, body: str) -> bool:
    """Open the draft in the default mail program. Returns False if no
    program could be opened."""
    return webbrowser.open(mailto_url(to, subject, body))
