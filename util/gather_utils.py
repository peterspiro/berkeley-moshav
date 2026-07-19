"""
Shared utilities for Gather browser-automation scripts.

Scripts call configure() before init_log() to set per-script paths,
then import everything else directly from this module.
"""

import csv
import datetime
import atexit
import io
import os
import shutil
import sys
import re
import tempfile
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


# ── Configuration ─────────────────────────────────────────────────────────────

_log_file: Path = Path("debug/gather_log.csv")
_screenshot_dir: Path = Path("debug/gather_screenshots")


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
    _log_file.parent.mkdir(parents=True, exist_ok=True)
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


def log_noop(quiet: bool, level: str, action: str, detail: str = "", error: str = "") -> None:
    """Like log(), but suppressed when quiet is True. Use for lines that
    describe an already-correct/no-op state, never for an actual change."""
    if not quiet:
        log(level, action, detail, error)


def set_gather_group_email_list(
    page: Page,
    base_url: str,
    group_id: str,
    list_local_part: str,
    domain: str,
    dry_run: bool,
    quiet: bool = False,
    all_can_send: bool = True,
) -> None:
    """
    Open the Gather group edit page and configure the email list:
      - Set the list address local part and domain (only when fields are enabled,
        i.e. the list has not yet been created — Gather disables them after creation)
      - Set "All community members can send to list?" to `all_can_send`
        (clubs want this off; True/on for everything else)
    """
    page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")

    # Name field — name="groups_group[mailman_list_attributes][name]"
    # Disabled once the list exists; Gather won't let it be changed after creation.
    name_input = page.locator('#groups_group_mailman_list_attributes_name')
    if name_input.count() == 0:
        log("WARN", "set_email_list", f"group {group_id}: mailman name field not found")
        return

    name_disabled = name_input.first.is_disabled()
    current_name = name_input.first.input_value().strip()

    # Domain select — name="groups_group[mailman_list_attributes][domain_id]"
    # Also disabled once list exists. Select by option label (the domain text), not value (numeric ID).
    domain_select = page.locator('#groups_group_mailman_list_attributes_domain_id')

    # "All community members can send to list?" checkbox
    everyone_checkbox = page.locator('#groups_group_mailman_list_attributes_all_cmty_members_can_send')

    if dry_run:
        checked = everyone_checkbox.first.is_checked() if everyone_checkbox.count() > 0 else False
        changes = []
        address_blocked = False
        if name_disabled:
            if current_name != list_local_part:
                address_blocked = True
                log("WARN", "set_email_list",
                    f"group {group_id}: name field disabled but value '{current_name}' ≠ '{list_local_part}'")
        else:
            # Mirrors the live branch below: the domain is always (re)submitted
            # here (not conditionally compared), since this path only runs
            # while the list hasn't been created yet.
            if current_name != list_local_part:
                changes.append(f"name: '{current_name}' → '{list_local_part}'")
            if domain_select.count() > 0:
                changes.append(f"domain → '{domain}'")
            else:
                log("WARN", "set_email_list", f"group {group_id}: domain selector not found")
        if checked != all_can_send:
            changes.append(f"everyone_can_post: {checked} → {all_can_send}")

        if changes:
            log("INFO", "set_email_list",
                f"[dry-run] group {group_id}: would change {', '.join(changes)}")
        elif address_blocked:
            # Distinct from the "already correct" case below: the address
            # itself is wrong, but this function can't fix it while the
            # field stays disabled (see the WARN above) — nothing else here
            # needed changing, but calling that "already correct" would
            # contradict the WARN sitting right above it.
            log_noop(quiet, "INFO", "set_email_list",
                      f"group {group_id}: no other email list settings need changing "
                      "(address mismatch noted above can't be fixed while disabled)")
        else:
            log_noop(quiet, "INFO", "set_email_list",
                      f"group {group_id}: email list settings already correct")
        return

    changed = False

    if not name_disabled:
        if current_name != list_local_part:
            name_input.first.fill(list_local_part)
            changed = True
        if domain_select.count() > 0:
            domain_select.first.select_option(label=domain)
            changed = True
        else:
            log("WARN", "set_email_list", f"group {group_id}: domain selector not found")
    elif current_name != list_local_part:
        log("WARN", "set_email_list",
            f"group {group_id}: name field disabled but value '{current_name}' ≠ '{list_local_part}'")

    if everyone_checkbox.count() > 0:
        if everyone_checkbox.first.is_checked() != all_can_send:
            # The checkbox is CSS-hidden; set it directly via JS to bypass visibility checks.
            js_bool = "true" if all_can_send else "false"
            page.evaluate(
                "document.getElementById"
                f"('groups_group_mailman_list_attributes_all_cmty_members_can_send').checked = {js_bool}"
            )
            changed = True
    else:
        log("WARN", "set_email_list", f"group {group_id}: all_cmty_members_can_send checkbox not found")

    if not changed:
        log_noop(quiet, "INFO", "set_email_list",
                  f"group {group_id}: email list settings already correct")
        return

    page.locator('input[type="submit"]').first.click(timeout=10_000)
    page.wait_for_load_state("networkidle", timeout=30_000)

    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"group_{group_id}_error")
        log("ERROR", "set_email_list", f"group {group_id}: form error — {err}")
    else:
        log("INFO", "set_email_list", f"group {group_id}: email list settings updated")


