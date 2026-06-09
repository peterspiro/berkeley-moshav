"""
Shared utilities for Gather browser-automation scripts.

Scripts call configure() before init_log() to set per-script paths,
then import everything else directly from this module.
"""

import csv
import datetime
import io
import os
import re
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, Page, sync_playwright


# ── Configuration ─────────────────────────────────────────────────────────────

_log_file: Path = Path("gather_log.csv")
_screenshot_dir: Path = Path("gather_screenshots")


def configure(log_file: Path, screenshot_dir: Path) -> None:
    """Set per-script log file path and screenshot directory before calling init_log()."""
    global _log_file, _screenshot_dir
    _log_file = log_file
    _screenshot_dir = screenshot_dir


# ── Logging ───────────────────────────────────────────────────────────────────

_log_writer = None
_log_file_handle = None


def init_log() -> None:
    global _log_writer, _log_file_handle
    _screenshot_dir.mkdir(parents=True, exist_ok=True)
    _log_file_handle = open(_log_file, "a", newline="")
    _log_writer = csv.writer(_log_file_handle)
    if _log_file.stat().st_size == 0:
        _log_writer.writerow(["timestamp", "level", "action", "detail", "error"])


def log(level: str, action: str, detail: str = "", error: str = "") -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {level:7s} {action}: {detail}" + (f" — {error}" if error else ""))
    if _log_writer:
        _log_writer.writerow([ts, level, action, detail, error])
        _log_file_handle.flush()


def close_log() -> None:
    if _log_file_handle:
        _log_file_handle.close()


# ── Browser helpers ───────────────────────────────────────────────────────────

def screenshot(page: Page, name: str) -> None:
    _screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = _screenshot_dir / f"{name}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path))
        log("DEBUG", "screenshot", str(path))
    except Exception as e:
        log("WARN", "screenshot", f"Failed to save {path}: {e}")


def _check_submit_errors(page: Page) -> str | None:
    """Return error text if the form showed validation errors, else None."""
    sel = ".error, #error_explanation, .alert-danger, .alert-warning"
    if page.locator(sel).count() > 0:
        return page.locator(sel).first.inner_text()
    return None


def select2_choose(page: Page, field_selector: str, search_text: str) -> None:
    """Open a Select2 dropdown, search, and click the first matching option."""
    container = page.locator(
        f"{field_selector} ~ .select2-container, "
        f"{field_selector} + .select2-container"
    )
    if container.count() > 0:
        container.first.click()
    else:
        page.locator(field_selector).first.evaluate(
            "el => el.nextElementSibling && el.nextElementSibling.click()"
        )
        page.locator(".select2-container").last.click()

    search_input = page.locator(".select2-search__field").last
    search_input.wait_for(state="visible", timeout=5000)
    search_input.fill("")
    search_input.type(search_text)

    option = page.locator(".select2-results__option").filter(has_text=search_text).first
    option.wait_for(state="visible", timeout=8000)
    option.click()


def login(page: Page, base_url: str, email: str, password: str) -> None:
    page.goto(f"{base_url}/people/users/sign-in", wait_until="load")
    if "sign-in" not in page.url and "sign_in" not in page.url:
        log("INFO", "login", "Already signed in")
        return
    page.fill('input[name="user[email]"]', email)
    page.fill('input[name="user[password]"]', password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")
    if "sign-in" in page.url or "sign_in" in page.url:
        screenshot(page, "login_failed")
        raise RuntimeError(f"Login failed — still on {page.url}")
    log("INFO", "login", f"Signed in as {email}")


def launch_browser(pw) -> Browser:
    """Launch Chromium, using the bundled binary if present."""
    chrome_path = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    launch_kwargs: dict = {"args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"]}
    if os.path.exists(chrome_path):
        launch_kwargs["executable_path"] = chrome_path
    return pw.chromium.launch(**launch_kwargs)


# ── Sheet fetching ────────────────────────────────────────────────────────────

