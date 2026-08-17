"""
Shared helpers for reading and writing the "Circle Hierarchy" wiki page —
the bullet-list markdown tree of circles/working groups, each optionally
linked to its Gather group ([Members]) and Drive folder ([Documents]).

Line format (4 spaces of indent per nesting depth):
    - Name
    - Name: [Members](/groups/123)
    - Name: [Members](/groups/123) | [Documents](/gdrive/item/456)

The [Members] and [Documents] links are the only parts the script manages.
Any other "| …"-separated segments on a line (extra links or notes added by
hand) are preserved verbatim and re-emitted after the managed links.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from util.gather_utils import _check_submit_errors, _codemirror_get, _codemirror_set, log, screenshot

WIKI_TITLE = "Circle Hierarchy"
WIKI_SLUG = "circle-hierarchy"
# The root line's full name is managed by ensure_root_node() in
# update_groups_in_google_and_hierarchy.py and may change over time, but it
# always contains this substring — used below to locate the block
# regardless of exactly how the rest of the root's name is worded.
HIERARCHY_ROOT = "Top Circle / HOA"

# Matches the hierarchy bullet list anchored on the root label. Matches the
# substring anywhere in the top-level bullet's text (not just as a prefix)
# so a root name like "Full Community (Circle of Everyone / Top Circle /
# HOA)" is still found.
HIERARCHY_BLOCK_RE = re.compile(
    r"(?m)^- [^\n]*" + re.escape(HIERARCHY_ROOT) + r"[^\n]*(?:\n[ \t][^\n]+)*"
)


def apply_hierarchy_content(page_content: str, new_hierarchy: str) -> tuple[Optional[str], str]:
    """Update just the hierarchy bullet list within the page content.

    Finds the existing hierarchy block (anchored on HIERARCHY_ROOT) and
    replaces only that portion, preserving any surrounding content.

    Returns (new_page_content, action) where action is 'skip', 'update', or 'add'.
    """
    block = new_hierarchy.rstrip("\n")
    m = HIERARCHY_BLOCK_RE.search(page_content)
    if m:
        if m.group(0) == block:
            return None, "skip"
        new_page = page_content[: m.start()] + block + page_content[m.end():]
        return new_page, "update"
    base = page_content.strip()
    new_page = (base + "\n\n" if base else "") + block + "\n"
    return new_page, "add"


def fetch_hierarchy_page_content(page, base_url: str) -> str:
    """Read the current Circle Hierarchy wiki page content, or exit with an
    error if it doesn't exist yet."""
    page.goto(f"{base_url}/wiki/{WIKI_SLUG}", wait_until="networkidle")
    page_title = page.title()
    page_exists = (
        "Exception" not in page_title
        and "Error" not in page_title
        and "404" not in page_title
        and page.locator("h1").count() > 0
    )
    if not page_exists:
        sys.exit(
            f"Error: the '{WIKI_SLUG}' wiki page doesn't exist yet. "
            "Run import_groups.py first to create it."
        )

    page.goto(f"{base_url}/wiki/{WIKI_SLUG}/edit", wait_until="networkidle")
    if page.locator(".CodeMirror").count() == 0:
        sys.exit("Error: editor not found on the hierarchy wiki edit page.")
    return _codemirror_get(page)


def ensure_hierarchy_page(page, base_url: str, content: str, dry_run: bool) -> bool:
    """Create or update the Circle Hierarchy wiki page."""
    try:
        page.goto(f"{base_url}/wiki/{WIKI_SLUG}", wait_until="networkidle")
        page_title = page.title()
        page_exists = (
            "Exception" not in page_title
            and "Error" not in page_title
            and "404" not in page_title
            and page.locator("h1").count() > 0
        )
        if page_exists:
            page.goto(f"{base_url}/wiki/{WIKI_SLUG}/edit", wait_until="networkidle")
            if page.locator(".CodeMirror").count() == 0:
                log("ERROR", "update_hierarchy", WIKI_TITLE, "Editor not found on edit page")
                return False
            current = _codemirror_get(page)
            new_page, action = apply_hierarchy_content(current, content)
            log("DEBUG", "hierarchy_action", f"action={action!r}  current_len={len(current)}  new_len={len(content)}")
            if action == "skip":
                log("INFO", "hierarchy_wiki", f"'{WIKI_TITLE}' up to date, skipping")
                return True
            if dry_run:
                log("DRY-RUN", "update_hierarchy", WIKI_TITLE)
                return True
            _codemirror_set(page, new_page)
            page.locator('input[name="commit"]').click()
            page.wait_for_load_state("networkidle")
            err = _check_submit_errors(page)
            if err:
                screenshot(page, "hierarchy_update_err")
                log("ERROR", "update_hierarchy", WIKI_TITLE, err[:200])
                return False
            log("INFO", "update_hierarchy", f"Updated: '{WIKI_TITLE}'")
            return True
        else:
            if dry_run:
                log("DRY-RUN", "create_hierarchy", WIKI_TITLE)
                return True
            page.goto(f"{base_url}/wiki/new", wait_until="networkidle")
            if page.locator("form").count() == 0:
                log("ERROR", "create_hierarchy", WIKI_TITLE, "New wiki page form not found")
                return False
            title_el = page.locator('input[name="wiki_page[title]"], #wiki_page_title')
            if title_el.count() > 0:
                title_el.fill(WIKI_TITLE)
            if page.locator(".CodeMirror").count() == 0:
                log("ERROR", "create_hierarchy", WIKI_TITLE, "Editor not found on new page form")
                return False
            _codemirror_set(page, content)
            page.locator('input[name="commit"]').click()
            page.wait_for_load_state("networkidle")
            err = _check_submit_errors(page)
            if err:
                screenshot(page, "hierarchy_create_err")
                log("ERROR", "create_hierarchy", WIKI_TITLE, err[:200])
                return False
            log("INFO", "create_hierarchy", f"Created: '{WIKI_TITLE}'")
            return True
    except Exception as e:
        screenshot(page, "hierarchy_exc")
        log("ERROR", "hierarchy_wiki", WIKI_TITLE, str(e))
        return False