def destroy_gather_group_email_list(page: Page, base_url: str, group_id: str, dry_run: bool) -> bool:
    """Delete a group's existing mailing list entirely.

    Needed before assigning a different address to a group that already
    has a list configured: set_gather_group_email_list() can't do this —
    Gather disables the name/domain fields once a list exists, so they
    can't simply be edited in place. Returns True if a list was destroyed
    (or none existed to begin with), False on failure.
    """
    page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")

    name_input = page.locator('#groups_group_mailman_list_attributes_name')
    if name_input.count() == 0:
        log("WARN", "destroy_email_list", f"group {group_id}: mailman name field not found")
        return False
    if not name_input.first.input_value().strip():
        return True  # no list configured — nothing to destroy

    # "Delete this list?" checkbox at the bottom of the edit page — found
    # by its label text rather than a guessed ID, since Rails' generated
    # id/name for this checkbox isn't documented anywhere in this codebase.
    label = page.locator('label:has-text("Delete this list?")')
    if label.count() == 0:
        log("ERROR", "destroy_email_list", f"group {group_id}",
            "no \"Delete this list?\" checkbox found — delete it "
            "manually in the Gather UI, then re-run")
        return False

    checkbox_id = label.first.get_attribute("for")
    checkbox = (
        page.locator(f"#{checkbox_id}") if checkbox_id
        else label.first.locator(
            'xpath=preceding-sibling::input[@type="checkbox"][1] | '
            'following-sibling::input[@type="checkbox"][1]'
        )
    )
    if checkbox.count() == 0:
        log("ERROR", "destroy_email_list", f"group {group_id}",
            "found the \"Delete this list?\" label but couldn't locate its checkbox")
        return False

    if dry_run:
        log("DRY-RUN", "destroy_email_list", f"group {group_id}: would delete existing mailing list")
        return True

    if not checkbox.first.is_checked():
        checkbox.first.check()

    # Click the Save button belonging to *this* checkbox's own <form> —
    # the page may have more than one form/submit button, and clicking an
    # unrelated one would submit successfully (no validation error to
    # catch) while silently never sending this checkbox's change at all.
    form = checkbox.first.locator("xpath=ancestor::form[1]")
    submit = form.locator('input[type="submit"][name="commit"]')
    if submit.count() == 0:
        submit = form.locator('input[type="submit"]')
    if submit.count() == 0:
        log("ERROR", "destroy_email_list", f"group {group_id}",
            "found the \"Delete this list?\" checkbox but no Save button in its form")
        return False
    post_submit_url = page.url
    submit.first.click(timeout=10_000)
    page.wait_for_load_state("networkidle", timeout=30_000)
    log("DEBUG", "destroy_email_list", f"group {group_id}: pre-submit URL was {post_submit_url}, "
        f"post-submit URL is {page.url}")

    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"group_{group_id}_destroy_list_error")
        log("ERROR", "destroy_email_list", f"group {group_id}", err[:200])
        return False

    # A "successful" submit (no validation error) doesn't guarantee the
    # destroy actually applied — e.g. the wrong form, a misrouted submit,
    # or a silently-ignored param would look identical here. Reload the
    # edit page fresh and check the list is actually gone before claiming
    # success.
    page.goto(f"{base_url}/groups/{group_id}/edit", wait_until="networkidle")
    name_input_after = page.locator('#groups_group_mailman_list_attributes_name')
    if name_input_after.count() > 0 and name_input_after.first.input_value().strip():
        screenshot(page, f"group_{group_id}_destroy_list_still_present")
        log("ERROR", "destroy_email_list", f"group {group_id}",
            "submitted with no validation error, but the mailing list still exists "
            f"afterward (name={name_input_after.first.input_value().strip()!r}) — "
            "see screenshot")
        return False

    log("INFO", "destroy_email_list", f"group {group_id}: mailing list deleted")
    return True


