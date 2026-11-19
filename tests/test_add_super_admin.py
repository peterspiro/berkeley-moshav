"""Tests for add_super_admin.py and util/super_admins.py, with made-up
members and fake Directory and Licensing services."""

from datetime import date

import httplib2
import pytest
from googleapiclient.errors import HttpError

import add_super_admin as asa
from util.gather_utils import GatherUser
from util.google_group_utils import DOMAIN
from util.super_admins import (FORWARDING_GROUP, candidates, default_local_part, gather_id_of,
                               new_user_body, personal_address, recovery_changes, recovery_phone,
                               routing_rule_steps, temporary_password, welcome_email)

TODAY = date(2026, 6, 1)
ADDRESS = f"jose@{DOMAIN}"


def _user(uid="7", first="José", last="Ortiz", **kw):
    return GatherUser(user_id=uid, first_name=first, last_name=last,
                      full_name=f"{first} {last}", **kw)


def _err(status):
    return HttpError(httplib2.Response({"status": status}), b"")


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeDirectory:
    def __init__(self, accounts=None, forwarding=()):
        self.accounts = {a["primaryEmail"]: a for a in (accounts or [])}
        self.forwarding = list(forwarding)
        self.writes = []

    def users(self):
        return self

    def members(self):
        return _Members(self)

    def get(self, userKey, projection=None):
        def fn():
            if userKey not in self.accounts:
                raise _err(404)
            return self.accounts[userKey]
        return _Call(fn)

    def insert(self, body):
        def fn():
            self.writes.append(("insert", body))
            account = {**body, "isAdmin": False, "customerId": "C0"}
            self.accounts[body["primaryEmail"]] = account
            return account
        return _Call(fn)

    def patch(self, userKey, body):
        def fn():
            self.writes.append(("patch", userKey, body))
            self.accounts[userKey].update(body)
            return self.accounts[userKey]
        return _Call(fn)

    def makeAdmin(self, userKey, body):
        def fn():
            self.writes.append(("makeAdmin", userKey))
            self.accounts[userKey]["isAdmin"] = True
        return _Call(fn)


class _Members:
    def __init__(self, d):
        self.d = d

    def list(self, groupKey, pageToken=None, **kw):
        return _Call(lambda: {"members": [{"email": e, "role": "MEMBER"} for e in self.d.forwarding]})

    def insert(self, groupKey, body):
        def fn():
            self.d.writes.append(("member", groupKey, body["email"]))
            self.d.forwarding.append(body["email"])
        return _Call(fn)


class FakeLicensing:
    def __init__(self, items=(), fail=False):
        self.items = list(items)
        self.fail = fail
        self.deleted = []

    def licenseAssignments(self):
        return self

    def listForProduct(self, productId, customerId, maxResults, pageToken):
        def fn():
            if self.fail:
                raise _err(403)
            return {"items": [i for i in self.items if i["productId"] == productId]}
        return _Call(fn)

    def delete(self, productId, skuId, userId):
        return _Call(lambda: self.deleted.append((productId, skuId, userId)))


# ── Choosing the member ──────────────────────────────────────────────────────

def test_candidates_are_current_adults_by_last_name():
    users = [_user("1", "Dana", "Smith"), _user("2", "Alex", "Green"),
             _user("3", "Kid", "Green", child=True),
             _user("4", "Gone", "Away", departure_date="2026-01-01"),
             _user("5", "Soon", "Away", departure_date="2026-12-01")]
    assert [u.user_id for u in candidates(users, TODAY)] == ["5", "2", "1"]


def test_candidates_match_part_of_a_name_ignoring_accents():
    users = [_user("1"), _user("2", "Dana", "Smith")]
    assert [u.user_id for u in candidates(users, TODAY, "jose o")] == ["1"]


def test_choose_member_asks_until_valid(capsys):
    members = [_user("1"), _user("2", "Dana", "Smith")]
    answers = iter(["x", "3", "2"])
    assert asa.choose_member(members, ask=lambda _: next(answers)).user_id == "2"


def test_choose_member_quits():
    with pytest.raises(SystemExit):
        asa.choose_member([_user()], ask=lambda _: "q")


# ── What the account is made with ───────────────────────────────────────────

def test_personal_address_skips_workspace_addresses():
    assert personal_address(_user(email="jo@example.org")) == "jo@example.org"
    assert personal_address(_user(email=f"jo@{DOMAIN}", google_email="jo@example.com")) == "jo@example.com"
    assert personal_address(_user(email=f"jo@{DOMAIN}")) == ""


