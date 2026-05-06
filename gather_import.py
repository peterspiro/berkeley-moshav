#!/usr/bin/env python3
"""
Bulk-imports community members into a Gather instance via browser automation.

Usage:
    python gather_import.py \
        -t members.tsv \
        -u http://foo.gatherdev.org:3000 \
        -e admin@example.com \
        -p adminpassword \
        [-n]
"""

import argparse
import csv
import datetime
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from preprocess import preprocess


LOG_FILE = "import_log.csv"
SCREENSHOT_DIR = Path("import_screenshots")


# ── Logging ───────────────────────────────────────────────────────────────────

_log_writer = None
_log_file_handle = None


def init_log():
    global _log_writer, _log_file_handle
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    _log_file_handle = open(LOG_FILE, "a", newline="")
    _log_writer = csv.writer(_log_file_handle)
    if Path(LOG_FILE).stat().st_size == 0:
        _log_writer.writerow(["timestamp", "level", "action", "detail", "error"])


def log(level: str, action: str, detail: str = "", error: str = ""):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {level:7s} {action}: {detail}" + (f" — {error}" if error else ""))
    if _log_writer:
        _log_writer.writerow([ts, level, action, detail, error])
        _log_file_handle.flush()


def close_log():
    if _log_file_handle:
        _log_file_handle.close()


# ── Browser helpers ───────────────────────────────────────────────────────────

def screenshot(page: Page, name: str):
    path = SCREENSHOT_DIR / f"{name}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path))
        log("DEBUG", "screenshot", str(path))
    except Exception:
        pass


def select2_choose(page: Page, field_selector: str, search_text: str):
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


def _check_submit_errors(page: Page) -> str | None:
    """Return error text if the form showed validation errors, else None."""
    if page.locator(".error, #error_explanation, .alert-danger").count() > 0:
        return page.locator(".error, #error_explanation, .alert-danger").first.inner_text()
    return None


# ── Auth ──────────────────────────────────────────────────────────────────────

def login(page: Page, base_url: str, email: str, password: str):
    page.goto(f"{base_url}/people/users/sign-in", wait_until="networkidle")
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


# ── Search helpers ────────────────────────────────────────────────────────────

def _search(page: Page, base_url: str, path: str, query: str):
    """Navigate to a list page and submit a search query."""
    page.goto(f"{base_url}/{path}", wait_until="networkidle")
    page.locator('input[name="search"]').first.fill(query)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


def _to_edit_url(base_url: str, href: str | None) -> str | None:
    """Convert a relative or absolute Gather href to an /edit URL."""
    if not href:
        return None
    if href.startswith("http"):
        return f"{href}/edit"
    return f"{base_url}{href}/edit"


def find_household_edit_url(page: Page, base_url: str, name: str) -> str | None:
    """
    Search for a household by exact name.
    Returns its edit URL if found, else None.
    """
    _search(page, base_url, "households", name)
    link = page.locator(f'a:text-is("{name}")').first
    if link.count() == 0:
        return None
    return _to_edit_url(base_url, link.get_attribute("href"))


def find_user_edit_url(page: Page, base_url: str, member: dict) -> str | None:
    """
    Search for a user by full name, then confirm email matches (for adults).
    Returns the user's edit URL if found, else None.
    """
    full_name = f"{member['first_name']} {member['last_name']}".strip()
    expected_email = member.get("email", "").strip().lower()

    _search(page, base_url, "users", full_name)

    # Collect all candidate hrefs before navigating away.
    # Search results show user name links, not "Profile" links.
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
            # Visit the edit form to read the email field directly
            page.goto(edit_url, wait_until="networkidle")
            email_input = page.locator('input[name="user[email]"]')
            if email_input.count() == 0:
                continue
            actual_email = email_input.input_value().strip().lower()
            if actual_email != expected_email:
                continue

        return edit_url

    return None


# ── Household operations ──────────────────────────────────────────────────────

def _household_unit_str(household: dict) -> str:
    unit_num = household.get("unit_num")
    unit_suffix = household.get("unit_suffix")
    if unit_num is None:
        return ""
    s = str(unit_num)
    if unit_suffix:
        s += f"-{unit_suffix}"
    return s


def create_household(page: Page, base_url: str, household: dict, dry_run: bool) -> bool:
    name = household["household_name"]
    unit = _household_unit_str(household)

    if dry_run:
        log("DRY-RUN", "create_household", f"{name} (unit {unit or 'none'})")
        return True

    try:
        page.goto(f"{base_url}/households/new", wait_until="networkidle")
        page.fill('input[name="household[name]"]', name)
        if unit:
            page.fill('input[name="household[unit_num_and_suffix]"]', unit)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"hh_error_{name[:20]}")
            log("ERROR", "create_household", name, err[:200])
            return False

        log("INFO", "create_household", f"Created: {name}")
        return True

    except Exception as e:
        screenshot(page, f"hh_exception_{name[:20]}")
        log("ERROR", "create_household", name, str(e))
        return False