def fill_group_basics(
    page: Page, name: str, kind: str, availability: str, description: str = ""
) -> None:
    """Fill the basic (non-member) fields of the group create/edit form."""
    page.locator('input[name="groups_group[name]"]').fill(name)
    page.locator('select[name="groups_group[kind]"]').select_option(kind)
    page.locator('select[name="groups_group[availability]"]').select_option(availability)
    desc_el = page.locator('textarea[name="groups_group[description]"]')
    if desc_el.count() > 0:
        desc_el.fill(description)


def find_gather_group_id_by_name(page: Page, base_url: str, name: str) -> Optional[str]:
    """Scan the groups list page (all pages) for a group whose name exactly
    matches `name` (case-insensitive); return its group_id, or None."""
    url: Optional[str] = f"{base_url}/groups"
    while url:
        page.goto(url, wait_until="networkidle")
        for link in page.locator('a[href*="/groups/"]').all():
            href = link.get_attribute("href") or ""
            m = re.search(r"/groups/(\d+)$", href)
            if not m:
                continue
            text = link.inner_text().strip()
            if text.casefold() == name.casefold():
                return m.group(1)
        next_link = page.locator('a[rel="next"]')
        next_href = next_link.get_attribute("href") if next_link.count() > 0 else None
        url = f"{base_url}{next_href}" if next_href else None
    return None


def create_gather_group(
    page: Page,
    base_url: str,
    name: str,
    kind: str,
    availability: str = "closed",
    description: str = "",
    dry_run: bool = False,
) -> Optional[str]:
    """Create a new Gather group (no members). Return its group_id, or
    "dry-run" if dry_run, or None on failure."""
    if dry_run:
        log("DRY-RUN", "create_group", name)
        return "dry-run"

    try:
        page.goto(f"{base_url}/groups/new", wait_until="networkidle")
        fill_group_basics(page, name, kind, availability, description)
        page.locator('input[name="commit"]').click()
        page.wait_for_load_state("networkidle")
        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"group_form_err_{name[:20]}")
            log("ERROR", "group_form", name, err[:200])
            return None

        post_submit_url = page.url
        log("DEBUG", "create_group", f"Post-submit URL: {post_submit_url}")
        screenshot(page, f"group_create_postsubmit_{name[:20]}")

        group_id = find_gather_group_id_by_name(page, base_url, name)
        if not group_id:
            log("ERROR", "create_group", name,
                f"Group not found in list after creation (post-submit URL: {post_submit_url})")
            screenshot(page, f"group_create_notfound_{name[:20]}")
            return None
        log("INFO", "create_group", f"Created: {name} (id={group_id})")
        return group_id

    except Exception as e:
        screenshot(page, f"group_create_exc_{name[:20]}")
        log("ERROR", "create_group", name, str(e))
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
    sign_in_url = f"{base_url}/people/users/sign-in"
    for attempt in range(1, 4):
        try:
            page.goto(sign_in_url, wait_until="load")
            break
        except Exception as e:
            if attempt == 3:
                raise
            log("WARN", "login", f"page.goto failed (attempt {attempt}/3): {e}; retrying in 3s")
            time.sleep(3)
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


