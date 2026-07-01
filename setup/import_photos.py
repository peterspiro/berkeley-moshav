#!/usr/bin/env python3
"""
Adds profile photos to Gather members from the community website.

For each photo on the community page, fetches the linked member page to find
first names, matches them to Gather users via the member spreadsheet, and
uploads the photo if the member doesn't already have one.

Usage:
    python gather_photos.py -e admin@example.com -p secret
    python gather_photos.py -e admin@example.com -p secret --dry-run
    python gather_photos.py -e admin@example.com -p secret \
        -u https://example.gather.coop -c https://example.org/meet-the-community.html
"""

import argparse
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import Page, sync_playwright

from util.credentials import load_credentials
from setup.parse_households import parse_households_from_text
from util.gather_utils import (
    close_log,
    configure,
    fetch_sheet,
    init_log,
    launch_browser,
    log,
    login,
    screenshot,
)


COMMUNITY_PAGE_URL = "https://www.berkeleymoshav.org/meet-the-community.html"
DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0f4Dq242kpuqpN7OiVoCnwEm6FDlMHaRlSS4HWiSNY/export?format=tsv"
)
_LOG_FILE = Path("photos_log.csv")
_SCREENSHOT_DIR = Path("photo_screenshots")

configure(_LOG_FILE, _SCREENSHOT_DIR)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].strip().split(";")[0]
        return resp.read().decode(charset, errors="replace")


# ── Community page parsing ────────────────────────────────────────────────────

class _PhotoLinkParser(HTMLParser):
    """Collect (page_url, img_url) pairs from same-domain anchors containing images."""

    def __init__(self, base_url: str):
        super().__init__()
        parsed = urllib.parse.urlparse(base_url)
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._base_url = base_url
        self._current_href: Optional[str] = None
        self._img_in_current = False
        # page_url -> img_url; first image wins per page
        self._seen: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a":
            href = attrs_dict.get("href", "")
            if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                full = urllib.parse.urljoin(self._base_url, href)
                parsed = urllib.parse.urlparse(full)
                target_origin = f"{parsed.scheme}://{parsed.netloc}"
                if target_origin == self._origin and full != self._base_url:
                    self._current_href = full
                    self._img_in_current = False
        elif tag == "img" and self._current_href and not self._img_in_current:
            src = (
                attrs_dict.get("src")
                or attrs_dict.get("data-src")
                or attrs_dict.get("data-lazy-src")
                or ""
            )
            if src:
                img_url = urllib.parse.urljoin(self._base_url, src)
                if self._current_href not in self._seen:
                    self._seen[self._current_href] = img_url
                self._img_in_current = True

    def handle_endtag(self, tag):
        if tag == "a":
            self._current_href = None
            self._img_in_current = False

    @property
    def photos(self) -> list[dict]:
        return [{"page_url": k, "img_url": v} for k, v in self._seen.items()]


class _H2Parser(HTMLParser):
    """Extract the text content of the first <h2> element."""

    def __init__(self):
        super().__init__()
        self._depth = 0
        self._done = False
        self.h2_text: Optional[str] = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "h2":
            self._depth += 1
            if self._depth == 1:
                self._buf = []

    def handle_endtag(self, tag):
        if self._done or self._depth == 0:
            return
        if tag == "h2":
            self._depth -= 1
            if self._depth == 0:
                self._done = True
                self.h2_text = "".join(self._buf).strip()

    def handle_data(self, data):
        if self._depth > 0 and not self._done:
            self._buf.append(data)


def scrape_community_photos(url: str) -> list[dict]:
    """Return list of {"page_url", "img_url"} from the community page."""
    html = _fetch_html(url)
    parser = _PhotoLinkParser(url)
    parser.feed(html)
    return parser.photos


def get_first_names_from_page(url: str) -> list[str]:
    """Fetch a member page and return first names from the first <h2>.

    E.g. a page whose first <h2> reads 'HILARY & NOAH' returns ['Hilary', 'Noah'].
    """
    try:
        html = _fetch_html(url)
    except Exception as e:
        log("WARN", "fetch_page", url, str(e))
        return []
    parser = _H2Parser()
    parser.feed(html)
    if not parser.h2_text:
        return []
    parts = re.split(r"\s*[,&]\s*|\s+AND\s+", parser.h2_text, flags=re.IGNORECASE)
    names = []
    for part in parts:
        words = part.strip().split()
        if words:
            word = words[0].strip(".,;:!?\"'")
            if word:
                names.append(word.title())
    return [n for n in names if n]


# ── Spreadsheet member lookup ─────────────────────────────────────────────────

