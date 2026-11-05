"""
Tests for util/circle_structure_placements.py and seed_circle_structure.py.
All names are fictional.
"""

import json
import textwrap

import pytest

from seed_circle_structure import placements_from_tree
from util.circle_structure_placements import (
    PLACEMENTS_FILE, add_placements, check_parent_kind, read_placements,
)
from util.hierarchy_wiki import parse_hierarchy


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeFiles:
    def __init__(self, content=None):
        self.content = content
        self.created = []
        self.updated = []

    def list(self, q, fields):
        assert PLACEMENTS_FILE in q and "'root' in parents" in q
        return FakeRequest({"files": [{"id": "f1"}] if self.content is not None else []})

    def get_media(self, fileId):
        return FakeRequest(self.content.encode())

    def _body(self, media_body):
        return json.loads(media_body.getbytes(0, media_body.size()).decode())

    def update(self, fileId, media_body):
        self.updated.append(self._body(media_body))
        return FakeRequest({})

    def create(self, body, media_body, fields):
        assert body["name"] == PLACEMENTS_FILE
        self.created.append(self._body(media_body))
        return FakeRequest({"id": "f1"})


class FakeDrive:
    def __init__(self, content=None):
        self._files = FakeFiles(content)

    def files(self):
        return self._files


def test_creates_file():
    drive = FakeDrive()
    add_placements(drive, {"3": "2"}, dry_run=False)
    assert drive.files().created == [{"3": "2"}]


def test_adds_to_waiting_placements():
    drive = FakeDrive('{"5": "1"}')
    add_placements(drive, {"3": "2"}, dry_run=False)
    assert drive.files().updated == [{"3": "2", "5": "1"}]
    assert read_placements(FakeDrive('{"5": "1"}')) == {"5": "1"}


def test_dry_run_writes_nothing():
    drive = FakeDrive()
    add_placements(drive, {"3": "2"}, dry_run=True)
    assert drive.files().created == [] and drive.files().updated == []


@pytest.mark.parametrize("child, parent, ok", [
    ("circle", "circle", True), ("circle", "committee", False),
    ("committee", "circle", True), ("committee", "committee", True),
    ("committee", "club", False), ("club", "circle", False)])
def test_parent_kinds(child, parent, ok):
    if ok:
        check_parent_kind(child, "P", parent)
    else:
        with pytest.raises(ValueError, match="can't be the parent"):
            check_parent_kind(child, "P", parent)


def test_placements_from_tree():
    root = parse_hierarchy(textwrap.dedent("""\
        - Full Community (Top Circle / HOA): [Members](/groups/1)
            - Land Circle: [Members](/groups/2)
                - Garden: [Members](/groups/3)
            - Unlinked Circle
                - Orphan: [Members](/groups/4)
            - Tech Circle: [Members](/groups/5) | [Wiki](/wiki/tech-wiki)
    """))
    placements, skipped = placements_from_tree(root)
    assert placements == {"2": "1", "3": "2", "5": "1"}
    assert skipped == ["Unlinked Circle", "Orphan"]