@pytest.mark.parametrize("phone, want", [
    ("(510) 555-0123", "+15105550123"),
    ("1-510-555-0123", "+15105550123"),
    ("+44 20 7946 0958", "+442079460958"),
    ("555-0123", ""),
    ("", ""),
])
def test_recovery_phone(phone, want):
    assert recovery_phone(phone) == want


def test_default_local_part():
    assert default_local_part(_user(first="José Ana")) == "joseana"
    assert default_local_part(_user(first="Mary-Kate")) == "marykate"


def test_temporary_password():
    p = temporary_password()
    assert len(p) == 19 and p.count("-") == 3 and p != temporary_password()


def test_new_user_body():
    body = new_user_body(_user(), ADDRESS, "pw", "jo@example.org", "+15105550123")
    assert body["changePasswordAtNextLogin"] is True
    assert body["recoveryEmail"] == "jo@example.org"
    assert body["recoveryPhone"] == "+15105550123"
    assert gather_id_of(body) == "7"


def test_new_user_body_leaves_out_an_unknown_phone():
    assert "recoveryPhone" not in new_user_body(_user(), ADDRESS, "pw", "jo@example.org", "")


def test_recovery_changes():
    account = {"recoveryEmail": "JO@example.org", "recoveryPhone": "+15105550100"}
    assert recovery_changes(account, "jo@example.org", "+15105550123") == {
        "recoveryPhone": "+15105550123"}


def test_routing_rule_and_welcome_email():
    steps = "\n".join(routing_rule_steps(ADDRESS, "jo@example.org"))
    assert "Single recipient" in steps and "Replace recipient: jo@example.org" in steps
    assert "enter 1 to move it to the top" in steps and "Description" not in steps
    subject, body = welcome_email(_user(), ADDRESS)
    assert ADDRESS in body and "temporary password separately" in body


# ── The Google steps ────────────────────────────────────────────────────────

def test_creates_a_new_account():
    d = FakeDirectory()
    account, password = asa.ensure_account(d, _user(), ADDRESS, "jo@example.org",
                                           "+15105550123", dry_run=False)
    assert password and d.writes[0][0] == "insert"
    assert d.writes[0][1]["password"] == password


def test_dry_run_creates_nothing():
    d = FakeDirectory()
    account, password = asa.ensure_account(d, _user(), ADDRESS, "jo@example.org", "", True)
    assert d.writes == [] and password
    asa.ensure_super_admin(d, account, True)
    asa.remove_workspace_licenses(FakeLicensing(), account, True, True)
    asa.ensure_forwarding_member(d, ADDRESS, True)
    assert d.writes == []


def test_existing_account_of_the_member_only_updates_recovery():
    existing = new_user_body(_user(), ADDRESS, "old", "old@example.org", "+15105550123")
    d = FakeDirectory([existing])
    account, password = asa.ensure_account(d, _user(), ADDRESS, "jo@example.org",
                                           "+15105550123", False)
    assert password == ""
    assert d.writes == [("patch", ADDRESS, {"recoveryEmail": "jo@example.org"})]


def test_someone_elses_account_stops():
    d = FakeDirectory([{"primaryEmail": ADDRESS}])
    with pytest.raises(SystemExit):
        asa.ensure_account(d, _user(), ADDRESS, "jo@example.org", "", False)
    assert d.writes == []


def test_makes_super_admin_once():
    d = FakeDirectory([{"primaryEmail": ADDRESS, "isAdmin": False}])
    asa.ensure_super_admin(d, d.accounts[ADDRESS], False)
    asa.ensure_super_admin(d, d.accounts[ADDRESS], False)
    assert d.writes == [("makeAdmin", ADDRESS)]


def test_removes_only_the_accounts_workspace_license():
    lic = FakeLicensing([
        {"productId": "Google-Apps", "skuId": "S1", "userId": ADDRESS},
        {"productId": "Google-Apps", "skuId": "S1", "userId": f"other@{DOMAIN}"},
    ])
    asa.remove_workspace_licenses(lic, {"primaryEmail": ADDRESS, "customerId": "C0"},
                                  created=True, dry_run=False, wait=0)
    assert lic.deleted == [("Google-Apps", "S1", ADDRESS)]


def test_license_check_failure_warns(capsys):
    asa.remove_workspace_licenses(FakeLicensing(fail=True),
                                  {"primaryEmail": ADDRESS, "customerId": "C0"}, False, False)
    assert "Warning" in capsys.readouterr().out


def test_adds_to_forwarding_group_once():
    d = FakeDirectory()
    asa.ensure_forwarding_member(d, ADDRESS, False)
    asa.ensure_forwarding_member(d, ADDRESS, False)
    assert d.writes == [("member", FORWARDING_GROUP, ADDRESS)]
