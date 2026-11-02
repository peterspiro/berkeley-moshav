"""
Tests for util.gather_utils.launch_browser's clearing of the wiki editor's
autosaved text (see CLEAR_EDITOR_AUTOSAVE_JS).
"""

import util.gather_utils as gu


class _Context:
    def __init__(self):
        self.init_scripts = []

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def close(self):
        pass


class _Chromium:
    def __init__(self, chrome_fails=False):
        self.chrome_fails = chrome_fails
        self.context = _Context()

    def launch_persistent_context(self, user_data_dir, channel=None, **kwargs):
        if channel == "chrome" and self.chrome_fails:
            raise RuntimeError("no Chrome")
        return self.context


class _Playwright:
    def __init__(self, chrome_fails=False):
        self.chromium = _Chromium(chrome_fails)


def test_clears_editor_autosave_on_every_page(monkeypatch):
    for chrome_fails in (False, True):
        monkeypatch.setattr(gu, "log", lambda *a, **k: None)
        pw = _Playwright(chrome_fails)
        context = gu.launch_browser(pw)
        assert context.init_scripts == [gu.CLEAR_EDITOR_AUTOSAVE_JS]
        assert 'startsWith("smde_")' in gu.CLEAR_EDITOR_AUTOSAVE_JS


class _SlowClose:
    """A close() that waits until kill() is called, like a Chrome that won't quit."""
    def __init__(self):
        import threading
        self.killed = threading.Event()

    def __call__(self):
        assert self.killed.wait(5), "close wasn't cut short"
        raise RuntimeError("Target page, context or browser has been closed")


def test_close_kills_chrome_that_wont_quit(monkeypatch):
    monkeypatch.setattr(gu, "log", lambda *a, **k: None)
    monkeypatch.setattr(gu, "CLOSE_TIMEOUT_SECONDS", 0.1)
    slow = _SlowClose()
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd)
        slow.killed.set()
    monkeypatch.setattr(gu.subprocess, "run", fake_run)
    gu._bounded_close(slow, "/tmp/pw_chrome_x")()
    assert calls == [["pkill", "-KILL", "-f", "/tmp/pw_chrome_x"]]


def test_close_that_finishes_kills_nothing(monkeypatch):
    monkeypatch.setattr(gu, "log", lambda *a, **k: None)
    monkeypatch.setattr(gu, "CLOSE_TIMEOUT_SECONDS", 0.1)
    calls = []
    monkeypatch.setattr(gu.subprocess, "run", lambda cmd, check: calls.append(cmd))
    gu._bounded_close(lambda: None, "/tmp/pw_chrome_x")()
    import time
    time.sleep(0.2)
    assert calls == []