def to_csv_export_url(url: str) -> str:
    """Convert any Google Sheets URL to a CSV export URL."""
    m = re.search(r"spreadsheets/d/([^/?#\s]+)", url)
    if not m:
        return url
    sheet_id = m.group(1)
    gid_m = re.search(r"[#&?]gid=(\d+)", url)
    gid = gid_m.group(1) if gid_m else "0"
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={gid}"
    )


def fetch_sheet(url: str) -> str:
    """Fetch a spreadsheet as CSV text, accepting file://, plain paths, or HTTP(S) URLs."""
    if url.startswith("file://"):
        path = url[7:]
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    if not url.startswith("http://") and not url.startswith("https://"):
        with open(url, encoding="utf-8-sig") as f:
            return f.read()
    export_url = to_csv_export_url(url)
    req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8-sig")


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class GatherUser:
    user_id: str
    first_name: str
    last_name: str
    full_name: str
    child: bool = False


@dataclass
class GatherGroupMember:
    user_id: str
    is_manager: bool


@dataclass
class GatherGroup:
    group_id: str
    name: str
    kind: str
    availability: str
    description: str
    members: list[GatherGroupMember] = field(default_factory=list)
    list_name: Optional[str] = None


# ── Gather I/O ────────────────────────────────────────────────────────────────

def fetch_all_gather_users(page: Page, base_url: str) -> list[GatherUser]:
    """Download all users from the Gather directory CSV export."""
    response = page.request.get(f"{base_url}/users.csv")
    reader = csv.DictReader(io.StringIO(response.text()))
    users: list[GatherUser] = []
    for row in reader:
        uid = row.get("ID", "").strip()
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        is_child = row.get("Is Child", "").strip().lower() == "true"
        if not uid:
            continue
        users.append(GatherUser(
            user_id=uid,
            first_name=first,
            last_name=last,
            full_name=f"{first} {last}".strip(),
            child=is_child,
        ))
    return users


def fetch_all_gather_groups(page: Page, base_url: str) -> list[GatherGroup]:
    """Scrape the group list page, returning basic info (members fetched later)."""
    groups: list[GatherGroup] = []
    seen_ids: set[str] = set()
    url: Optional[str] = f"{base_url}/groups"

    while url:
        page.goto(url, wait_until="networkidle")
        for link in page.locator('a[href*="/groups/"]').all():
            href = link.get_attribute("href") or ""
            m = re.search(r"/groups/(\d+)$", href)
            if not m:
                continue
            gid = m.group(1)
            if gid in seen_ids:
                continue
            name = link.inner_text().strip()
            if not name or name in ("Edit", "Delete", "Members", "New Group", "New group"):
                continue
            seen_ids.add(gid)
            groups.append(GatherGroup(group_id=gid, name=name,
                                      kind="", availability="", description=""))

        next_link = page.locator('a[rel="next"]')
        next_href = next_link.get_attribute("href") if next_link.count() > 0 else None
        url = f"{base_url}{next_href}" if next_href else None

    return groups


def _fetch_group_detail(page: Page, base_url: str, group: GatherGroup) -> GatherGroup:
    """Load the group edit page to read current kind, availability, description,
    and inline membership rows."""
    page.goto(f"{base_url}/groups/{group.group_id}/edit", wait_until="networkidle")

    def get_select(name: str) -> str:
        el = page.locator(f'select[name="{name}"]')
        return el.input_value() if el.count() > 0 else ""

    def get_textarea(name: str) -> str:
        el = page.locator(f'textarea[name="{name}"]')
        return el.input_value() if el.count() > 0 else ""

    kind = get_select("groups_group[kind]")
    availability = get_select("groups_group[availability]")
    description = get_textarea("groups_group[description]")

    members: list[GatherGroupMember] = []
    for sel in page.locator('select[name*="[user_id]"]').all():
        uid = sel.input_value()
        if not uid:
            continue
        name_attr = sel.get_attribute("name") or ""
        kind_name = name_attr.replace("[user_id]", "[kind]")
        member_kind = page.locator(f'select[name="{kind_name}"]').input_value()
        members.append(GatherGroupMember(user_id=uid, is_manager=(member_kind == "manager")))

    mailman_name_el = page.locator(
        'input[name*="mailman_list_attributes"][name*="[name]"]'
    )
    list_name: Optional[str] = None
    if mailman_name_el.count() > 0:
        list_name = mailman_name_el.first.input_value().strip() or None

    return GatherGroup(group_id=group.group_id, name=group.name,
                       kind=kind, availability=availability,
                       description=description, members=members,
                       list_name=list_name)


