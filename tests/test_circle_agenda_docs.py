"""
Tests for util/circle_agenda_docs.py. All names and addresses are fictional.
"""

from util.circle_agenda_docs import GROUP_KEY, STATE_KEY, STATE_NEW, ensure_agenda_doc, find_agenda_doc


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeFiles:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.queries = []
        self.created = []
        self.updated = []

    def list(self, **kwargs):
        self.queries.append(kwargs)
        return FakeRequest({"files": self.existing})

    def update(self, fileId, body, supportsAllDrives):
        self.updated.append((fileId, body))
        return FakeRequest({})

    def create(self, body, supportsAllDrives, fields):
        assert supportsAllDrives
        self.created.append(body)
        return FakeRequest({"id": "new-doc"})


class FakeDrive:
    def __init__(self, existing=None):
        self._files = FakeFiles(existing)

    def files(self):
        return self._files


def test_finds_doc_by_group_label():
    drive = FakeDrive([{"id": "d1", "name": "Tech Circle Agenda"}])
    assert find_agenda_doc(drive, "Tech@Example.org") == {"id": "d1", "name": "Tech Circle Agenda"}
    query = drive.files().queries[0]
    assert f"properties has {{ key='{GROUP_KEY}' and value='tech@example.org' }}" in query["q"]
    assert query["corpora"] == "allDrives" and query["includeItemsFromAllDrives"]


def test_creates_labelled_doc_in_drive_item():
    drive = FakeDrive()
    doc_id = ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "FOLDER", dry_run=False)
    assert doc_id == "new-doc"
    [body] = drive.files().created
    assert body["name"] == "Tech Circle Agenda"
    assert body["mimeType"] == "application/vnd.google-apps.document"
    assert body["parents"] == ["FOLDER"]
    assert body["properties"] == {GROUP_KEY: "tech@example.org", STATE_KEY: STATE_NEW}


def test_keeps_existing_doc():
    drive = FakeDrive([{"id": "d1", "name": "Tech Circle Agenda"}])
    assert ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "F", False) == "d1"
    assert drive.files().created == []
    assert drive.files().updated == []


def test_dry_run_creates_nothing():
    drive = FakeDrive()
    assert ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "F", True) is None
    assert drive.files().created == []


def test_relabels_doc_labelled_by_an_earlier_version():
    old = {GROUP_KEY: "tech@example.org", STATE_KEY: STATE_NEW}
    drive = FakeDrive([{"id": "d1", "name": "Tech Circle Agenda", "appProperties": old}])
    assert ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "F", False) == "d1"
    assert drive.files().updated == [("d1", {"properties": old})]
    assert drive.files().created == []
