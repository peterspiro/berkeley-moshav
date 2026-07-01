"""
Shared helpers for reading and modifying Gather's /gdrive/* pages — the
mapping between linked Google Drive items (folders) and the Gather groups
granted access to them.
"""

import json
import re
from pathlib import Path

from util.gather_utils import _check_submit_errors, log, screenshot, select2_choose

GDRIVE_MAP_FILE = Path(__file__).parent.parent / "gdrive_item_map.json"


# ── Persisted google_file_id → Gather item_id cache ───────────────────────────

def load_gdrive_item_map() -> dict[str, str]:
    """Load the persisted google_file_id → numeric item_id mapping."""
    if GDRIVE_MAP_FILE.exists():
        try:
            return json.loads(GDRIVE_MAP_FILE.read_text())
        except Exception:
            pass
    return {}


def save_gdrive_item_map(mapping: dict[str, str]) -> None:
    """Persist the google_file_id → numeric item_id mapping to disk."""
    GDRIVE_MAP_FILE.write_text(json.dumps(mapping, indent=2, sort_keys=True))


# ── /gdrive/config scraping ────────────────────────────────────────────────────

def scrape_gdrive_config(page, base_url: str) -> list[dict]:
    """
    Navigate to /gdrive/config and return a list of entries for the Folders
    section only (not Shared Drives), each a dict with:
      folder_name – display name of the folder (plain text on page)
      group_id    – Gather group ID associated with this folder
      group_name  – Gather group name
      item_id     – Gather's internal numeric ID for this linked item, or
                    None if no item-groups/new link was found in the row

    The page has a single <table> with <tr class="heading"> rows separating
    sections (Shared Drives / Folders / Files).  Each data row has the folder
    name as plain text in the first <td> and a /groups/{id} link in the
    third <td>.
    """
    page.goto(f"{base_url}/gdrive/config", wait_until="networkidle")

    entries = []
    in_folders_section = False

    for row in page.locator("table tbody tr").all():
        # Section heading row — track which section we're in
        heading = row.locator("h2")
        if heading.count() > 0:
            in_folders_section = heading.first.inner_text().strip() == "Folders"
            continue

        if not in_folders_section:
            continue

        # Group link — rows without one are header/empty rows
        group_link = row.locator("a[href*='/groups/']")
        if group_link.count() == 0:
            continue
        group_href = group_link.first.get_attribute("href") or ""
        gm = re.search(r"/groups/(\d+)", group_href)
        if not gm:
            continue
        group_id = gm.group(1)
        group_name = group_link.first.inner_text().strip()

        # Folder name is plain text in the first <td>
        cells = row.locator("td").all()
        if not cells:
            continue
        folder_name = cells[0].inner_text().strip()
        if not folder_name:
            continue

        item_id = None
        item_link = row.locator('a[href*="item-groups/new"]')
        if item_link.count() > 0:
            item_href = item_link.first.get_attribute("href") or ""
            im = re.search(r"item_id=(\d+)", item_href)
            if im:
                item_id = im.group(1)

        entries.append(dict(
            folder_name=folder_name,
            group_id=group_id,
            group_name=group_name,
            item_id=item_id,
        ))

    return entries


def gdrive_config_item_ids(page, base_url: str) -> dict[str, str]:
    """Return {item_id: container_text} for every item currently linked on
    /gdrive/config (any section), keyed by the numeric item_id used in
    /gdrive/item-groups/new?item_id=... links. container_text is the
    surrounding row/block text, useful for checking which groups already
    have access to a given item."""
    page.goto(f"{base_url}/gdrive/config", wait_until="networkidle")
    existing: dict[str, str] = {}
    for link in page.locator('a[href*="item-groups/new"]').all():
        href = link.get_attribute("href") or ""
        m = re.search(r"item_id=(\d+)", href)
        if not m:
            continue
        iid = m.group(1)
        container_text = page.evaluate(
            """el => {
                let c = el.parentElement;
                while (c && c !== document.body) {
                    if (c.querySelectorAll('a[href*="item-groups/new"]').length <= 1)
                        return c.textContent;
                    c = c.parentElement;
                }
                return '';
            }""",
            link.element_handle(),
        )
        existing[iid] = container_text
    return existing


