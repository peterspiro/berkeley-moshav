"""
The parts of add_super_admin.py that don't talk to Google or Gather:
choosing the member, and what their account is made with.

A super admin here is a Cloud Identity Free user in the Workspace: it has
no Gmail mailbox, so a Default routing rule (made by hand in the Admin
console, since Google has no API for it) sends its mail to the member's
own address, and it's a member of admin-forwarding@, which gets the
filtered copies of admin@'s mail.
"""

import re
import secrets
import unicodedata
from datetime import date

from util.gather_utils import GatherUser
from util.google_group_utils import DOMAIN, is_in_domain

FORWARDING_GROUP = f"admin-forwarding@{DOMAIN}"
# Where the Gather user ID is kept on the account, so a later run knows
# whose account an address is.
GATHER_ID_TYPE = "gather_id"
ADMIN_CONSOLE_URL = "https://admin.google.com"
# Workspace licenses a new user may be given automatically; without one,
# the user is a Cloud Identity Free user.
WORKSPACE_PRODUCT = "Google-Apps"
# Left out of temporary passwords: easy to misread or mistype.
_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").casefold()


def _has_left(departure: str, today: date) -> bool:
    if not departure:
        return False
    try:
        return date.fromisoformat(departure[:10]) <= today
    except ValueError:
        return True


def candidates(users: list[GatherUser], today: date, query: str = "") -> list[GatherUser]:
    """The adults in the directory who haven't left, sorted by name; with a
    query, only those whose name contains it (ignoring case and accents)."""
    q = _fold(query.strip())
    found = [u for u in users
             if not u.child and u.full_name and not _has_left(u.departure_date, today)
             and q in _fold(u.full_name)]
    return sorted(found, key=lambda u: (_fold(u.last_name), _fold(u.first_name)))


def personal_address(user: GatherUser) -> str:
    """The member's own address, for their mail and account recovery: their
    Gather email, or else their Google Email, if it's outside the
    Workspace; "" if neither is."""
    for address in (user.email, user.google_email):
        if address and not is_in_domain(address):
            return address
    return ""


def recovery_phone(phone: str) -> str:
    """A directory phone number in the E.164 form Google needs
    ("+15105550123"), taking a 10-digit number as North American; "" if it
    can't be read."""
    if phone.strip().startswith("+"):
        digits = re.sub(r"\D", "", phone)
        return f"+{digits}" if 8 <= len(digits) <= 15 else ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return ""


def default_local_part(user: GatherUser) -> str:
    """The new address's name: the member's first name, in lower case,
    without accents, spaces, or punctuation ("José Ana" → "joseana")."""
    return re.sub(r"[^a-z0-9]", "", _fold(user.first_name))


def temporary_password(length: int = 16) -> str:
    """A random password for the first sign-in, in groups of four."""
    chars = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
    return "-".join(chars[i:i + 4] for i in range(0, length, 4))


def gather_id_of(account: dict) -> str:
    """The Gather user ID stored on a Workspace account, or ""."""
    for e in account.get("externalIds") or []:
        if e.get("type") == "custom" and e.get("customType") == GATHER_ID_TYPE:
            return e.get("value", "")
    return ""


def new_user_body(user: GatherUser, address: str, password: str,
                  recovery_email: str, phone: str) -> dict:
    """The Directory API body that creates the member's account."""
    body = {
        "primaryEmail": address,
        "name": {"givenName": user.first_name, "familyName": user.last_name or user.first_name},
        "password": password,
        "changePasswordAtNextLogin": True,
        "orgUnitPath": "/",
        "externalIds": [{"type": "custom", "customType": GATHER_ID_TYPE, "value": user.user_id}],
    }
    body.update(recovery_fields(recovery_email, phone))
    return body


def recovery_fields(recovery_email: str, phone: str) -> dict:
    """The account's recovery email and phone, leaving out any not known."""
    fields = {}
    if recovery_email:
        fields["recoveryEmail"] = recovery_email
    if phone:
        fields["recoveryPhone"] = phone
    return fields


def primary_admin_steps() -> list[str]:
    """How to check that admin@ is still the Workspace's primary admin.
    Making the new account a super admin once also made it the primary
    admin, and there's no API to read or set the primary admin."""
    return [
        f"In the Admin console ({ADMIN_CONSOLE_URL}), go to Account → Account settings →",
        "Profile, and check that the Primary admin is still admin@. Making a new super",
        "admin has been known to make it the primary admin instead. If it has, change",
        "the Primary admin back to admin@ and save.",
    ]


def recovery_changes(account: dict, recovery_email: str, phone: str) -> dict:
    """The recovery fields an existing account needs changed."""
    return {k: v for k, v in recovery_fields(recovery_email, phone).items()
            if (account.get(k) or "").casefold() != v.casefold()}


def routing_rule_steps(address: str, personal: str) -> list[str]:
    """How to add the Default routing rule that sends the account's mail to
    the member's own address."""
    return [
        f"In the Admin console ({ADMIN_CONSOLE_URL}), go to Apps → Google Workspace →",
        "Gmail → Default routing, at the top-level organizational unit, and add a rule:",
        "  1. Specify envelope recipients to match: Single recipient,",
        f"     Email address: {address}",
        "  2. If the envelope recipient matches the above, do the following:",
        "     Modify message → Envelope recipient → Change envelope recipient,",
        f"     Replace recipient: {personal}",
        "  3. Options: Perform this action on non-recognized and recognized addresses.",
        "  Save. The new rule goes to the bottom of the list, below the rule for All",
        "  recipients: click Reorder on the new rule, and enter 1 to move it to the top.",
    ]


def welcome_email(user: GatherUser, address: str) -> tuple[str, str]:
    """(subject, body) of the email to the new admin's own address. It
    leaves out the temporary password, which is given to them separately."""
    subject = "Your Berkeley Moshav Google admin account"
    body = (
        f"Hi {user.first_name},\n\n"
        f"You now have a Google account, {address}, that's a super admin of the "
        f"Berkeley Moshav Workspace. I'll give you its temporary password separately.\n\n"
        "To start using it:\n"
        f"1. In a private browser window (so your own Google account isn't in the way), "
        f"go to {ADMIN_CONSOLE_URL} and sign in as {address} with the temporary password.\n"
        "2. Choose a new password. Keep it in a password manager: a super admin can't "
        "reset their own password by email, so if you lose it, another admin has to.\n"
        "3. Set up 2-Step Verification when you're asked to (or under "
        "myaccount.google.com → Security), preferably with a passkey or an "
        "authenticator app.\n\n"
        f"The account has no mailbox of its own: mail sent to {address} comes to this "
        "address instead. You're also on the admin-forwarding list, which gets copies of "
        "the mail that the shared admin account receives.\n\n"
        "Please use the account only for Workspace administration, and sign out of it "
        "when you're done.\n"
    )
    return subject, body