# Each tuple is a group of interchangeable first-name forms.
_NAME_ALIAS_GROUPS: list[tuple[str, ...]] = [
    ("ann", "annie"),
]

def _name_aliases(name: str) -> frozenset[str]:
    """Return the set of lowercase aliases for a first name (always includes itself)."""
    n = name.lower()
    for group in _NAME_ALIAS_GROUPS:
        if n in group:
            return frozenset(group)
    return frozenset({n})


def _names_match(a: str, b: str) -> bool:
    """Return True if two first names are equal or known aliases of each other."""
    return bool(_name_aliases(a) & _name_aliases(b))


def find_members_for_names(households: list[dict], first_names: list[str]) -> list[dict]:
    """Return members matching first_names.

    With one name, returns the unique member across all households with that first name.
    With two or more names, finds the household containing all of them and returns
    only the members whose first name appears in the list.
    Name matching is alias-aware (e.g. Ann matches Annie).
    """
    if len(first_names) == 1:
        fname = first_names[0]
        matches = [
            m for hh in households for m in hh["members"]
            if _names_match(m["first_name"], fname)
        ]
        if len(matches) == 1:
            return matches
        if not matches:
            log("WARN", "match", f"No member found with first name {fname!r}")
        else:
            full_names = [f"{m['first_name']} {m['last_name']}" for m in matches]
            log("WARN", "match", f"Ambiguous first name {fname!r}: {full_names}")
        return []

    for hh in households:
        # Check that every requested name matches some member in the household
        if all(
            any(_names_match(m["first_name"], req) for m in hh["members"])
            for req in first_names
        ):
            return [
                m for m in hh["members"]
                if any(_names_match(m["first_name"], req) for req in first_names)
            ]

    log("WARN", "match", f"No household found containing all names: {first_names}")
    return []


# ── Gather browser helpers ────────────────────────────────────────────────────

def _search(page: Page, base_url: str, path: str, query: str):
    page.goto(f"{base_url}/{path}", wait_until="networkidle")
    page.locator('input[name="search"]').first.fill(query)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