def gdrive_item_url(base_url: str, google_file_id: str) -> str:
    """Return Gather's own viewer URL for a linked Drive item.

    This is the actual route Gather exposes (confirmed from a real example:
    /gdrive/item/{google_file_id}) — there is no /gdrive/items/{id}/edit
    route (that 404s), and the numeric item_id used in item-groups/new
    links is a separate internal ID, not the Drive file ID.
    """
    return f"{base_url}/gdrive/item/{google_file_id}"


# ── Linking a Drive folder into /gdrive/config ────────────────────────────────

def create_gdrive_item(page, base_url: str, google_file_id: str, dry_run: bool = False):
    """Attempt to link google_file_id as a new folder item on /gdrive/config.

    Returns (item_id, None) on success, or (None, error_message) if the
    item already exists there (or another submit error occurred).
    """
    short_id = google_file_id[:12]

    if dry_run:
        log("DRY-RUN", "gdrive_link_item", f"would link gdrive folder {google_file_id!r}")
        return None, None

    existing_before = gdrive_config_item_ids(page, base_url)

    page.goto(f"{base_url}/gdrive/items/new", wait_until="networkidle")
    ext_field = page.locator('input[name="gdrive_item[external_id]"]')
    if ext_field.count() == 0:
        screenshot(page, f"gdrive_link_nofield_{short_id}")
        return None, "gdrive_item[external_id] field not found on /gdrive/items/new"

    ext_field.fill(google_file_id)
    page.locator('select[name="gdrive_item[kind]"]').select_option("folder")
    page.locator('input[name="commit"], button[type="submit"]').first.click()
    page.wait_for_load_state("networkidle")

    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"gdrive_link_err_{short_id}")
        return None, err

    existing_after = gdrive_config_item_ids(page, base_url)
    new_ids = set(existing_after) - set(existing_before)
    if not new_ids:
        screenshot(page, f"gdrive_link_noid_{short_id}")
        return None, "Could not find new item_id on /gdrive/config after linking"

    item_id = next(iter(new_ids))
    log("INFO", "gdrive_link_item", f"Linked gdrive folder {google_file_id!r} as item_id={item_id}")
    return item_id, None


def add_group_access_to_gdrive_item(
    page, base_url: str, item_id: str, gather_group_name: str, dry_run: bool = False
) -> bool:
    """Add gather_group_name to the /gdrive item with Content manager access.
    Returns True on success (including if dry_run)."""
    if dry_run:
        log("DRY-RUN", "gdrive_access",
            f"would add {gather_group_name!r} (Content manager) → item_id={item_id}")
        return True

    new_url = f"{base_url}/gdrive/item-groups/new?item_id={item_id}"
    page.goto(new_url, wait_until="networkidle")

    group_sel = page.locator(
        'select[name*="[group_id]"], select[name*="group_id"], select[name*="group"]'
    ).first
    if group_sel.count() == 0:
        fields = [
            el.get_attribute("name")
            for el in page.locator("input[name], select[name]").all()
        ]
        log("WARN", "gdrive_access", gather_group_name,
            f"No group selector found on {new_url} | fields={fields}")
        screenshot(page, f"gdrive_acc_nosel_{item_id}")
        return False

    sel_name = group_sel.get_attribute("name") or ""
    group_options = page.evaluate(
        """name => [...document.querySelectorAll(`select[name="${name}"] option`)]
            .map(o => ({value: o.value, label: o.textContent.trim()}))""",
        sel_name,
    )
    log("DEBUG", "gdrive_access",
        f"group selector={sel_name!r} options[0:5]={group_options[:5]}")

    # Prefer native select_option; fall back to Select2 only if needed.
    has_select2 = page.locator(
        f'select[name="{sel_name}"] ~ .select2-container, '
        f'select[name="{sel_name}"] + .select2-container'
    ).count() > 0
    if has_select2:
        select2_choose(page, f'select[name="{sel_name}"]', gather_group_name)
    else:
        try:
            page.locator(f'select[name="{sel_name}"]').select_option(label=gather_group_name)
        except Exception as e:
            log("WARN", "gdrive_access", gather_group_name,
                f"select_option(label={gather_group_name!r}) failed: {e} | options={group_options}")
            screenshot(page, f"gdrive_acc_grpsel_{item_id}")
            return False

    chosen_group = page.locator(f'select[name="{sel_name}"]').input_value()
    log("DEBUG", "gdrive_access", f"group selected value={chosen_group!r}")

    role_sel = page.locator(
        'select[name*="[access_level]"], select[name*="access_level"], select[name*="access"]'
    )
    if role_sel.count() > 0:
        role_name = role_sel.first.get_attribute("name") or ""
        options = page.evaluate(
            """name => [...document.querySelectorAll(`select[name="${name}"] option`)]
                .map(o => ({value: o.value, label: o.textContent.trim()}))""",
            role_name,
        )
        # Try common value patterns before falling back to first option.
        selected = False
        for val in ("fileOrganizer", "content_manager", "contentmanager", "content manager"):
            try:
                page.locator(f'select[name="{role_name}"]').select_option(val)
                selected = True
                break
            except Exception:
                pass
        if not selected:
            for label in ("Content manager", "Content Manager"):
                try:
                    page.locator(f'select[name="{role_name}"]').select_option(label=label)
                    selected = True
                    break
                except Exception:
                    pass
        if not selected:
            log("WARN", "gdrive_access", gather_group_name,
                f"Could not select 'Content manager'; available options={options}")
        else:
            log("DEBUG", "gdrive_access",
                f"Set access_level for {gather_group_name!r}; options were={options}")
    else:
        log("WARN", "gdrive_access", gather_group_name, "access_level select not found")

    page.locator('input[name="commit"], button[type="submit"]').first.click()
    page.wait_for_load_state("networkidle")

    post_url = page.url
    err = _check_submit_errors(page)
    if err:
        screenshot(page, f"gdrive_acc_err_{item_id}")
        log("ERROR", "gdrive_access", gather_group_name, f"url={post_url} | {err[:200]}")
        return False

    body_preview = page.locator("body").inner_text()[:300]
    log("DEBUG", "gdrive_access",
        f"post-submit url={post_url!r} body={body_preview!r}")

    # Catch silent failures: stayed on form, or server error page.
    if "item-groups/new" in post_url or "something went wrong" in body_preview.lower():
        screenshot(page, f"gdrive_acc_fail_{item_id}")
        log("WARN", "gdrive_access", gather_group_name,
            f"Submit did not succeed | url={post_url!r} body={body_preview!r}")
        return False

    log("INFO", "gdrive_access",
        f"Added {gather_group_name!r} (Content manager) to item_id={item_id}")
    return True


