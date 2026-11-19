"""Tests for util/mail_draft.py's opening of a draft."""

import pytest

import util.mail_draft as md

class _Done:
    def __init__(self, code):
        self.returncode = code


def test_mac_opens_the_mail_app_with_open(monkeypatch):
    ran = []
    monkeypatch.setattr(md.sys, "platform", "darwin")
    monkeypatch.setattr(md.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or _Done(0))
    monkeypatch.setattr(md.webbrowser, "open", lambda url: pytest.fail("used webbrowser"))
    assert md.open_draft("dana@example.org", "Hi", "Body") is True
    assert ran == [["open", md.mailto_url("dana@example.org", "Hi", "Body")]]


def test_mac_reports_failure(monkeypatch):
    monkeypatch.setattr(md.sys, "platform", "darwin")
    monkeypatch.setattr(md.subprocess, "run", lambda cmd, **kw: _Done(1))
    assert md.open_draft("dana@example.org", "Hi", "Body") is False


def test_elsewhere_uses_webbrowser(monkeypatch):
    opened = []
    monkeypatch.setattr(md.sys, "platform", "linux")
    monkeypatch.setattr(md.webbrowser, "open", lambda url: opened.append(url) or True)
    assert md.open_draft("dana@example.org", "Hi", "Body") is True
    assert opened[0].startswith("mailto:dana@example.org?")