def update_household(page: Page, edit_url: str, household: dict, dry_run: bool) -> str:
    """Navigate to the household edit page, compare fields, and update if changed.
    Returns 'skipped', 'updated', or 'failed'."""
    name = household["household_name"]
    desired_unit = _household_unit_str(household)

    try:
        page.goto(edit_url, wait_until="networkidle")
        current_unit = page.locator('input[name="household[unit_num_and_suffix]"]').input_value().strip()

        if current_unit == desired_unit:
            log("INFO", "household", f"Up to date, skipping: {name}")
            return "skipped"

        if dry_run:
            log("DRY-RUN", "update_household",
                f"{name}: unit {current_unit!r} → {desired_unit!r}")
            return "skipped"

        page.fill('input[name="household[unit_num_and_suffix]"]', desired_unit)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"hh_update_error_{name[:20]}")
            log("ERROR", "update_household", name, err[:200])
            return "failed"

        log("INFO", "update_household", f"Updated: {name} unit {current_unit!r} → {desired_unit!r}")
        return "updated"

    except Exception as e:
        screenshot(page, f"hh_update_exception_{name[:20]}")
        log("ERROR", "update_household", name, str(e))
        return "failed"


# ── User operations ───────────────────────────────────────────────────────────

def find_guardian_for(household: dict) -> str | None:
    for member in household["members"]:
        if not member.get("child"):
            return f"{member['first_name']} {member['last_name']}".strip()
    return None


def create_user(page: Page, base_url: str, member: dict,
                household_name: str, guardian_name: str | None,
                dry_run: bool) -> bool:
    full_name = f"{member['first_name']} {member['last_name']}".strip()
    is_child = member.get("child", False)

    if dry_run:
        log("DRY-RUN", "create_user",
            f"{full_name} ({'child' if is_child else 'adult'}) in {household_name}")
        return True

    try:
        page.goto(f"{base_url}/users/new", wait_until="networkidle")

        household_select = page.locator('select[name="user[household_id]"]')
        if household_select.count() > 0:
            select2_choose(page, 'select[name="user[household_id]"]', household_name)
        else:
            log("WARN", "create_user", full_name, "No household_id select found")

        page.wait_for_load_state("networkidle")
        page.fill('input[name="user[first_name]"]', member["first_name"])
        page.fill('input[name="user[last_name]"]', member["last_name"])

        if member.get("email"):
            page.fill('input[name="user[email]"]', member["email"])
        if member.get("phone"):
            page.fill('input[name="user[mobile_phone]"]', member["phone"])

        if is_child:
            child_cb = page.locator('input[type="checkbox"]#user_child')
            if child_cb.count() > 0 and not child_cb.is_checked():
                child_cb.click()
            page.wait_for_timeout(800)

            full_access_cb = page.locator('input[type="checkbox"]#user_full_access')
            if full_access_cb.count() > 0 and full_access_cb.is_checked():
                full_access_cb.click()

            if guardian_name:
                page.wait_for_timeout(500)
                guardian_select = page.locator(
                    '[id^="user_up_guardianships_attributes_"][id$="_guardian_id"]'
                )
                if guardian_select.count() > 0:
                    select2_choose(page,
                        '[id^="user_up_guardianships_attributes_"][id$="_guardian_id"]',
                        guardian_name)
                else:
                    log("WARN", "create_user", full_name,
                        "Guardian select not found — child may fail validation")

        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"user_error_{full_name[:20].replace(' ', '_')}")
            log("ERROR", "create_user", full_name, err[:200])
            return False

        log("INFO", "create_user", f"Created: {full_name} in {household_name}")
        return True

    except Exception as e:
        screenshot(page, f"user_exception_{full_name[:20].replace(' ', '_')}")
        log("ERROR", "create_user", full_name, str(e))
        return False


