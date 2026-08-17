"""
Shared helpers for reading and modifying Gather's /gdrive/* pages — the
mapping between linked Google Drive items (folders) and the Gather groups
granted access to them.
"""

import json
import re
from collections import deque
from pathlib import Path

from util.gather_utils import _check_submit_errors, log, screenshot, select2_choose
from util.google_group_footer import ITEM_TYPE_FOLDER, ITEM_TYPE_SHARED_DRIVE

GDRIVE_MAP_FILE = Path(__file__).parent.parent / "groups_drive_sync" / "gdrive_item_map.json"


# ── Google Drive API traversal ─────────────────────────────────────────────────

def walk_drive_folders(drive_service, drive_id: str) -> list[dict]:
    """Breadth-first walk of every folder in the Shared Drive, via the Drive
    API (not Gather). Returns a flat list of {"id", "name", "path"} dicts,
    where "path" is the list of ancestor folder names (not including the
    drive root) leading to and including this folder.
    """
    folders: list[dict] = []
    queue = deque([(drive_id, [])])

    while queue:
        parent_id, parent_path = queue.popleft()
        page_token = None
        while True:
            resp = drive_service.files().list(
                corpora="drive",
                driveId=drive_id,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                q=f"mimeType='application/vnd.google-apps.folder' and "
                  f"'{parent_id}' in parents and trashed=false",
                fields="nextPageToken,files(id,name)",
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                path = parent_path + [f["name"]]
                folders.append({"id": f["id"], "name": f["name"], "path": path})
                queue.append((f["id"], path))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return folders


def find_folder_id_under_parent(drive_service, drive_id: str, parent_id: str, name: str) -> str | None:
    """Return the Drive folder ID of the folder called name (exact match)
    directly under parent_id, or None if no such folder exists."""
    resp = drive_service.files().list(
        corpora="drive",
        driveId=drive_id,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        q=f"mimeType='application/vnd.google-apps.folder' and "
          f"'{parent_id}' in parents and trashed=false and name='{name}'",
        fields="files(id,name)",
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def find_top_level_folder_id(drive_service, drive_id: str, name: str) -> str | None:
    """Return the Drive folder ID of the top-level folder called name
    (exact match), or None if no such folder exists."""
    return find_folder_id_under_parent(drive_service, drive_id, drive_id, name)


def ensure_folder_name_available(drive_service, drive_id: str, parent_id: str, name: str) -> None:
    """Raise ValueError if a folder named name already exists directly
    under parent_id, to avoid silently creating a duplicate."""
    existing_id = find_folder_id_under_parent(drive_service, drive_id, parent_id, name)
    if existing_id is not None:
        raise ValueError(f"A folder named {name!r} already exists here (id={existing_id})")


def create_drive_folder(drive_service, name: str, parent_id: str) -> str:
    """Create a new folder named name under parent_id. Returns the new
    folder's Drive ID."""
    resp = drive_service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return resp["id"]


def get_drive_folder_by_id(drive_service, folder_id: str) -> dict | None:
    """Return {"id", "name"} for folder_id, or None if it doesn't exist or
    isn't a folder. Read-only."""
    try:
        resp = drive_service.files().get(
            fileId=folder_id, fields="id,name,mimeType", supportsAllDrives=True,
        ).execute()
    except Exception:
        return None
    if resp.get("mimeType") != "application/vnd.google-apps.folder":
        return None
    return {"id": resp["id"], "name": resp["name"]}


# ── Naming/placement for a newly created group folder ─────────────────────────

_SMALL_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to"}

# Gather group "kind" -> folder name suffix / Shared Drive placement rule.
FOLDER_TYPE_SUFFIXES = {
    "circle": "Circle",
    "committee": "Working Group",
    "club": "Club",
}


def standardize_name(name: str) -> str:
    """Title-case a human-entered name: capitalize each word's first
    letter, except words that are already an acronym (all-uppercase, e.g.
    "CIT", "DF&L") or otherwise mixed-case (e.g. "McTeague"), which are
    left as typed. Small linking words (and, of, the, ...) are lowercased
    unless they're the first or last word, per standard title-case style.
    """
    words = name.strip().split()
    if not words:
        return name.strip()
    last_idx = len(words) - 1
    result = []
    for i, w in enumerate(words):
        if w.isupper() or any(c.isupper() for c in w[1:]):
            result.append(w)
        elif 0 < i < last_idx and w.lower() in _SMALL_WORDS:
            result.append(w.lower())
        else:
            result.append(w[:1].upper() + w[1:].lower())
    return " ".join(result)


def folder_name_for_group_type(raw_name: str, kind: str) -> str:
    """Standardize a user-entered folder name and append the type's suffix
    (e.g. "Working Group" for a committee-kind group) if not already
    present. Raises ValueError for any kind this sync system doesn't know
    how to place in the Shared Drive."""
    if kind not in FOLDER_TYPE_SUFFIXES:
        raise ValueError(f"Don't know how to handle type {kind!r}")
    name = standardize_name(raw_name)
    suffix = FOLDER_TYPE_SUFFIXES[kind]
    if not re.search(r"\b" + re.escape(suffix) + r"\s*$", name, re.IGNORECASE):
        name = f"{name} {suffix}"
    return name


def parent_folder_id_for_group_type(drive_service, drive_id: str, kind: str) -> str:
    """Return the Drive folder ID under which a new folder for a group of
    this kind should be created: the Shared Drive's own root for
    circle/committee, or the top-level "Clubs" folder for club. Raises
    ValueError for any other kind, or if "Clubs" can't be found."""
    if kind in ("circle", "committee"):
        return drive_id
    if kind == "club":
        clubs_id = find_top_level_folder_id(drive_service, drive_id, "Clubs")
        if clubs_id is None:
            raise ValueError('Could not find a top-level "Clubs" folder in the Shared Drive')
        return clubs_id
    raise ValueError(f"Don't know how to handle type {kind!r}")


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

# Maps a /gdrive/config section heading to the item_type it links.
# "Files" (and any other section) is ignored — this sync only cares about
# Drive items whose membership can be mirrored into a Google Group.
_SECTION_ITEM_TYPES = {
    "Shared Drives": ITEM_TYPE_SHARED_DRIVE,
    "Folders": ITEM_TYPE_FOLDER,
}


def scrape_gdrive_config(page, base_url: str) -> list[dict]:
    """
    Navigate to /gdrive/config and return a list of entries for the Shared
    Drives and Folders sections (Files are skipped), each a dict with:
      folder_name    – display name of the folder or shared drive
      group_id       – Gather group ID associated with this item
      group_name     – Gather group name
      item_type      – ITEM_TYPE_FOLDER for a Folders-section row,
                       ITEM_TYPE_SHARED_DRIVE for a Shared Drives-section row
      item_id        – Gather's internal numeric ID for this linked item, or
                       None if no item-groups/new link was found in the row
      google_file_id – the underlying Google Drive ID (a folder ID, or a
                       shared drive's ID), parsed from a /gdrive/item/{id}
                       link on the name, or None if the name isn't rendered
                       as a link

    The page has a single <table> with <tr class="heading"> rows separating
    sections (Shared Drives / Folders / Files).  Each data row has the item
    name in the first <td> (sometimes a plain-text label, sometimes a link
    to /gdrive/item/{google_file_id}) and a /groups/{id} link in the
    third <td>.
    """
    page.goto(f"{base_url}/gdrive/config", wait_until="networkidle")

    entries = []
    section_item_type = None

    for row in page.locator("table tbody tr").all():
        # Section heading row — track which section we're in
        heading = row.locator("h2")
        if heading.count() > 0:
            section_item_type = _SECTION_ITEM_TYPES.get(heading.first.inner_text().strip())
            continue

        if section_item_type is None:
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

        cells = row.locator("td").all()
        if not cells:
            continue
        folder_name = cells[0].inner_text().strip()
        if not folder_name:
            continue

        google_file_id = None
        folder_link = cells[0].locator("a[href*='/gdrive/item/']")
        if folder_link.count() > 0:
            folder_href = folder_link.first.get_attribute("href") or ""
            fm = re.search(r"/gdrive/item/([^/?#]+)", folder_href)
            if fm:
                google_file_id = fm.group(1)

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
            item_type=section_item_type,
            item_id=item_id,
            google_file_id=google_file_id,
        ))

    return entries


