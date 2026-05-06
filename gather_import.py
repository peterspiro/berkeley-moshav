#!/usr/bin/env python3
"""
Bulk-imports community members into a Gather instance via browser automation.

Usage:
    python gather_import.py \
        --tsv members.tsv \
        --base-url http://foo.gatherdev.org:3000 \
        --email admin@example.com \
        --password adminpassword \
        [--dry-run]
"""

import argparse
import csv
import datetime
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright, TimeoutError as PlaywrightTimeout

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
    """
    Open a Select2 dropdown (identified by the hidden <select> or id selector),
    type a search string, and click the first matching result.
    """
    # Click the Select2 container that wraps this select element
    select_el = page.locator(field_selector).first
    # Select2 replaces the <select> with a sibling .select2-container
    # We can click the container that shares the same parent
    container = page.locator(
        f"{field_selector} ~ .select2-container, "
        f"{field_selector} + .select2-container"
    )
    if container.count() > 0:
        container.first.click()
    else:
        # Fallback: find the nearest select2 container to the element
        select_el.evaluate("el => el.nextElementSibling && el.nextElementSibling.click()")
        # Or just click any open select2 container
        page.locator(".select2-container").last.click()

    # Type into the search field that appears in the dropdown
    search_input = page.locator(".select2-search__field").last
    search_input.wait_for(state="visible", timeout=5000)
    search_input.fill("")
    search_input.type(search_text)

    # Wait for and click the first matching result
    option = page.locator(f".select2-results__option").filter(has_text=search_text).first
    option.wait_for(state="visible", timeout=8000)
    option.click()


# ── Auth ──────────────────────────────────────────────────────────────────────

def login(page: Page, base_url: str, email: str, password: str):
    sign_in_url = f"{base_url}/people/users/sign-in"
    page.goto(sign_in_url, wait_until="networkidle")

    # If already signed in, we'll land on a redirect — check we're on sign-in page
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


# ── Household operations ──────────────────────────────────────────────────────

def _search_list_page(page: Page, base_url: str, path: str, query: str) -> bool:
    """Navigate to a list page, perform a search, and return whether query text appears."""
    page.goto(f"{base_url}/{path}", wait_until="networkidle")
    search = page.locator('input[name="search"]').first
    search.fill(query)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    return query in page.inner_text("body")


def find_existing_household(page: Page, base_url: str, name: str) -> bool:
    """Return True if a household with this exact name already exists."""
    return _search_list_page(page, base_url, "households", name)


def create_household(page: Page, base_url: str, household: dict, dry_run: bool) -> bool:
    """
    Navigate to /households/new, fill in the form, and submit.
    Returns True on success.
    """
    name = household["household_name"]
    unit_num = household.get("unit_num")
    unit_suffix = household.get("unit_suffix")

    unit_and_suffix = ""
    if unit_num is not None:
        unit_and_suffix = str(unit_num)
        if unit_suffix:
            unit_and_suffix += f"-{unit_suffix}"

    if dry_run:
        log("DRY-RUN", "create_household", f"{name} (unit {unit_and_suffix or 'none'})")
        return True

    try:
        page.goto(f"{base_url}/households/new", wait_until="networkidle")
        page.fill('input[name="household[name]"]', name)
        if unit_and_suffix:
            page.fill('input[name="household[unit_num_and_suffix]"]', unit_and_suffix)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Check for errors
        if page.locator(".error, #error_explanation, .alert-danger").count() > 0:
            error = page.locator(".error, #error_explanation, .alert-danger").first.inner_text()
            screenshot(page, f"hh_error_{name[:20]}")
            log("ERROR", "create_household", name, error[:200])
            return False

        log("INFO", "create_household", f"Created: {name}")
        return True

    except Exception as e:
        screenshot(page, f"hh_exception_{name[:20]}")
        log("ERROR", "create_household", name, str(e))
        return False


# ── User operations ───────────────────────────────────────────────────────────

def find_existing_user(page: Page, base_url: str, member: dict) -> bool:
    """Return True if the user already exists (by email for adults, by name for kids)."""
    if member.get("email"):
        return _search_list_page(page, base_url, "users", member["email"])
    else:
        full_name = f"{member['first_name']} {member['last_name']}".strip()
        return _search_list_page(page, base_url, "users", full_name)


def find_guardian_for(page: Page, base_url: str, household: dict) -> str | None:
    """Return the full name of the first adult in the household, for use as guardian."""
    for member in household["members"]:
        if not member.get("child"):
            return f"{member['first_name']} {member['last_name']}".strip()
    return None