def _normalize_phone(s: str) -> str:
    """Strip formatting and US country code for comparison."""
    digits = "".join(c for c in s if c.isdigit())
    # Treat 11-digit US numbers (1XXXXXXXXXX) same as 10-digit (XXXXXXXXXX)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def update_user(page: Page, edit_url: str, member: dict, dry_run: bool) -> str:
    """Navigate to the user edit page, compare fields, and update any that differ.
    Returns 'skipped', 'updated', or 'failed'."""
    full_name = f"{member['first_name']} {member['last_name']}".strip()

    desired = {
        "user[first_name]": member.get("first_name", "").strip(),
        "user[last_name]": member.get("last_name", "").strip(),
        "user[mobile_phone]": member.get("phone", "").strip(),
    }

    try:
        page.goto(edit_url, wait_until="networkidle")

        changes = {}
        for field, desired_val in desired.items():
            current = page.locator(f'input[name="{field}"]').input_value().strip()
            # Phone fields: compare digits only to ignore formatting differences
            if "_phone" in field:
                if _normalize_phone(current) != _normalize_phone(desired_val):
                    changes[field] = (current, desired_val)
            elif current != desired_val:
                changes[field] = (current, desired_val)

        if not changes:
            log("INFO", "user", f"Up to date, skipping: {full_name}")
            return "skipped"

        change_desc = ", ".join(
            f"{k.split('[')[1].rstrip(']')}: {v[0]!r} → {v[1]!r}"
            for k, v in changes.items()
        )

        if dry_run:
            log("DRY-RUN", "update_user", f"{full_name}: {change_desc}")
            return "skipped"

        for field, (_, desired_val) in changes.items():
            page.fill(f'input[name="{field}"]', desired_val)

        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        err = _check_submit_errors(page)
        if err:
            screenshot(page, f"user_update_error_{full_name[:20].replace(' ', '_')}")
            log("ERROR", "update_user", full_name, err[:200])
            return "failed"

        log("INFO", "update_user", f"Updated: {full_name} — {change_desc}")
        return "updated"

    except Exception as e:
        screenshot(page, f"user_update_exception_{full_name[:20].replace(' ', '_')}")
        log("ERROR", "update_user", full_name, str(e))
        return "failed"


# ── Main ──────────────────────────────────────────────────────────────────────

def main(tsv_path: str, base_url: str, email: str, password: str, dry_run: bool = False):
    base_url = base_url.rstrip("/")
    init_log()

    log("INFO", "start", f"tsv={tsv_path} base_url={base_url} dry_run={dry_run}")

    households = preprocess(tsv_path)
    log("INFO", "preprocess", f"{len(households)} households parsed")

    with sync_playwright() as pw:
        chrome_path = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        launch_kwargs = {"args": ["--no-sandbox"]}
        if os.path.exists(chrome_path):
            launch_kwargs["executable_path"] = chrome_path
        browser = pw.chromium.launch(**launch_kwargs)
        page = browser.new_context().new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            close_log()
            browser.close()
            sys.exit(1)

        stats = {"hh_created": 0, "hh_updated": 0, "hh_skipped": 0, "hh_failed": 0,
                 "user_created": 0, "user_updated": 0, "user_skipped": 0, "user_failed": 0}

        for household in households:
            hh_name = household["household_name"]

            # ── Household ──
            edit_url = find_household_edit_url(page, base_url, hh_name) if not dry_run else None
            if edit_url:
                result = update_household(page, edit_url, household, dry_run)
                stats[f"hh_{result}"] += 1
                if result == "failed":
                    log("WARN", "household", f"Skipping members of failed household: {hh_name}")
                    continue
            else:
                ok = create_household(page, base_url, household, dry_run)
                stats["hh_created" if ok else "hh_failed"] += 1
                if not ok:
                    log("WARN", "household", f"Skipping members of failed household: {hh_name}")
                    continue

            # ── Adults first, then children ──
            adults = [m for m in household["members"] if not m.get("child")]
            children = [m for m in household["members"] if m.get("child")]
            guardian_name = find_guardian_for(household)

            for member in adults + children:
                edit_url = find_user_edit_url(page, base_url, member) if not dry_run else None
                if edit_url:
                    result = update_user(page, edit_url, member, dry_run)
                    stats[f"user_{result}"] += 1
                else:
                    ok = create_user(page, base_url, member, hh_name, guardian_name, dry_run)
                    stats["user_created" if ok else "user_failed"] += 1

        browser.close()

    log("INFO", "done", str(stats))
    close_log()


def cli():
    parser = argparse.ArgumentParser(description="Bulk-import members into Gather via browser automation")
    parser.add_argument("-t", "--tsv", required=True, help="Path to input TSV file")
    parser.add_argument("-u", "--base-url", required=True,
                        help="Gather base URL, e.g. http://foo.gatherdev.org:3000")
    parser.add_argument("-e", "--email", required=True, help="Admin login email")
    parser.add_argument("-p", "--password", required=True, help="Admin login password")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Log what would happen without making any changes")
    args = parser.parse_args()
    main(args.tsv, args.base_url, args.email, args.password, args.dry_run)


if __name__ == "__main__":
    cli()