def check_single_link_per_group(config_entries: list[dict]) -> None:
    """Raise ValueError if any Gather group is linked to more than one
    Drive item (folder and/or shared drive) on /gdrive/config.

    Membership sync mirrors a single item's permissions into a group, so a
    group tied to two items is ambiguous — fail loudly rather than silently
    pick one. The message lists every offending group and its linked items."""
    by_group: dict[str, list[dict]] = {}
    for entry in config_entries:
        by_group.setdefault(entry["group_id"], []).append(entry)

    offenders = []
    for group_id, group_entries in sorted(by_group.items()):
        if len(group_entries) > 1:
            group_name = group_entries[0].get("group_name") or ""
            items = ", ".join(
                f"{e['folder_name']!r} ({e['item_type']})" for e in group_entries
            )
            offenders.append(f"{group_name!r} (group_id={group_id}) → {items}")

    if offenders:
        raise ValueError(
            "Each Gather group may be linked to at most one Drive folder or "
            "shared drive, but these are linked to several:\n  "
            + "\n  ".join(offenders)
        )


_DUMP_DIR = Path("debug/gdrive_config_row_dumps")


def dump_gdrive_config_row_html(page, base_url: str, folder_name: str) -> Path | None:
    """Write the outerHTML of the /gdrive/config row for folder_name to disk,
    for inspecting the actual markup when automatic resolution fails.
    Returns the file path, or None if no matching row was found."""
    page.goto(f"{base_url}/gdrive/config", wait_until="networkidle")
    for row in page.locator("table tbody tr").all():
        cells = row.locator("td").all()
        if cells and cells[0].inner_text().strip() == folder_name:
            html = row.evaluate("el => el.outerHTML")
            _DUMP_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", folder_name)[:60]
            path = _DUMP_DIR / f"{safe_name}.html"
            path.write_text(html)
            return path
    return None


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


def gdrive_item_url(google_file_id: str) -> str:
    """Return Gather's own viewer path for a linked Drive item, relative
    (no base_url prefix) — matching the convention used for [Members] links
    (/groups/{id}) and for Documents links elsewhere in the codebase
    (import_groups.py's gdrive_href, scraped straight from Gather's own
    anchors, is likewise relative). Keeping this relative avoids every
    existing hierarchy entry looking "changed" on the first run after this
    was introduced, just because of an absolute-vs-relative mismatch.

    This is the actual route Gather exposes (confirmed from a real example:
    /gdrive/item/{google_file_id}) — there is no /gdrive/items/{id}/edit
    route (that 404s), and the numeric item_id used in item-groups/new
    links is a separate internal ID, not the Drive file ID.
    """
    return f"/gdrive/item/{google_file_id}"


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