# ── Hierarchy tree parsing (for update_groups_in_google_and_hierarchy.py) ─────

_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)-\s+(?P<rest>.+)$")
_MEMBERS_RE = re.compile(r"\[Members\]\((?P<url>[^)]+)\)")
_DOCUMENTS_RE = re.compile(r"\[Documents\]\((?P<url>[^)]+)\)")
_GROUP_ID_RE = re.compile(r"/groups/(\d+)")


@dataclass
class HierarchyNode:
    name: str
    group_id: Optional[str] = None
    members_url: str = ""
    documents_url: str = ""
    # Any "| …"-separated segments in the links portion that the script
    # doesn't manage (i.e. aren't the [Members] or [Documents] link) —
    # extra links or notes a human added by hand. Preserved verbatim,
    # in order, and re-emitted after the managed links on render.
    extras: list[str] = field(default_factory=list)
    children: list["HierarchyNode"] = field(default_factory=list)
    parent: Optional["HierarchyNode"] = None


def _parse_rest(rest: str) -> tuple[str, str, str, list[str]]:
    """Split a bullet line's text into
    (name, members_url, documents_url, extras).

    extras holds every "| …"-separated segment of the links portion that
    isn't the [Members] or [Documents] link, kept verbatim and in order so
    unmanaged content a human added by hand survives the parse/render
    round-trip.
    """
    if ": [" in rest:
        name, _, links_part = rest.partition(": ")
    else:
        name, links_part = rest, ""
    members_m = _MEMBERS_RE.search(links_part)
    documents_m = _DOCUMENTS_RE.search(links_part)
    segments = [s.strip() for s in links_part.split("|")] if links_part else []
    extras = [
        s for s in segments
        if s and not _MEMBERS_RE.search(s) and not _DOCUMENTS_RE.search(s)
    ]
    return (
        name.strip(),
        members_m.group("url") if members_m else "",
        documents_m.group("url") if documents_m else "",
        extras,
    )


def parse_hierarchy(markdown: str) -> HierarchyNode:
    """Parse the Circle Hierarchy bullet-list markdown into a tree.

    Builds the tree from raw indentation depth (robust to inconsistent
    spacing) rather than assuming exactly 4 spaces per level.
    """
    stack: list[tuple[int, HierarchyNode]] = []
    root: Optional[HierarchyNode] = None

    for raw in markdown.splitlines():
        if not raw.strip():
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        indent_len = len(m.group("indent"))
        name, members_url, documents_url, extras = _parse_rest(m.group("rest"))
        group_id_m = _GROUP_ID_RE.search(members_url) if members_url else None
        node = HierarchyNode(
            name=name,
            group_id=group_id_m.group(1) if group_id_m else None,
            members_url=members_url,
            documents_url=documents_url,
            extras=extras,
        )

        while stack and stack[-1][0] >= indent_len:
            stack.pop()

        if stack:
            parent = stack[-1][1]
            node.parent = parent
            parent.children.append(node)
        else:
            root = node

        stack.append((indent_len, node))

    if root is None:
        raise ValueError("No hierarchy root line found in wiki content")
    return root


def iter_nodes(root: HierarchyNode):
    """Yield every node in the tree (pre-order), including root."""
    yield root
    for child in root.children:
        yield from iter_nodes(child)


def _render_line(
    name: str, members_url: str, documents_url: str, extras: list[str], depth: int
) -> str:
    pad = "    " * depth
    parts = [name]
    if members_url:
        parts.append(f"[Members]({members_url})")
    if documents_url:
        parts.append(f"[Documents]({documents_url})")
    # Unmanaged segments (extra links, notes) are re-emitted after the
    # managed links so hand-added content isn't dropped on rewrite.
    parts.extend(extras)
    text = parts[0] + ": " + " | ".join(parts[1:]) if len(parts) > 1 else parts[0]
    return f"{pad}- {text}"


def render_hierarchy(root: HierarchyNode) -> str:
    """Render the tree back to markdown, sorting each level's children
    alphabetically (case-insensitive) so insertions land in the right spot
    without any manual bisecting."""
    lines: list[str] = []

    def walk(node: HierarchyNode, depth: int) -> None:
        lines.append(
            _render_line(node.name, node.members_url, node.documents_url, node.extras, depth)
        )
        for child in sorted(node.children, key=lambda c: c.name.casefold()):
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(lines) + "\n"


def find_unique_node_by_prefix(nodes: list[HierarchyNode], prefix: str) -> HierarchyNode:
    """Return the single node whose name starts with `prefix`
    (case-insensitive). Raises ValueError if zero or more than one match."""
    matches = [n for n in nodes if n.name.casefold().startswith(prefix.casefold())]
    if not matches:
        raise ValueError(f"No hierarchy entry found matching prefix {prefix!r}")
    if len(matches) > 1:
        names = ", ".join(repr(n.name) for n in matches)
        raise ValueError(f"Prefix {prefix!r} matches multiple hierarchy entries: {names}")
    return matches[0]


def remove_node(node: HierarchyNode) -> None:
    """Remove node from the tree, re-parenting its children to node.parent
    so the rest of the hierarchy stays connected."""
    parent = node.parent
    if parent is None:
        raise ValueError("Cannot remove the hierarchy root")
    parent.children.remove(node)
    for child in node.children:
        child.parent = parent
        parent.children.append(child)
    node.children = []