def ensure_gdrive_group_access(
    page, base_url: str, gdrive_href: str, gather_name: str, dry_run: bool = False
) -> bool:
    """Idempotent, cache-tolerant helper: link the Drive folder referenced by
    gdrive_href (a /gdrive/item/{google_file_id} path) if not already linked
    (using the persisted google_file_id → item_id cache to survive re-runs),
    then ensure gather_name has Content manager access to it.

    Unlike create_gdrive_item(), an already-linked folder is treated as
    success (not an error) — this helper is for repeatable bulk imports,
    not for one-off "should this be a new entry" checks.
    """
    google_file_id = gdrive_href.rstrip("/").split("/")[-1]
    short_id = google_file_id[:12]

    try:
        existing = gdrive_config_item_ids(page, base_url)

        item_id: str | None = None
        container_text = ""

        gdrive_map = load_gdrive_item_map()
        if google_file_id in gdrive_map:
            item_id = gdrive_map[google_file_id]
            container_text = existing.get(item_id, "")
            log("INFO", "gdrive_link_item",
                f"Found cached item_id={item_id} for {gather_name!r}")

        if item_id is None:
            if dry_run:
                log("DRY-RUN", "gdrive_link_item",
                    f"would link gdrive folder {google_file_id!r} for {gather_name!r}")
                log("DRY-RUN", "gdrive_access",
                    f"would add {gather_name!r} (Content manager) to linked item")
                return True

            item_id, err = create_gdrive_item(page, base_url, google_file_id, dry_run=False)
            if err:
                log("ERROR", "gdrive_link_item", gather_name,
                    f"Already-taken error and no cached item_id | err={err[:200]}")
                return False
            gdrive_map[google_file_id] = item_id
            save_gdrive_item_map(gdrive_map)

        if gather_name.lower() in container_text.lower():
            log("INFO", "gdrive_access",
                f"{gather_name!r} already linked to item_id={item_id}, skipping")
            return True

        return add_group_access_to_gdrive_item(page, base_url, item_id, gather_name, dry_run)

    except Exception as e:
        screenshot(page, f"gdrive_acc_exc_{short_id}")
        log("ERROR", "gdrive_access", gather_name, str(e))
        return False