def _add_inline_member(page: Page, user: GatherUser, is_manager: bool) -> None:
    """Click '+ Add Member', then fill in the user and kind for the new row."""
    page.locator('a:has-text("Add Member")').first.click()
    page.wait_for_timeout(400)

    user_sel = page.locator('select[name*="[user_id]"]').last
    uid_name = user_sel.get_attribute("name") or ""
    select2_choose(page, f'select[name="{uid_name}"]', user.full_name)

    kind_name = uid_name.replace("[user_id]", "[kind]")
    page.locator(f'select[name="{kind_name}"]').select_option(
        "manager" if is_manager else "joiner"
    )


def _remove_inline_member(page: Page, user_id: str) -> None:
    """Mark an existing membership row for destruction on the edit form."""
    for sel in page.locator('select[name*="[user_id]"]').all():
        if sel.input_value() == user_id:
            name_attr = sel.get_attribute("name") or ""
            destroy_name = name_attr.replace("[user_id]", "[_destroy]")
            destroy_el = page.locator(f'input[name="{destroy_name}"]')
            if destroy_el.count() > 0:
                page.evaluate(
                    f'document.querySelector(\'input[name="{destroy_name}"]\').value = "1"'
                )
            else:
                m = re.search(r'\[memberships_attributes\]\[(\w+)\]', name_attr)
                if m:
                    key = m.group(1)
                    remove_link = page.locator(
                        f'[data-key="{key}"] a:has-text("Remove"), '
                        f'a[data-remove-fields][data-key="{key}"]'
                    ).first
                    if remove_link.count() > 0:
                        remove_link.click()
                        page.wait_for_timeout(200)
            break


def _codemirror_get(page: Page) -> str:
    """Return current value from the first CodeMirror editor on the page."""
    return page.evaluate(
        "() => { const cm = document.querySelector('.CodeMirror')?.CodeMirror;"
        " return cm ? cm.getValue() : ''; }"
    )


def _codemirror_set(page: Page, text: str) -> None:
    """Set value on the first CodeMirror editor, then trigger change event."""
    page.evaluate(
        "(text) => { const cm = document.querySelector('.CodeMirror')?.CodeMirror;"
        " if (cm) { cm.setValue(text); cm.save(); } }",
        text,
    )


# ── Name matching ─────────────────────────────────────────────────────────────

# Each frozenset is a group of equivalent first names (case-insensitive)
FIRST_NAME_ALIASES: list[frozenset] = [
    frozenset({"katie", "kathryn"}),
    frozenset({"ann", "annie"}),
    frozenset({"yocab", "yacov"}),
]


def _fold_accents(s: str) -> str:
    """Strip diacritical marks so accented characters match their base letter."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _cmp(s: str) -> str:
    """Canonical form for accent- and case-insensitive comparison."""
    return _fold_accents(s).strip().lower()


def first_name_matches(a: str, b: str) -> bool:
    a_l, b_l = _cmp(a), _cmp(b)
    if a_l == b_l:
        return True
    for alias_set in FIRST_NAME_ALIASES:
        if a_l in alias_set and b_l in alias_set:
            return True
    return False


def match_member(
    first: str,
    last_or_initial: Optional[str],
    gather_users: list[GatherUser],
) -> list[GatherUser]:
    results = []
    for user in gather_users:
        if not first_name_matches(first, user.first_name):
            continue
        if last_or_initial is None:
            results.append(user)
        elif last_or_initial.endswith("."):
            initial = _cmp(last_or_initial.rstrip("."))
            if _cmp(user.last_name).startswith(initial):
                results.append(user)
        elif _cmp(user.last_name) == _cmp(last_or_initial):
            results.append(user)
    return results