def create_user(page: Page, base_url: str, member: dict,
                household_name: str, guardian_name: str | None,
                dry_run: bool) -> bool:
    """
    Navigate to /users/new, fill the form, and submit.
    Returns True on success.
    """
    full_name = f"{member['first_name']} {member['last_name']}".strip()
    is_child = member.get("child", False)

    if dry_run:
        log("DRY-RUN", "create_user",
            f"{full_name} ({'child' if is_child else 'adult'}) in {household_name}")
        return True

    try:
        page.goto(f"{base_url}/users/new", wait_until="networkidle")

        # For new users, household_by_id hidden field is already "true"
        # (the form shows the household selector for new records at the top)

        # Select household via Select2
        household_select = page.locator('select[name="user[household_id]"]')
        if household_select.count() > 0:
            select2_choose(page, 'select[name="user[household_id]"]', household_name)
        else:
            log("WARN", "create_user", full_name, "No household_id select found")

        page.wait_for_load_state("networkidle")

        # Fill basic fields
        page.fill('input[name="user[first_name]"]', member["first_name"])
        page.fill('input[name="user[last_name]"]', member["last_name"])

        if member.get("email"):
            page.fill('input[name="user[email]"]', member["email"])

        if member.get("phone"):
            page.fill('input[name="user[mobile_phone]"]', member["phone"])

        # Child / full_access checkboxes
        if is_child:
            # Use #user_child to avoid matching the hidden input of the same name
            child_cb = page.locator('input[type="checkbox"]#user_child')
            if child_cb.count() > 0 and not child_cb.is_checked():
                child_cb.click()
            # Wait for guardian field to appear (JS-driven)
            page.wait_for_timeout(800)

            full_access_cb = page.locator('input[type="checkbox"]#user_full_access')
            if full_access_cb.count() > 0 and full_access_cb.is_checked():
                full_access_cb.click()

            # Select guardian via Select2 widget
            if guardian_name:
                # Wait for the guardian field to become visible
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

        # Check for errors
        errors = page.locator(".error, #error_explanation, .alert-danger")
        if errors.count() > 0:
            error_text = errors.first.inner_text()
            # Treat duplicate email as "already exists" — idempotent skip
            page_text = page.inner_text("body")
            if "already been taken" in page_text.lower():
                log("INFO", "create_user", f"Already exists (duplicate email), skipping: {full_name}")
                return True
            screenshot(page, f"user_error_{full_name[:20].replace(' ', '_')}")
            log("ERROR", "create_user", full_name, error_text[:200])
            return False

        log("INFO", "create_user", f"Created: {full_name} in {household_name}")
        return True

    except Exception as e:
        screenshot(page, f"user_exception_{full_name[:20].replace(' ', '_')}")
        log("ERROR", "create_user", full_name, str(e))
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main(tsv_path: str, base_url: str, email: str, password: str, dry_run: bool = False):
    base_url = base_url.rstrip("/")
    init_log()

    log("INFO", "start", f"tsv={tsv_path} base_url={base_url} dry_run={dry_run}")

    households = preprocess(tsv_path)
    log("INFO", "preprocess", f"{len(households)} households parsed")

    with sync_playwright() as pw:
        chrome_path = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        import os
        launch_kwargs = {"args": ["--no-sandbox"]}
        if os.path.exists(chrome_path):
            launch_kwargs["executable_path"] = chrome_path
        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        page = context.new_page()

        try:
            login(page, base_url, email, password)
        except RuntimeError as e:
            log("ERROR", "login", str(e))
            close_log()
            browser.close()
            sys.exit(1)

        stats = {"hh_created": 0, "hh_skipped": 0, "hh_failed": 0,
                 "user_created": 0, "user_skipped": 0, "user_failed": 0}

        for household in households:
            hh_name = household["household_name"]

            # ── Household ──
            exists = find_existing_household(page, base_url, hh_name) if not dry_run else False
            if exists:
                log("INFO", "household", f"Already exists, skipping: {hh_name}")
                stats["hh_skipped"] += 1
            else:
                ok = create_household(page, base_url, household, dry_run)
                if ok:
                    stats["hh_created"] += 1
                else:
                    stats["hh_failed"] += 1
                    log("WARN", "household", f"Skipping members of failed household: {hh_name}")
                    continue

            # ── Adults first, then children ──
            adults = [m for m in household["members"] if not m.get("child")]
            children = [m for m in household["members"] if m.get("child")]
            guardian_name = find_guardian_for(page, base_url, household)

            for member in adults + children:
                full_name = f"{member['first_name']} {member['last_name']}".strip()
                exists = find_existing_user(page, base_url, member) if not dry_run else False
                if exists:
                    log("INFO", "user", f"Already exists, skipping: {full_name}")
                    stats["user_skipped"] += 1
                    continue

                ok = create_user(page, base_url, member, hh_name, guardian_name, dry_run)
                if ok:
                    stats["user_created"] += 1
                else:
                    stats["user_failed"] += 1

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
