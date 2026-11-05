"""
Tests for util/member_names.py. All names and addresses are fictional.
"""

import json

from util.gather_utils import GatherUser
from util.member_names import MEMBER_NAMES_FILE, build_member_names, publish_member_names


def user(uid, first, last, email="", google_email=""):
    return GatherUser(user_id=uid, first_name=first, last_name=last,
                      full_name=f"{first} {last}".strip(), email=email,
                      google_email=google_email)


class TestBuildMemberNames:
    def test_lists_both_addresses(self):
        names = build_member_names([
            user("1", "Ada", "Lane", "Ada@Example.org", "ada.lane@gmail.com"),
            user("2", "Ben", "Ruiz", "ben@example.org"),
        ])
        assert names == {
            "ada@example.org": "Ada",
            "ada.lane@gmail.com": "Ada",
            "ben@example.org": "Ben",
        }

    def test_initial_only_when_first_name_is_shared(self):
        names = build_member_names([
            user("1", "Ada", "Lane", "a1@example.org"),
            user("2", "ada", "Moss", "a2@example.org"),
            user("3", "Ben", "Ruiz", "b@example.org"),
        ])
        assert names == {"a1@example.org": "Ada L", "a2@example.org": "ada M",
                         "b@example.org": "Ben"}

    def test_full_name_when_initial_is_shared_too(self):
        names = build_member_names([
            user("1", "Ada", "Lane", "a1@example.org"),
            user("2", "Ada", "Lowe", "a2@example.org"),
            user("3", "Ada", "Moss", "a3@example.org"),
        ])
        assert names == {"a1@example.org": "Ada Lane", "a2@example.org": "Ada Lowe",
                         "a3@example.org": "Ada M"}

    def test_counts_members_without_addresses(self):
        # Someone with no address still makes a shared first name ambiguous.
        names = build_member_names([user("1", "Ada", "Lane", "a@example.org"),
                                    user("2", "Ada", "Moss")])
        assert names == {"a@example.org": "Ada L"}

    def test_skips_users_without_names_or_addresses(self):
        assert build_member_names([user("1", "", "", "x@example.org"),
                                   user("2", "Cy", "Ng")]) == {}


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeFiles:
    def __init__(self, existing=None):
        self.existing = existing  # (id, content) or None
        self.calls = []

    def list(self, q, fields):
        assert f"name = '{MEMBER_NAMES_FILE}'" in q
        return FakeRequest({"files": [{"id": self.existing[0]}] if self.existing else []})

    def get_media(self, fileId):
        return FakeRequest(self.existing[1].encode("utf-8"))

    def update(self, fileId, media_body):
        self.calls.append(("update", fileId, media_body.getbytes(0, media_body.size())))
        return FakeRequest({})

    def create(self, body, media_body, fields):
        self.calls.append(("create", body["name"], media_body.getbytes(0, media_body.size())))
        return FakeRequest({"id": "new"})


class FakeDrive:
    def __init__(self, existing=None):
        self._files = FakeFiles(existing)

    def files(self):
        return self._files


NAMES = {"ada@example.org": "Ada Lane"}


class TestPublishMemberNames:
    def test_creates_file(self):
        drive = FakeDrive()
        publish_member_names(drive, NAMES, dry_run=False)
        [(kind, name, data)] = drive.files().calls
        assert (kind, name) == ("create", MEMBER_NAMES_FILE)
        assert json.loads(data) == NAMES

    def test_updates_changed_file(self):
        drive = FakeDrive(existing=("f1", "{}"))
        publish_member_names(drive, NAMES, dry_run=False)
        [(kind, file_id, data)] = drive.files().calls
        assert (kind, file_id) == ("update", "f1")
        assert json.loads(data) == NAMES

    def test_leaves_unchanged_file_alone(self):
        content = json.dumps(NAMES, indent=1, ensure_ascii=False, sort_keys=True)
        drive = FakeDrive(existing=("f1", content))
        publish_member_names(drive, NAMES, dry_run=False)
        assert drive.files().calls == []

    def test_dry_run_writes_nothing(self):
        drive = FakeDrive()
        publish_member_names(drive, NAMES, dry_run=True)
        assert drive.files().calls == []
