"""
Helpers for a group's own wiki page (distinct from the shared "Circle
Hierarchy" page handled by util/hierarchy_wiki.py).

The slug/title convention matches the per-group wiki pages the codebase has
always used: a page titled "<Name> Wiki" at slug "<name-slug>-wiki".
"""

import re

from util.gather_utils import (
    _check_submit_errors,
    _codemirror_set,
    _fold_accents,
    log,
    screenshot,
)


def wiki_slug(name: str) -> str:
    """Convert a group name to its wiki page slug, appending '-wiki'.

    E.g. 'Landscape Work Group' → 'landscape-work-group-wiki'.
    """
    s = _fold_accents(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") + "-wiki"


def wiki_title(name: str) -> str:
    """Return the wiki page title for a group, e.g. 'Membership Wiki'."""
    return f"{name} Wiki"


def wiki_page_exists(page, base_url: str, slug: str) -> bool:
    """True if a wiki page with this slug already exists."""
    page.goto(f"{base_url}/wiki/{slug}", wait_until="networkidle")
    page_title = page.title()
    return (
        "Exception" not in page_title
        and "Error" not in page_title
        and "404" not in page_title
        and page.locator("h1").count() > 0
    )


def ensure_named_wiki_page(
    page, base_url: str, title: str, slug: str, content: str, dry_run: bool
) -> bool:
    """Create a wiki page titled `title` at `slug` if it doesn't exist yet.

    Existing pages are left untouched (their content isn't overwritten).
    Returns True if the page exists (already or after creation), False on a
    creation failure.
    """
    try:
        if wiki_page_exists(page, base_url, slug):
            log("INFO", "wiki_page", f"'{title}' already exists, skipping")
            return True
        if dry_run:
            log("DRY-RUN", "create_wiki_page", title)
            return True
        page.goto(f"{base_url}/wiki/new", wait_until="networkidle")
        if page.locator("form").count() == 0:
            log("ERROR", "create_wiki_page", title, "New wiki page form not found")
            return False
        title_el = page.locator('input[name="wiki_page[title]"], #wiki_page_title')
        if title_el.count() > 0:
            title_el.fill(title)
        if page.locator(".CodeMirror").count() == 0:
            log("ERROR", "create_wiki_page", title, "Editor not found on new page form")
            return False
        _codemirror_set(page, content)
        page.locator('input[name="commit"]').click()
        page.wait_for_load_state("networkidle")
        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"wiki_page_err_{slug[:20]}")
            log("ERROR", "create_wiki_page", title, err[:200])
            return False
        log("INFO", "create_wiki_page", f"Created: '{title}'")
        return True
    except Exception as e:
        screenshot(page, f"wiki_page_exc_{slug[:20]}")
        log("ERROR", "create_wiki_page", title, str(e))
        return False
