"""Tests for util.google_group_utils.truncate_local_part, which
add_new_group.py --short-address and change_group_email_alias.py use to
drop a group address's type suffix."""

from util.google_group_utils import truncate_local_part


def test_circle():
    assert truncate_local_part("landscape-circle") == "landscape"


def test_working_group():
    assert truncate_local_part("tech-support-working-group") == "tech-support"


def test_work_group():
    assert truncate_local_part("grounds-work-group") == "grounds"


def test_no_suffix():
    assert truncate_local_part("book-club") is None


def test_suffix_only():
    assert truncate_local_part("-circle") is None
