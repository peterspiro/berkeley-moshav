"""
Tests for util/circle_agenda_docs.py. All names and addresses are fictional.
"""

from util.circle_agenda_docs import (
    GROUP_KEY, MAX_SUB_CIRCLES, PARENT_KEY, REFRESH_KEY, REFRESH_STRUCTURE, STATE_BUILT, STATE_KEY,
    STATE_NEW, SUB_KEY, ensure_agenda_doc, find_agenda_doc, relabel_agenda_doc_structure,
    structure_properties,
)


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
    doc_id = ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "FOLDER", "Full Community",
                               dry_run=False)
    assert doc_id == "new-doc"
    [body] = drive.files().created
    assert body["name"] == "Tech Circle Agenda"
    assert body["mimeType"] == "application/vnd.google-apps.document"
    assert body["parents"] == ["FOLDER"]
    assert body["properties"] == {GROUP_KEY: "tech@example.org", STATE_KEY: STATE_NEW,
                                     PARENT_KEY: "Full Community"}


def test_keeps_existing_doc():
    drive = FakeDrive([{"id": "d1", "name": "Tech Circle Agenda"}])
    assert ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "F", None, False) == "d1"
    assert drive.files().created == []
    assert drive.files().updated == []


def test_dry_run_creates_nothing():
    drive = FakeDrive()
    assert ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "F", None, True) is None
    assert drive.files().created == []


def test_long_parent_name_fits_drive_limit():
    drive = FakeDrive()
    ensure_agenda_doc(drive, "a@example.org", "A", "F", "Ü" * 100, dry_run=False)
    parent = drive.files().created[0]["properties"][PARENT_KEY]
    assert len(PARENT_KEY.encode()) + len(parent.encode()) <= 124


def test_relabels_doc_labelled_by_an_earlier_version():
    old = {GROUP_KEY: "tech@example.org", STATE_KEY: STATE_NEW}
    drive = FakeDrive([{"id": "d1", "name": "Tech Circle Agenda", "appProperties": old}])
    assert ensure_agenda_doc(drive, "tech@example.org", "Tech Circle", "F", None, False) == "d1"
    assert drive.files().updated == [("d1", {"properties": old})]
    assert drive.files().created == []


def test_labels_sub_circles():
    drive = FakeDrive()
    ensure_agenda_doc(drive, "a@example.org", "A", "F", None, False, ["Sub One", "Sub Two"])
    props = drive.files().created[0]["properties"]
    assert props[f"{SUB_KEY}1"] == "Sub One" and props[f"{SUB_KEY}2"] == "Sub Two"


# ── relabel_agenda_doc_structure ─────────────────────────────────────────────

def _doc(**props):
    return {"id": "d1", "name": "Tech Circle Agenda",
            "properties": {GROUP_KEY: "tech@example.org", **props}}


def test_relabels_built_doc_and_asks_for_refresh():
    drive = FakeDrive([_doc(**{STATE_KEY: STATE_BUILT, PARENT_KEY: "Old Parent",
                               f"{SUB_KEY}1": "Sub One", f"{SUB_KEY}2": "Sub Two"})])
    result = relabel_agenda_doc_structure(drive, "tech@example.org", "New Parent", ["Sub One"],
                                          dry_run=False)
    assert result == "relabelled"
    [(doc_id, body)] = drive.files().updated
    assert doc_id == "d1"
    assert body["properties"] == {PARENT_KEY: "New Parent", f"{SUB_KEY}2": None,
                                  REFRESH_KEY: REFRESH_STRUCTURE}


def test_relabels_new_doc_without_refresh():
    drive = FakeDrive([_doc(**{STATE_KEY: STATE_NEW, PARENT_KEY: "Old Parent"})])
    relabel_agenda_doc_structure(drive, "tech@example.org", None, [], dry_run=False)
    assert drive.files().updated == [("d1", {"properties": {PARENT_KEY: None}})]


def test_leaves_doc_already_labelled_so():
    drive = FakeDrive([_doc(**{STATE_KEY: STATE_BUILT, PARENT_KEY: "P", f"{SUB_KEY}1": "S"})])
    assert relabel_agenda_doc_structure(drive, "tech@example.org", "P", ["S"], False) == "unchanged"
    assert drive.files().updated == []


def test_relabel_dry_run_changes_nothing():
    drive = FakeDrive([_doc(**{STATE_KEY: STATE_BUILT})])
    assert relabel_agenda_doc_structure(drive, "tech@example.org", "P", [], True) == "relabelled"
    assert drive.files().updated == []


def test_relabel_with_no_doc():
    assert relabel_agenda_doc_structure(FakeDrive(), "tech@example.org", "P", [], False) == "no doc"


def test_structure_properties_clears_every_unused_sub_label():
    props = structure_properties("P", ["A"])
    assert props[f"{SUB_KEY}1"] == "A"
    assert all(props[f"{SUB_KEY}{i}"] is None for i in range(2, MAX_SUB_CIRCLES + 1))


def test_update_agenda_doc_structure_uses_the_page(monkeypatch, capsys):
    import util.circle_agenda_docs as docs
    from util.hierarchy_wiki import parse_hierarchy
    root = parse_hierarchy("- Top Circle / HOA: [Members](/groups/1)\n"
                           "    - Tech Circle: [Members](/groups/2)\n"
                           "        - Web Working Group: [Members](/groups/4)\n"
                           "        - App Working Group: [Members](/groups/3)\n")
    monkeypatch.setattr(docs, "gather_group_list_email",
                        lambda page, base_url, gid: {"2": "tech@example.org"}.get(gid))
    drive = FakeDrive([_doc(**{STATE_KEY: STATE_BUILT})])
    docs.update_agenda_doc_structure(drive, None, "", root.children[0], dry_run=False)
    [(_, body)] = drive.files().updated
    assert body["properties"][PARENT_KEY] == "Top Circle / HOA"
    assert body["properties"][f"{SUB_KEY}1"] == "App Working Group"
    assert body["properties"][f"{SUB_KEY}2"] == "Web Working Group"
    docs.update_agenda_doc_structure(drive, None, "", root, dry_run=False)
    assert "has no email list" in capsys.readouterr().out
