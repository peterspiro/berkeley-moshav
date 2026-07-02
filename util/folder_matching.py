"""
Shared name-matching helpers for associating Gather groups with Google
Drive folders (and, by the same logic, with existing Google Groups).

Matching is deliberately loose in one direction (folder/candidate name may
have extra words — a suffix like "Circle"/"Working Group", an acronym in
parens, etc.) but anchored on the group's own name as the "base" that must
appear as a prefix or abbreviation of the candidate.
"""

import re

from util.google_group_utils import normalize_ampersand, strip_parens

MATCH_SUFFIXES = ["working group", "circle", "pod"]
ABBREV_STOP_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
MIN_TERM_LENGTH = 3


def to_match_base(name: str) -> str:
    base = normalize_ampersand(strip_parens(name)).lower().strip()
    for suffix in MATCH_SUFFIXES:
        if re.search(r"\b" + re.escape(suffix) + r"\s*$", base):
            base = re.sub(r"\b" + re.escape(suffix) + r"\s*$", "", base).strip()
            break
    return base


def folder_abbreviation(folder_name: str) -> str:
    words = re.findall(r"[a-z0-9]+", to_match_base(folder_name))
    return "".join(w[0] for w in words if w not in ABBREV_STOP_WORDS)


def singularize(base: str) -> str:
    return re.sub(
        r"[a-z0-9]+",
        lambda m: m.group()[:-1] if m.group().endswith("s") and len(m.group()) > 2 else m.group(),
        base,
    )


def concat(base: str) -> str:
    return re.sub(r"[^a-z0-9]", "", base)


def folder_matches_group(group_name: str, folder_name: str) -> bool:
    """True if folder_name is a strict prefix/abbreviation match for
    group_name (e.g. group 'DFL' vs. folder 'Development, Finance, and
    Legal Circle'). Despite the parameter names this is a generic
    two-name comparison — also used to match a group's name against
    existing Google Groups' display names."""
    group_base = singularize(to_match_base(group_name))
    folder_base = singularize(to_match_base(folder_name))
    if folder_base.startswith(group_base) or concat(folder_base).startswith(concat(group_base)):
        return True
    if group_base == folder_abbreviation(folder_name):
        return True
    return False


def significant_words(base: str) -> set[str]:
    """Words worth matching on: no stop words, no very short/common words."""
    return {
        w for w in re.findall(r"[a-z0-9]+", base)
        if w not in ABBREV_STOP_WORDS and len(w) >= MIN_TERM_LENGTH
    }


def shares_significant_term(group_name: str, folder_name: str) -> bool:
    """True if the group and folder names share at least one significant
    word, e.g. group 'Landscape' vs. folder 'Landscape & Grounds Photos'."""
    group_words = significant_words(singularize(to_match_base(group_name)))
    folder_words = significant_words(singularize(to_match_base(folder_name)))
    return bool(group_words & folder_words)


def find_matching_folders(group_name: str, folders: list[dict]) -> list[dict]:
    """Return candidate folders, each tagged with how it matched.

    Folders satisfying the strict prefix/abbreviation rule
    (folder_matches_group) are tagged "strict"; folders that merely share
    one significant word with the group name are tagged "term" — a weaker
    signal meant to widen the candidate list for human review.
    """
    matches = []
    for f in folders:
        if folder_matches_group(group_name, f["name"]):
            matches.append({**f, "match_kind": "strict"})
        elif shares_significant_term(group_name, f["name"]):
            matches.append({**f, "match_kind": "term"})
    return matches
