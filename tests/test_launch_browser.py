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