def _to_edit_url(base_url: str, href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    if href.startswith("http"):
        return f"{href}/edit"
    return f"{base_url}{href}/edit"


def find_user_edit_url(page: Page, base_url: str, member: dict) -> Optional[str]:
    full_name = f"{member['first_name']} {member['last_name']}".strip()
    expected_email = member.get("email", "").strip().lower()
    _search(page, base_url, "users", full_name)
    candidate_hrefs = [
        link.get_attribute("href")
        for link in page.locator(f'a[href*="/users/"]:has-text("{full_name}")').all()
        if link.get_attribute("href")
    ]
    for href in candidate_hrefs:
        edit_url = _to_edit_url(base_url, href)
        if not edit_url:
            continue
        if expected_email:
            page.goto(edit_url, wait_until="networkidle")
            email_input = page.locator('input[name="user[email]"]')
            if email_input.count() == 0:
                continue
            if email_input.input_value().strip().lower() != expected_email:
                continue
        return edit_url
    return None


# ── Photo operations ──────────────────────────────────────────────────────────

def has_custom_photo(page: Page, profile_url: str) -> bool:
    """Return True if the user has an uploaded photo (not the missing-photo placeholder)."""
    page.goto(profile_url, wait_until="networkidle")
    # Gather renders the photo as <img class="photo">; falls back to
    # missing/users/<format>.png when no photo is attached.
    photo = page.locator("img.photo").first
    if photo.count() == 0:
        return False
    src = photo.get_attribute("src") or ""
    return bool(src) and "missing" not in src


def upload_photo(
    page: Page, base_url: str, edit_url: str, img_url: str, member_name: str, dry_run: bool
) -> bool:
    if dry_run:
        log("DRY-RUN", "upload_photo", f"{member_name}: {img_url}")
        return True

    try:
        # Download the community website photo into memory.
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            img_data = resp.read()

        ext = {
            "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"
        }.get(content_type, ".jpg")

        # Navigate to the edit page to establish session context and read the CSRF token.
        page.goto(edit_url, wait_until="networkidle")
        csrf = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""

        # POST the file directly to /uploads using the browser's authenticated session.
        # This replicates what Dropzone.js does server-side without relying on JS
        # event dispatch (which proved unreliable with set_input_files).
        response = page.context.request.post(
            f"{base_url}/uploads",
            headers={"X-CSRF-Token": csrf},
            multipart={
                "class_name": "User",
                "attrib": "photo",
                "file": {
                    "name": f"photo{ext}",
                    "mimeType": content_type,
                    "buffer": img_data,
                },
            },
        )

        if not response.ok:
            log("ERROR", "upload_photo", member_name,
                f"/uploads returned {response.status}: {response.text()[:200]}")
            return False

        blob_id = response.json()["blob_id"]
        log("DEBUG", "upload_photo", f"{member_name}: blob uploaded, signed_id obtained")

        # Inject the signed_id into the hidden field — the same thing Dropzone's
        # fileUploaded callback does via setSignedId().
        page.evaluate(
            "(id) => { const el = document.querySelector('[id$=_photo_new_signed_id]');"
            " if (el) el.value = id; }",
            blob_id,
        )

        # Click the main form's Save (not the Dropzone form's submit).
        main_save = page.locator(
            "form:not(.dropzone) button[type='submit'], "
            "form:not(.dropzone) input[type='submit']"
        ).first
        if main_save.count() == 0:
            log("WARN", "upload_photo", member_name, "Main form save button not found")
            return False

        main_save.click()
        page.wait_for_load_state("networkidle")

        err_el = page.locator(".error, #error_explanation, .alert-danger")
        if err_el.count() > 0:
            err = err_el.first.inner_text()
            screenshot(page, f"photo_error_{member_name[:20].replace(' ', '_')}")
            log("ERROR", "upload_photo", member_name, err[:200])
            return False

        # Verify the photo actually saved.
        profile_url = re.sub(r"/edit$", "", edit_url)
        if not has_custom_photo(page, profile_url):
            screenshot(page, f"photo_verify_{member_name[:20].replace(' ', '_')}")
            log("ERROR", "upload_photo", member_name,
                "Form submitted but photo not visible on profile")
            return False

        log("INFO", "upload_photo", f"Uploaded photo for {member_name}")
        return True

    except Exception as e:
        screenshot(page, f"photo_exc_{member_name[:20].replace(' ', '_')}")
        log("ERROR", "upload_photo", member_name, str(e))
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    sheet_url: str,
    base_url: str,
    community_url: str,
    email: str,
    password: str,
    dry_run: bool = False,
):
    base_url = base_url.rstrip("/")
    init_log()
    log("INFO", "start",
        f"community_url={community_url} base_url={base_url} dry_run={dry_run}")

    log("INFO", "fetch_sheet", sheet_url)
    tsv_text = fetch_sheet(sheet_url)
    households = parse_households_from_text(tsv_text)
    log("INFO", "parse_households", f"{len(households)} households parsed")

    log("INFO", "scrape", f"Fetching {community_url}")
    photos = scrape_community_photos(community_url)
    log("INFO", "scrape", f"{len(photos)} photo entries found")

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            close_log()
            browser.close()
            sys.exit(1)

        stats = {"uploaded": 0, "skipped": 0, "not_found": 0, "failed": 0}

        for entry in photos:
            page_url = entry["page_url"]
            img_url = entry["img_url"]

            first_names = get_first_names_from_page(page_url)
            if not first_names:
                log("DEBUG", "skip_page", f"No names found at {page_url}")
                continue

            log("INFO", "photo_page", f"{page_url} → names: {first_names}")

            members = find_members_for_names(households, first_names)
            if not members:
                continue

            for member in members:
                full_name = f"{member['first_name']} {member['last_name']}".strip()
                edit_url = find_user_edit_url(page, base_url, member)
                if not edit_url:
                    log("WARN", "not_found", f"No Gather user found for {full_name}")
                    stats["not_found"] += 1
                    continue

                profile_url = re.sub(r"/edit$", "", edit_url)
                if has_custom_photo(page, profile_url):
                    log("INFO", "skip", f"{full_name} already has a photo")
                    stats["skipped"] += 1
                    continue

                ok = upload_photo(page, base_url, edit_url, img_url, full_name, dry_run)
                stats["uploaded" if ok else "failed"] += 1

        browser.close()

    log("INFO", "done", str(stats))
    close_log()


def cli():
    parser = argparse.ArgumentParser(
        description="Add profile photos to Gather members from the community website",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-s", "--sheet-url", default=DEFAULT_SHEET_URL,
        help="Google Sheets TSV export URL or local file path",
    )
    parser.add_argument(
        "-u", "--base-url", default="https://berkeley-moshav.gather.coop",
        help="Gather base URL",
    )
    parser.add_argument(
        "-c", "--community-url", default=COMMUNITY_PAGE_URL,
        help="Community website page with member photos",
    )

    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Log what would happen without making any changes",
    )
    args = parser.parse_args()
    email, password = load_credentials()
    main(
        args.sheet_url, args.base_url, args.community_url,
        email, password, args.dry_run,
    )


if __name__ == "__main__":
    cli()