def launch_browser(pw, headless: bool = True) -> BrowserContext:
    """Launch Chrome in a fresh temporary profile via launch_persistent_context.

    Uses system Chrome (channel="chrome") to avoid CDN TLS-fingerprint blocking.
    The temporary user_data_dir prevents profile-lock conflicts with an already-
    running Chrome instance. Falls back to bundled Chromium on Linux CI.
    Returns a BrowserContext; callers use context.new_page() directly.
    """
    tmp_profile = tempfile.mkdtemp(prefix="pw_chrome_")
    atexit.register(shutil.rmtree, tmp_profile, ignore_errors=True)

    try:
        return pw.chromium.launch_persistent_context(
            tmp_profile, channel="chrome", headless=headless,
        )
    except Exception as e:
        log("WARN", "launch_browser",
            f"system Chrome unavailable ({e}); falling back to bundled Chromium")

    return pw.chromium.launch_persistent_context(
        tmp_profile, headless=headless, args=["--no-sandbox"],
    )


# ── Sheet fetching ────────────────────────────────────────────────────────────

def to_csv_export_url(url: str) -> str:
    """Convert a Google Sheets edit URL to a CSV export URL.

    URLs that already contain /export? (e.g. ?format=tsv or ?format=csv) are
    returned unchanged so their format and gid parameters are preserved.
    """
    if "/export?" in url:
        return url
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
    email: str = ""


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
    list_domain: Optional[str] = None


# ── Gather I/O ────────────────────────────────────────────────────────────────

def fetch_all_gather_users(page: Page, base_url: str) -> list[GatherUser]:
    """Download all users from the Gather directory CSV export."""
    csv_text: str = page.evaluate(
        "async (url) => { const r = await fetch(url); return await r.text(); }",
        f"{base_url}/users.csv",
    )
    reader = csv.DictReader(io.StringIO(csv_text))
    users: list[GatherUser] = []
    for row in reader:
        uid = row.get("ID", "").strip()
        first = row.get("First Name", "").strip()
        last = row.get("Last Name", "").strip()
        is_child = row.get("Is Child", "").strip().lower() == "true"
        email = (row.get("Email", "") or row.get("Email Address", "")).strip().lower()
        if not uid:
            continue
        users.append(GatherUser(
            user_id=uid,
            first_name=first,
            last_name=last,
            full_name=f"{first} {last}".strip(),
            child=is_child,
            email=email,
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

    # The local part (list_name, above) says nothing about which domain the
    # list actually lives on — that's a separate <select>. Read its
    # currently-selected option's label (the domain text itself, e.g.
    # "berkeleymoshav.org" — matches how set_gather_group_email_list()
    # selects it) so callers can catch a group whose mailing list is
    # configured on a domain other than the one they assume.
    domain_select_el = page.locator('#groups_group_mailman_list_attributes_domain_id')
    list_domain: Optional[str] = None
    if domain_select_el.count() > 0:
        text = domain_select_el.evaluate("el => el.options[el.selectedIndex]?.text || ''").strip()
        list_domain = text or None

    return GatherGroup(group_id=group.group_id, name=group.name,
                       kind=kind, availability=availability,
                       description=description, members=members,
                       list_name=list_name, list_domain=list_domain)


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
