"""
Unit tests for the pure-Python functions in gather_groups.py.

All names, emails, and identifying details are fictional.
"""

import textwrap
from typing import Optional

import pytest

import re

from gather_groups import (
    Circle,
    GatherGroup,
    GatherGroupMember,
    GatherUser,
    GROUP_KINDS,
    _build_wiki_index_content,
    _circle_name_to_list_name,
    _apply_gdrive_link,
    _extract_wiki_url,
    _filter_circles,
    _needs_wiki,
    _parse_wiki_index_entries,
    find_gdrive_link,
    _circle_wiki_slug,
    _group_kind,
    _group_needs_update,
    best_column_match,
    build_description,
    build_wiki_markdown,
    edit_distance,
    expand_acronym,
    find_header_row_index,
    first_name_matches,
    group_names_match,
    match_member,
    parse_member_line,
    parse_sheet,
    resolve_group_members,
    to_csv_export_url,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_circle(
    name="Alpha Circle",
    col_index=0,
    parent_name=None,
    member_lines=None,
    lead_lines=None,
    consultant_text="",
    meetings="",
    description="",
    aim="",
    qualifications="",
):
    return Circle(
        raw_name=name,
        name=name,
        col_index=col_index,
        parent_name=parent_name,
        member_lines=member_lines or [],
        lead_lines=lead_lines or [],
        consultant_text=consultant_text,
        meetings=meetings,
        description=description,
        aim=aim,
        qualifications=qualifications,
    )


def make_user(user_id, first, last):
    return GatherUser(
        user_id=user_id,
        first_name=first,
        last_name=last,
        full_name=f"{first} {last}",
    )


USERS = [
    make_user("1", "Alex", "Green"),
    make_user("2", "Robin", "Blue"),
    make_user("3", "Sam", "Norris"),
    make_user("4", "Taylor", "Norris"),
    make_user("5", "Kathryn", "Smith"),
    make_user("6", "Morgan", "Vale"),
]


# ── CSV for sheet-parsing tests ───────────────────────────────────────────────

SAMPLE_CSV = textwrap.dedent("""\
    Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Description,Aim,Qualifications
    Alpha Circle,,,,"- Alex Green
    - Robin Blue",- Alex,Mondays,Governs all things,To flourish,Must care
    ,Beta Circle,,,"- Sam Norris",- Sam,Tuesdays,Handles sub work,Sub aim,Sub qual
    ,,Gamma,,,"- Taylor Norris",,Leaf node,,
""")

# This CSV has the header row buried below two blank/preamble rows
SAMPLE_CSV_PREAMBLE = textwrap.dedent("""\
    Community Circles Spreadsheet,,,,,,,,
    Updated 2024-01-01,,,,,,,,
    Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Description,Aim,Qualifications
    Alpha Circle,,,,"- Alex Green",- Alex,Mondays,Domain text,Aim text,Qual text
""")


# ── edit_distance ─────────────────────────────────────────────────────────────

class TestEditDistance:
    def test_identical(self):
        assert edit_distance("hello", "hello") == 0

    def test_case_insensitive(self):
        assert edit_distance("Hello", "hello") == 0

    def test_one_insertion(self):
        assert edit_distance("abc", "abcd") == 1

    def test_one_deletion(self):
        assert edit_distance("abcd", "abc") == 1

    def test_one_substitution(self):
        assert edit_distance("abc", "axc") == 1

    def test_completely_different(self):
        assert edit_distance("abc", "xyz") == 3

    def test_empty_strings(self):
        assert edit_distance("", "") == 0
        assert edit_distance("abc", "") == 3
        assert edit_distance("", "abc") == 3

    def test_fuzzy_column_match(self):
        # "Lead Facilitator" is closer to "Lead, Facilitator, Sec." than to "Members"
        assert edit_distance("Lead Facilitator", "Lead, Facilitator, Sec.") < \
               edit_distance("Lead Facilitator", "Members")


# ── expand_acronym ────────────────────────────────────────────────────────────

class TestExpandAcronym:
    def test_clc(self):
        assert expand_acronym("CLC") == "Community Life Circle"

    def test_p_and_g(self):
        assert expand_acronym("P & G") == "Process & Governance"

    def test_d_f_and_l(self):
        assert expand_acronym("D, F, & L") == "Development, Finance, & Legal"

    def test_unknown_passthrough(self):
        assert expand_acronym("Technology") == "Technology"

    def test_strips_whitespace(self):
        assert expand_acronym("  CLC  ") == "Community Life Circle"


# ── group_names_match ─────────────────────────────────────────────────────────

class TestGroupNamesMatch:
    def test_exact_match(self):
        assert group_names_match("Technology", "Technology")

    def test_case_insensitive(self):
        assert group_names_match("technology", "Technology")

    def test_tech_alias(self):
        assert group_names_match("Tech", "Technology")
        assert group_names_match("Technology", "Tech")

    def test_acronym_match(self):
        assert group_names_match("CLC", "Community Life Circle")
        assert group_names_match("Community Life Circle", "CLC")

    def test_no_match(self):
        assert not group_names_match("Finance", "Technology")

    def test_acronym_vs_alias(self):
        assert group_names_match("P & G", "Process & Governance")

    def test_parenthetical_suffix_ignored_in_match(self):
        assert group_names_match("Coordinating Circle (General Circle)", "Coordinating Circle")
        assert group_names_match("Coordinating Circle", "Coordinating Circle (General Circle)")

    def test_truncated_parenthetical_ignored_in_match(self):
        # Gather may truncate the group name, leaving an unclosed paren
        assert group_names_match("Coordinating Circle (General Circle ", "Coordinating Circle")
        assert group_names_match("Coordinating Circle", "Coordinating Circle (General Circle ")

    def test_mid_name_parens_preserved(self):
        # Only trailing parentheticals are stripped, not mid-name ones
        assert not group_names_match("Circle (A) Extra", "Circle Extra")

    def test_circle_suffix_matches(self):
        assert group_names_match("Technology", "Technology Circle")
        assert group_names_match("Technology Circle", "Technology")

    def test_circle_suffix_case_insensitive(self):
        assert group_names_match("technology", "Technology Circle")
        assert group_names_match("Technology Circle", "technology")

    def test_circle_suffix_no_false_match(self):
        assert not group_names_match("Technology", "Finance Circle")

    def test_team_suffix_matches(self):
        assert group_names_match("Membership", "Membership Team")
        assert group_names_match("Membership Team", "Membership")

    def test_team_suffix_case_insensitive(self):
        assert group_names_match("membership", "Membership Team")
        assert group_names_match("Membership Team", "membership")

    def test_team_suffix_no_false_match(self):
        assert not group_names_match("Technology", "Finance Team")

    def test_team_and_parenthetical_matches(self):
        # "Foo Team (description)" should match "Foo": normalization strips the
        # paren, suffix-stripping removes " Team", leaving the same base name.
        assert group_names_match("Membership", "Membership Team (All Members)")
        assert group_names_match("Membership Team (All Members)", "Membership")

    def test_ampersand_equals_and(self):
        assert group_names_match("Finance & Legal", "Finance and Legal")
        assert group_names_match("Finance and Legal", "Finance & Legal")

    def test_commas_ignored(self):
        assert group_names_match("Development Finance Legal", "Development, Finance, Legal")

    def test_ampersand_and_commas_together(self):
        # The motivating example: Gather group vs spreadsheet name
        assert group_names_match(
            "Development, Finance, and Legal Circle (DF&L)",
            "Development, Finance, & Legal",
        )
        assert group_names_match(
            "Development, Finance, & Legal",
            "Development, Finance, and Legal Circle (DF&L)",
        )

    def test_working_group_matches_work_group(self):
        assert group_names_match("Furnishings Work Group", "Furnishings Working Group")
        assert group_names_match("Furnishings Working Group", "Furnishings Work Group")

    def test_working_group_no_false_match(self):
        assert not group_names_match("Furnishings Work Group", "Landscape Working Group")


class TestParseSheetParenStrip:
    def test_trailing_paren_stripped_from_name(self):
        csv_text = textwrap.dedent("""\
            Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Desc,Aim,Qual
            Coordinating Circle (General Circle),,,,,,,,
        """)
        circles = parse_sheet(csv_text)
        assert circles[0].name == "Coordinating Circle"

    def test_name_without_paren_unchanged(self):
        csv_text = textwrap.dedent("""\
            Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Desc,Aim,Qual
            Alpha Circle,,,,,,,,
        """)
        circles = parse_sheet(csv_text)
        assert circles[0].name == "Alpha Circle"


# ── _group_needs_update ───────────────────────────────────────────────────────

class TestGroupNeedsUpdate:
    def _make_group(self, name="Alpha Circle", kind="circle",
                    availability="closed", description="", members=None):
        return GatherGroup(
            group_id="1", name=name, kind=kind,
            availability=availability, description=description,
            members=members or [],
        )

    def test_up_to_date(self):
        g = self._make_group()
        assert not _group_needs_update(g, "circle", "closed", "", [], "Alpha Circle")

    def test_parenthetical_suffix_not_stale(self):
        # Parenthetical suffixes are stripped by group_names_match, so the
        # name alone does not trigger an update.
        g = self._make_group(name="Alpha Circle (Old Name)")
        assert not _group_needs_update(g, "circle", "closed", "", [], "Alpha Circle")

    def test_truncated_paren_not_stale(self):
        g = self._make_group(name="Alpha Circle (Old Name ")
        assert not _group_needs_update(g, "circle", "closed", "", [], "Alpha Circle")

    def test_genuinely_different_name_triggers_update(self):
        g = self._make_group(name="Beta Circle")
        assert _group_needs_update(g, "circle", "closed", "", [], "Alpha Circle")

    def test_circle_suffix_not_stale(self):
        # "Technology Circle" in Gather matches spreadsheet circle "Technology"
        g = self._make_group(name="Technology Circle")
        assert not _group_needs_update(g, "circle", "closed", "", [], "Technology")

    def test_stale_kind_triggers_update(self):
        g = self._make_group(kind="committee")
        assert _group_needs_update(g, "circle", "closed", "", [], "Alpha Circle")


# ── first_name_matches ────────────────────────────────────────────────────────

class TestFirstNameMatches:
    def test_exact(self):
        assert first_name_matches("Alex", "Alex")

    def test_case_insensitive(self):
        assert first_name_matches("alex", "Alex")

    def test_katie_kathryn(self):
        assert first_name_matches("Katie", "Kathryn")
        assert first_name_matches("Kathryn", "Katie")

    def test_no_match(self):
        assert not first_name_matches("Alex", "Robin")

    def test_katie_does_not_match_kate(self):
        assert not first_name_matches("Katie", "Kate")


# ── parse_member_line ─────────────────────────────────────────────────────────

class TestParseMemberLine:
    def test_simple_full_name(self):
        assert parse_member_line("- Alex Green") == [("Alex", "Green")]

    def test_last_initial(self):
        assert parse_member_line("- Alex G.") == [("Alex", "G.")]

    def test_first_name_only(self):
        assert parse_member_line("- Alex") == [("Alex", None)]

    def test_strips_leading_dash(self):
        assert parse_member_line("Alex Green") == [("Alex", "Green")]

    def test_strips_trailing_parens(self):
        assert parse_member_line("- Alex Green (treasurer)") == [("Alex", "Green")]

    def test_first_only_with_parens(self):
        assert parse_member_line("- Alex (treasurer)") == [("Alex", None)]

    def test_slash_shared_last_name(self):
        assert parse_member_line("- Alex/Robin Blue") == [
            ("Alex", "Blue"), ("Robin", "Blue")
        ]

    def test_slash_no_last_name(self):
        assert parse_member_line("- Alex/Robin") == [("Alex", None), ("Robin", None)]

    def test_slash_both_have_last_names(self):
        assert parse_member_line("- Alex Green/Robin Blue") == [
            ("Alex", "Green"), ("Robin", "Blue")
        ]

    def test_slash_with_parens(self):
        assert parse_member_line("- Alex/Robin Blue (note)") == [
            ("Alex", "Blue"), ("Robin", "Blue")
        ]

    def test_empty_line(self):
        assert parse_member_line("") == []

    def test_dash_only(self):
        assert parse_member_line("- ") == []

    def test_three_words_compound_last_name(self):
        # All words after first are treated as last name (supports compound last names)
        assert parse_member_line("- Alex Middle Green") == [("Alex", "Middle Green")]

    def test_mid_string_paren_stripped(self):
        # Paren mid-string (role annotation) truncates at the paren
        assert parse_member_line("- Laura(Lead-CLC) Facilitator") == [("Laura", None)]

    def test_role_lead_stripped(self):
        assert parse_member_line("- Henry -lead") == [("Henry", None)]

    def test_role_facilitator_stripped(self):
        assert parse_member_line("- Stephen-facilitator") == [("Stephen", None)]

    def test_role_secretary_stripped(self):
        assert parse_member_line("- Melissa-Secretary") == [("Melissa", None)]

    def test_role_feedback_link_stripped(self):
        assert parse_member_line("- Sharon-Feedback link") == [("Sharon", None)]

    def test_role_with_last_name(self):
        assert parse_member_line("- Henry Hirschel-lead") == [("Henry", "Hirschel")]

    def test_lead_role_after_name_and_space(self):
        assert parse_member_line("- Anita -Lead") == [("Anita", None)]

    def test_trailing_hyphen_stripped(self):
        assert parse_member_line("- Hilary- ") == [("Hilary", None)]

    def test_trailing_dash_space_stripped(self):
        assert parse_member_line("- Anita -") == [("Anita", None)]

    def test_trailing_space_stripped(self):
        assert parse_member_line("- Annie ") == [("Annie", None)]


# ── first_name_matches ────────────────────────────────────────────────────────

class TestFirstNameAliases:
    def test_annie_matches_ann(self):
        from gather_groups import first_name_matches
        assert first_name_matches("Annie", "Ann")
        assert first_name_matches("Ann", "Annie")

    def test_annie_does_not_match_unrelated(self):
        from gather_groups import first_name_matches
        assert not first_name_matches("Annie", "Alex")

    def test_yocab_matches_yacov(self):
        from gather_groups import first_name_matches
        assert first_name_matches("Yocab", "Yacov")
        assert first_name_matches("Yacov", "Yocab")


# ── find_header_row_index ─────────────────────────────────────────────────────

class TestFindHeaderRowIndex:
    def test_first_row(self):
        rows = [["Circle", "Sub", "Members", "Meetings"]]
        assert find_header_row_index(rows) == 0

    def test_after_preamble(self):
        rows = [
            ["Spreadsheet Title"],
            ["Updated 2024"],
            ["Circle", "Sub", "Members", "Meetings"],
        ]
        assert find_header_row_index(rows) == 2

    def test_not_found(self):
        with pytest.raises(ValueError, match="Header row not found"):
            find_header_row_index([["A", "B", "C"]])

    def test_requires_exact_match(self):
        # "Member" (no 's') should not match
        with pytest.raises(ValueError):
            find_header_row_index([["Circle", "Member", "Meetings"]])


# ── best_column_match ─────────────────────────────────────────────────────────

class TestBestColumnMatch:
    def test_exact_match(self):
        headers = ["Circle", "Consultants", "Members", "Meetings"]
        assert best_column_match(headers, "Members", set()) == 2

    def test_fuzzy_match(self):
        headers = ["Circle", "Consult", "Member", "Mtgs"]
        # "Consult" is closest to "Consultants"
        assert best_column_match(headers, "Consultants", set()) == 1

    def test_respects_used(self):
        headers = ["Circle", "Members", "Members2"]
        # col 1 is best but excluded; col 2 is next
        assert best_column_match(headers, "Members", {1}) == 2

    def test_no_candidates_raises(self):
        with pytest.raises(ValueError):
            best_column_match([], "Members", set())


# ── parse_sheet ───────────────────────────────────────────────────────────────

class TestParseSheet:
    def test_basic_parsing(self):
        circles = parse_sheet(SAMPLE_CSV)
        assert len(circles) == 3
        names = [c.name for c in circles]
        assert "Alpha Circle" in names
        assert "Beta Circle" in names
        assert "Gamma" in names

    def test_col_index(self):
        circles = parse_sheet(SAMPLE_CSV)
        by_name = {c.name: c for c in circles}
        assert by_name["Alpha Circle"].col_index == 0
        assert by_name["Beta Circle"].col_index == 1
        assert by_name["Gamma"].col_index == 2

    def test_parent_detection(self):
        circles = parse_sheet(SAMPLE_CSV)
        by_name = {c.name: c for c in circles}
        assert by_name["Alpha Circle"].parent_name is None
        assert by_name["Beta Circle"].parent_name == "Alpha Circle"
        assert by_name["Gamma"].parent_name == "Beta Circle"

    def test_member_lines_parsed(self):
        circles = parse_sheet(SAMPLE_CSV)
        alpha = next(c for c in circles if c.name == "Alpha Circle")
        assert "- Alex Green" in alpha.member_lines
        assert "- Robin Blue" in alpha.member_lines

    def test_lead_lines_parsed(self):
        circles = parse_sheet(SAMPLE_CSV)
        alpha = next(c for c in circles if c.name == "Alpha Circle")
        assert "- Alex" in alpha.lead_lines

    def test_meetings_parsed(self):
        circles = parse_sheet(SAMPLE_CSV)
        alpha = next(c for c in circles if c.name == "Alpha Circle")
        assert alpha.meetings == "Mondays"

    def test_description_parsed(self):
        circles = parse_sheet(SAMPLE_CSV)
        alpha = next(c for c in circles if c.name == "Alpha Circle")
        assert alpha.description == "Governs all things"

    def test_preamble_rows_skipped(self):
        circles = parse_sheet(SAMPLE_CSV_PREAMBLE)
        assert len(circles) == 1
        assert circles[0].name == "Alpha Circle"

    def test_acronym_expansion(self):
        csv_text = textwrap.dedent("""\
            Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Desc,Aim,Qual
            CLC,,,,,,,,
        """)
        circles = parse_sheet(csv_text)
        assert circles[0].name == "Community Life Circle"

    def test_parent_reset_on_shallower_col(self):
        csv_text = textwrap.dedent("""\
            Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Desc,Aim,Qual
            Alpha,,,,,,,,
            ,Beta,,,,,,,
            ,,Gamma,,,,,,
            ,Delta,,,,,,,
            ,,Epsilon,,,,,,
        """)
        circles = parse_sheet(csv_text)
        by_name = {c.name: c for c in circles}
        assert by_name["Gamma"].parent_name == "Beta"
        assert by_name["Delta"].parent_name == "Alpha"
        assert by_name["Epsilon"].parent_name == "Delta"

    def test_leading_punctuation_stripped_from_name(self):
        csv_text = textwrap.dedent("""\
            Circle,,Sub-circle,Consultants,Members,Lead Facilitator Sec.,Meetings,Desc,Aim,Qual
            Alpha,,,,,,,,
            ,-Rental and Resale,,,,,,,
        """)
        circles = parse_sheet(csv_text)
        names = [c.name for c in circles]
        assert "Rental and Resale" in names
        assert not any(n.startswith("-") for n in names)


# ── build_description ─────────────────────────────────────────────────────────

class TestBuildDescription:
    def test_description_only(self):
        c = make_circle(description="Some text")
        assert build_description(c, "") == "Some text"

    def test_appends_meetings(self):
        c = make_circle(description="Desc", meetings="Tuesdays")
        result = build_description(c, "")
        assert result == "Desc\nMeetings: Tuesdays"

    def test_appends_parent(self):
        c = make_circle(description="Desc", parent_name="Alpha Circle")
        result = build_description(c, "")
        assert "Parent: Alpha Circle" in result

    def test_appends_consultants(self):
        c = make_circle(description="Desc")
        result = build_description(c, "Morgan Vale")
        assert "Consultants: Morgan Vale" in result

    def test_order_consultants_meetings_parent(self):
        c = make_circle(description="D", meetings="Mon", parent_name="Alpha")
        result = build_description(c, "Morgan Vale")
        ci = result.index("Consultants:")
        mi = result.index("Meetings:")
        pi = result.index("Parent:")
        assert ci < mi < pi

    def test_truncates_description_to_fit(self):
        c = make_circle(description="A" * 250, meetings="Tuesdays")
        result = build_description(c, "")
        assert len(result) <= 255
        assert "Meetings: Tuesdays" in result

    def test_skips_line_that_exceeds_limit_even_empty_desc(self):
        c = make_circle(description="", meetings="T" * 300)
        result = build_description(c, "")
        # "\nMeetings: " + 300 chars > 255, so line is omitted
        assert "Meetings:" not in result

    def test_empty_description_no_extras(self):
        c = make_circle(description="")
        assert build_description(c, "") == ""

    def test_max_255_chars(self):
        c = make_circle(
            description="D" * 200,
            meetings="M" * 50,
            parent_name="Alpha",
        )
        result = build_description(c, "C" * 50)
        assert len(result) <= 255

    def test_post_length_never_exceeds_255(self):
        # Description with many newlines: browser \n->\r\n adds extra chars
        c = make_circle(
            description="D" * 100,
            meetings="M" * 80,
            parent_name="P" * 50,
        )
        result = build_description(c, "C" * 40)
        post_len = len(result) + result.count("\n")
        assert post_len <= 255

    def test_post_length_with_newlines_in_description(self):
        # Base description itself contains newlines
        c = make_circle(description="\n".join(["line"] * 60))  # ~299 chars with newlines
        result = build_description(c, "")
        post_len = len(result) + result.count("\n")
        assert post_len <= 255

    def test_wiki_link_appended(self):
        c = make_circle(description="Desc")
        result = build_description(c, "", wiki_url="https://host/wiki/alpha-circle-wiki")
        assert result.endswith('\n<a href="https://host/wiki/alpha-circle-wiki">Wiki</a>')

    def test_wiki_link_is_last(self):
        c = make_circle(description="Desc", meetings="Mondays")
        result = build_description(c, "", wiki_url="https://host/wiki/alpha-circle-wiki")
        assert result.endswith(">Wiki</a>")
        assert result.index("Meetings:") < result.index("Wiki</a>")

    def test_wiki_link_absent_when_url_empty(self):
        c = make_circle(description="Desc")
        result = build_description(c, "", wiki_url="")
        assert "Wiki" not in result

    def test_wiki_link_truncates_base_description(self):
        wiki_url = "https://host/wiki/alpha-circle-wiki"
        wiki_line = f'\n<a href="{wiki_url}">Wiki</a>'
        c = make_circle(description="A" * 255)
        result = build_description(c, "", wiki_url=wiki_url)
        post_len = len(result) + result.count("\n")
        assert post_len <= 255
        assert result.endswith(wiki_line)

    def test_wiki_link_post_length_never_exceeds_255(self):
        c = make_circle(description="D" * 100, meetings="M" * 50)
        result = build_description(c, "", wiki_url="https://host/wiki/alpha-wiki")
        post_len = len(result) + result.count("\n")
        assert post_len <= 255

    def test_wiki_link_fits_with_empty_description(self):
        c = make_circle(description="")
        result = build_description(c, "", wiki_url="https://host/wiki/alpha-wiki")
        assert "Wiki</a>" in result


# ── match_member ──────────────────────────────────────────────────────────────

class TestMatchMember:
    def test_first_and_last(self):
        result = match_member("Alex", "Green", USERS)
        assert len(result) == 1
        assert result[0].user_id == "1"

    def test_last_initial(self):
        result = match_member("Alex", "G.", USERS)
        assert len(result) == 1
        assert result[0].user_id == "1"

    def test_first_only_multiple_matches(self):
        # Sam Norris and Taylor Norris both have last "Norris"
        result = match_member("Sam", None, USERS)
        assert any(u.user_id == "3" for u in result)

    def test_no_match(self):
        result = match_member("Zara", "Unknown", USERS)
        assert result == []

    def test_katie_matches_kathryn(self):
        result = match_member("Katie", "Smith", USERS)
        assert len(result) == 1
        assert result[0].user_id == "5"

    def test_accented_last_name_matches_unaccented(self):
        # Spreadsheet has "Codruta Girlea"; directory has "Codruța Gîrlea"
        users = [make_user("42", "Codruța", "Gîrlea")]
        result = match_member("Codruta", "Girlea", users)
        assert len(result) == 1
        assert result[0].user_id == "42"

    def test_accented_first_name_matches_unaccented(self):
        users = [make_user("43", "Codruța", "Girlea")]
        result = match_member("Codruta", "Girlea", users)
        assert len(result) == 1

    def test_both_accented_match(self):
        users = [make_user("44", "Codruța", "Gîrlea")]
        result = match_member("Codruța", "Gîrlea", users)
        assert len(result) == 1

    def test_kathryn_matches_kathryn(self):
        result = match_member("Kathryn", "Smith", USERS)
        assert len(result) == 1
        assert result[0].user_id == "5"

    def test_last_initial_case_insensitive(self):
        result = match_member("Alex", "g.", USERS)
        assert len(result) == 1
        assert result[0].user_id == "1"


# ── resolve_group_members ─────────────────────────────────────────────────────

class TestResolveGroupMembers:
    def test_basic_members_and_lead(self):
        c = make_circle(
            member_lines=["- Alex Green", "- Robin Blue"],
            lead_lines=["- Alex"],
        )
        members, remaining = resolve_group_members(c, USERS)
        manager_ids = {u.user_id for u, mgr in members if mgr}
        member_ids = {u.user_id for u, mgr in members if not mgr}
        assert "1" in manager_ids
        assert "2" in member_ids
        assert remaining == ""

    def test_unresolved_member_skipped(self):
        c = make_circle(member_lines=["- Zara Unknown"])
        members, _ = resolve_group_members(c, USERS)
        assert members == []

    def test_ambiguous_member_adds_all_and_warns(self):
        # "Sam" with no last name matches both Sam Norris and Sam Vale;
        # both should be added rather than raising an error.
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(member_lines=["- Sam"])
        members, _ = resolve_group_members(c, users_with_two_sams)
        member_ids = {u.user_id for u, _ in members}
        assert "3" in member_ids   # Sam Norris
        assert "99" in member_ids  # Sam Vale

    def test_ambiguous_member_resolved_by_lead_cell(self):
        # "Sam" alone is ambiguous (Norris vs Vale), but lead cell names Sam Norris
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(
            member_lines=["- Sam"],
            lead_lines=["- Sam Norris"],
        )
        members, _ = resolve_group_members(c, users_with_two_sams)
        assert len(members) == 1
        assert members[0][0].last_name == "Norris"

    def test_ambiguous_after_failed_disambiguation_adds_all(self):
        # Both Sams appear in lead lines — can't disambiguate, so both are added
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(
            member_lines=["- Sam"],
            lead_lines=["- Sam Norris", "- Sam Vale"],
        )
        members, _ = resolve_group_members(c, users_with_two_sams)
        member_ids = {u.user_id for u, _ in members}
        assert "3" in member_ids
        assert "99" in member_ids

    def test_ambiguous_resolved_by_existing_membership(self):
        # "Sam" is ambiguous, but Sam Norris (id="3") is already in the group.
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(member_lines=["- Sam"])
        members, _ = resolve_group_members(
            c, users_with_two_sams, existing_member_ids={"3"}
        )
        assert len(members) == 1
        assert members[0][0].user_id == "3"

    def test_existing_membership_not_used_when_unambiguous(self):
        # A unique first-name match should still resolve normally.
        c = make_circle(member_lines=["- Alex Green"])
        members, _ = resolve_group_members(c, USERS, existing_member_ids={"99"})
        assert len(members) == 1
        assert members[0][0].user_id == "1"

    def test_existing_membership_ignored_when_multiple_existing(self):
        # If more than one candidate is already in the group, fall back to adding all.
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(member_lines=["- Sam"])
        members, _ = resolve_group_members(
            c, users_with_two_sams, existing_member_ids={"3", "99"}
        )
        member_ids = {u.user_id for u, _ in members}
        assert "3" in member_ids
        assert "99" in member_ids

    def test_existing_membership_ignored_when_none_in_group(self):
        # If no candidate is already in the group, fall back to adding all.
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(member_lines=["- Sam"])
        members, _ = resolve_group_members(
            c, users_with_two_sams, existing_member_ids=set()
        )
        member_ids = {u.user_id for u, _ in members}
        assert "3" in member_ids
        assert "99" in member_ids

    def test_child_users_excluded_from_matching(self):
        child_user = GatherUser(
            user_id="77", first_name="Luna", last_name="Green",
            full_name="Luna Green", child=True,
        )
        c = make_circle(member_lines=["- Luna Green"])
        members, _ = resolve_group_members(c, USERS + [child_user])
        assert not any(u.user_id == "77" for u, _ in members)

    def test_child_not_matched_even_by_first_name(self):
        child_user = GatherUser(
            user_id="77", first_name="Alex", last_name="Green",
            full_name="Alex Green", child=True,
        )
        # With child excluded, "Alex Green" should still match the adult user "1"
        c = make_circle(member_lines=["- Alex Green"])
        members, _ = resolve_group_members(c, [child_user, make_user("1", "Alex", "Green")])
        assert len(members) == 1
        assert members[0][0].user_id == "1"

    def test_lead_not_in_members_or_gather_raises(self):
        c = make_circle(
            member_lines=["- Alex Green"],
            lead_lines=["- Zara Unknown"],
        )
        with pytest.raises(ValueError, match="not found"):
            resolve_group_members(c, USERS)

    def test_lead_not_in_members_but_in_gather_added(self):
        # Robin Blue is in Gather but not in the Members cell; should be added as manager
        c = make_circle(
            member_lines=["- Alex Green"],
            lead_lines=["- Robin Blue"],
        )
        members, _ = resolve_group_members(c, USERS)
        manager_ids = {u.user_id for u, mgr in members if mgr}
        all_ids = {u.user_id for u, _ in members}
        assert "2" in manager_ids   # Robin Blue is manager
        assert "1" in all_ids       # Alex Green still present

    def test_consultant_found_in_gather_added_to_group(self):
        c = make_circle(
            member_lines=["- Alex Green"],
            consultant_text="- Morgan Vale",
        )
        members, remaining = resolve_group_members(c, USERS)
        member_ids = {u.user_id for u, _ in members}
        assert "6" in member_ids  # Morgan Vale
        assert remaining == ""

    def test_consultant_not_in_gather_stays_in_text(self):
        c = make_circle(
            member_lines=["- Alex Green"],
            consultant_text="- Jordan Unknown",
        )
        members, remaining = resolve_group_members(c, USERS)
        assert "Jordan Unknown" in remaining

    def test_lead_slash_separated_roles_ignored(self):
        # "Alex-Facilitator/Feedback Link" should resolve to first name "Alex"
        c = make_circle(
            member_lines=["- Alex Green"],
            lead_lines=["Alex-Facilitator/Feedback Link"],
        )
        members, _ = resolve_group_members(c, USERS)
        manager_ids = {u.user_id for u, mgr in members if mgr}
        assert "1" in manager_ids

    def test_slash_members_both_added(self):
        c = make_circle(member_lines=["- Alex/Robin Blue"])
        # Robin Blue is user 2; Alex Blue doesn't exist → only Robin matched
        # (Alex Green exists but last is Green, not Blue)
        members, _ = resolve_group_members(c, USERS)
        assert any(u.user_id == "2" for u, _ in members)

    def test_katie_lead_matches_kathryn_member(self):
        c = make_circle(
            member_lines=["- Kathryn Smith"],
            lead_lines=["- Katie"],
        )
        members, _ = resolve_group_members(c, USERS)
        managers = [u for u, mgr in members if mgr]
        assert any(u.user_id == "5" for u in managers)

    def test_no_duplicate_members(self):
        c = make_circle(
            member_lines=["- Alex Green", "- Alex Green"],
        )
        members, _ = resolve_group_members(c, USERS)
        ids = [u.user_id for u, _ in members]
        assert ids.count("1") == 1


# ── build_wiki_markdown ───────────────────────────────────────────────────────

class TestBuildWikiMarkdown:
    def _circles(self):
        alpha = make_circle(
            "Alpha Circle", col_index=0,
            description="Governs", aim="Flourish", qualifications="Care",
        )
        beta = make_circle(
            "Beta Circle", col_index=1, parent_name="Alpha Circle",
            description="Handles sub", aim="Sub aim", qualifications="",
        )
        gamma = make_circle(
            "Gamma", col_index=2, parent_name="Beta Circle",
        )
        return [alpha, beta, gamma]

    def test_title_present(self):
        md = build_wiki_markdown(self._circles(), {})
        assert "# Circle Hierarchy" in md

    def test_root_circle_at_top_level(self):
        md = build_wiki_markdown(self._circles(), {"Alpha Circle": "/groups/1"})
        assert "- [Alpha Circle](/groups/1)" in md

    def test_circle_without_url(self):
        md = build_wiki_markdown(self._circles(), {})
        assert "- Alpha Circle\n" in md or "- Alpha Circle" in md

    def test_domain_aim_qualifications(self):
        md = build_wiki_markdown(self._circles(), {})
        assert "Domain: Governs" in md
        assert "Aim: Flourish" in md
        assert "Qualifications: Care" in md

    def test_empty_qualifications_omitted(self):
        md = build_wiki_markdown(self._circles(), {})
        # Beta Circle has no qualifications
        lines = md.splitlines()
        beta_idx = next(i for i, l in enumerate(lines) if "Beta Circle" in l)
        beta_section = "\n".join(lines[beta_idx:beta_idx + 10])
        assert "Qualifications:" not in beta_section.split("Gamma")[0]

    def test_sub_circles_label(self):
        md = build_wiki_markdown(self._circles(), {})
        assert "Sub-circles:" in md

    def test_hierarchy_indentation(self):
        md = build_wiki_markdown(self._circles(), {})
        lines = md.splitlines()
        alpha_line = next(l for l in lines if "Alpha Circle" in l)
        beta_line = next(l for l in lines if "Beta Circle" in l)
        gamma_line = next(l for l in lines if "Gamma" in l and "Sub-circles" not in l)
        alpha_indent = len(alpha_line) - len(alpha_line.lstrip())
        beta_indent = len(beta_line) - len(beta_line.lstrip())
        gamma_indent = len(gamma_line) - len(gamma_line.lstrip())
        assert alpha_indent < beta_indent < gamma_indent

    def test_multiple_roots(self):
        a = make_circle("Alpha", col_index=0)
        b = make_circle("Beta", col_index=0)
        md = build_wiki_markdown([a, b], {})
        assert "Alpha" in md
        assert "Beta" in md

    def test_circle_with_no_children_has_no_sub_circles_label(self):
        circles = [make_circle("Lone Circle", col_index=0)]
        md = build_wiki_markdown(circles, {})
        assert "Sub-circles:" not in md


# ── to_csv_export_url ─────────────────────────────────────────────────────────

class TestToCsvExportUrl:
    def test_edit_url_converted(self):
        url = "https://docs.google.com/spreadsheets/d/SHEET123/edit?gid=0#gid=0"
        result = to_csv_export_url(url)
        assert "export?format=csv" in result
        assert "SHEET123" in result
        assert "gid=0" in result

    def test_non_google_url_passthrough(self):
        url = "https://example.com/data.csv"
        assert to_csv_export_url(url) == url

    def test_preserves_gid(self):
        url = "https://docs.google.com/spreadsheets/d/ABC/edit#gid=42"
        result = to_csv_export_url(url)
        assert "gid=42" in result


# ── _circle_name_to_list_name ─────────────────────────────────────────────────

class TestCircleNameToListName:
    def test_basic_multi_word(self):
        assert _circle_name_to_list_name("Community Life Circle") == "community-life-circle"

    def test_ampersand_becomes_dash(self):
        assert _circle_name_to_list_name("Process & Governance") == "process-governance"

    def test_comma_and_ampersand(self):
        assert _circle_name_to_list_name("Development, Finance, & Legal") == \
            "development-finance-legal"

    def test_already_lowercase(self):
        assert _circle_name_to_list_name("alpha circle") == "alpha-circle"

    def test_single_word(self):
        assert _circle_name_to_list_name("Technology") == "technology"

    def test_numbers_preserved(self):
        assert _circle_name_to_list_name("Circle 42") == "circle-42"

    def test_truncation_at_50_chars(self):
        result = _circle_name_to_list_name("a" * 60)
        assert result == "a" * 50

    def test_leading_trailing_dash_stripped(self):
        result = _circle_name_to_list_name("& Tech &")
        assert not result.startswith("-")
        assert not result.endswith("-")
        assert result == "tech"

    def test_accented_characters_folded(self):
        assert _circle_name_to_list_name("Café Société") == "cafe-societe"

    def test_all_punctuation_becomes_empty(self):
        assert _circle_name_to_list_name("&&&") == ""


# ── _circle_wiki_slug ─────────────────────────────────────────────────────────

class TestCircleWikiSlug:
    def test_basic(self):
        assert _circle_wiki_slug("Membership") == "membership-wiki"

    def test_multi_word(self):
        assert _circle_wiki_slug("Landscape Work Group") == "landscape-work-group-wiki"

    def test_accents_folded(self):
        assert _circle_wiki_slug("Café") == "cafe-wiki"

    def test_ampersand_becomes_dash(self):
        assert _circle_wiki_slug("Process & Governance") == "process-governance-wiki"

    def test_always_ends_in_wiki(self):
        slug = _circle_wiki_slug("Alpha Circle")
        assert slug.endswith("-wiki")


# ── _group_kind ───────────────────────────────────────────────────────────────

class TestGroupKind:
    def test_circle_by_default(self):
        c = make_circle(name="Membership", col_index=0)
        assert _group_kind(c) == "circle"

    def test_work_group_is_committee(self):
        c = make_circle(name="Landscape Work Group", col_index=0)
        assert _group_kind(c) == "committee"

    def test_sub_circle_is_circle(self):
        c = make_circle(name="Jewish Life Circle", col_index=1)
        assert _group_kind(c) == "circle"

    def test_work_group_not_at_end_is_circle(self):
        c = make_circle(name="Work Group Liaison", col_index=0)
        assert _group_kind(c) == "circle"


# ── _needs_wiki ───────────────────────────────────────────────────────────────

class TestNeedsWiki:
    def test_circle_needs_wiki(self):
        assert _needs_wiki(make_circle(name="Membership"))

    def test_work_group_needs_wiki(self):
        assert _needs_wiki(make_circle(name="Landscape Work Group"))

    def test_furnishings_work_group_needs_wiki(self):
        assert _needs_wiki(make_circle(name="Furnishings Work Group"))

    def test_other_committee_does_not_need_wiki(self):
        # A circle that is a committee for reasons other than its name
        # (col_index=1 still maps to "circle" in GROUP_KINDS, so this
        # just tests the kind override path is respected for non-work-groups)
        c = make_circle(name="Some Committee", col_index=0)
        # "Some Committee" has kind "circle" (no work group suffix), so it gets a wiki
        assert _needs_wiki(c)


# ── _extract_wiki_url ────────────────────────────────────────────────────────

class TestExtractWikiUrl:
    def test_extracts_relative_url(self):
        desc = 'Some text\n<a href="/wiki/membership-wiki">Wiki</a>'
        assert _extract_wiki_url(desc) == "/wiki/membership-wiki"

    def test_extracts_with_trailing_whitespace(self):
        desc = 'Text\n<a href="/wiki/foo-wiki">Wiki</a>  \n'
        assert _extract_wiki_url(desc) == "/wiki/foo-wiki"

    def test_case_insensitive_tag(self):
        desc = 'Text\n<A HREF="/wiki/foo-wiki">Wiki</A>'
        assert _extract_wiki_url(desc) == "/wiki/foo-wiki"

    def test_returns_empty_when_no_link(self):
        assert _extract_wiki_url("Some text with no link") == ""

    def test_returns_empty_when_link_not_at_end(self):
        desc = '<a href="/wiki/foo-wiki">Wiki</a>\nMore text'
        assert _extract_wiki_url(desc) == ""

    def test_returns_empty_for_non_wiki_link(self):
        desc = 'Text\n<a href="/groups/42">Groups</a>'
        assert _extract_wiki_url(desc) == ""


# ── _build_wiki_index_content ─────────────────────────────────────────────────

class TestBuildWikiIndexContent:
    def test_no_title_header(self):
        content = _build_wiki_index_content([("Alpha Circle", "/wiki/alpha-circle-wiki")])
        assert "# Circle Wiki Pages" not in content

    def test_link_format_relative(self):
        content = _build_wiki_index_content([("Alpha Circle", "/wiki/alpha-circle-wiki")])
        assert "- [Alpha Circle](/wiki/alpha-circle-wiki)" in content

    def test_custom_url_used_verbatim(self):
        content = _build_wiki_index_content([("Alpha Circle", "/wiki/custom-page")])
        assert "- [Alpha Circle](/wiki/custom-page)" in content

    def test_alphabetical_order(self):
        content = _build_wiki_index_content([
            ("Membership", "/wiki/membership-wiki"),
            ("Alpha Circle", "/wiki/alpha-circle-wiki"),
            ("Technology", "/wiki/technology-wiki"),
        ])
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0].startswith("- [Alpha")
        assert lines[1].startswith("- [Membership")
        assert lines[2].startswith("- [Technology")

    def test_case_insensitive_sort(self):
        content = _build_wiki_index_content([
            ("beta", "/wiki/beta-wiki"),
            ("Alpha", "/wiki/alpha-wiki"),
        ])
        lines = [l for l in content.splitlines() if l.startswith("- ")]
        assert lines[0].startswith("- [Alpha")
        assert lines[1].startswith("- [beta")

    def test_empty_list(self):
        content = _build_wiki_index_content([])
        assert "- [" not in content


# ── _parse_wiki_index_entries ─────────────────────────────────────────────────

class TestParseWikiIndexEntries:
    def test_parses_single_entry(self):
        content = "- [Alpha Circle](/wiki/alpha-circle-wiki)\n"
        assert _parse_wiki_index_entries(content) == [("Alpha Circle", "/wiki/alpha-circle-wiki")]

    def test_parses_multiple_entries(self):
        content = (
            "- [Alpha Circle](/wiki/alpha-circle-wiki)\n"
            "- [Membership](/wiki/membership-wiki)\n"
        )
        entries = _parse_wiki_index_entries(content)
        assert len(entries) == 2
        assert ("Alpha Circle", "/wiki/alpha-circle-wiki") in entries
        assert ("Membership", "/wiki/membership-wiki") in entries

    def test_ignores_non_bullet_lines(self):
        content = "Some header\n- [Alpha Circle](/wiki/alpha-circle-wiki)\n"
        entries = _parse_wiki_index_entries(content)
        assert entries == [("Alpha Circle", "/wiki/alpha-circle-wiki")]

    def test_empty_content(self):
        assert _parse_wiki_index_entries("") == []

    def test_roundtrip(self):
        pairs = [("Alpha Circle", "/wiki/alpha-circle-wiki"), ("Membership", "/wiki/membership-wiki")]
        content = _build_wiki_index_content(pairs)
        assert set(_parse_wiki_index_entries(content)) == set(pairs)

    def test_partial_merge_preserves_existing(self):
        # Simulate the merge logic used in _ensure_wiki_index when partial=True.
        existing_content = (
            "- [Alpha Circle](/wiki/alpha-circle-wiki)\n"
            "- [Membership](/wiki/membership-wiki)\n"
        )
        new_entry = [("Beta Circle", "/wiki/beta-circle-wiki")]
        existing = dict(_parse_wiki_index_entries(existing_content))
        existing.update(new_entry)
        merged = _build_wiki_index_content(list(existing.items()))
        assert "Alpha Circle" in merged
        assert "Membership" in merged
        assert "Beta Circle" in merged

    def test_partial_merge_updates_existing_url(self):
        existing_content = "- [Alpha Circle](/wiki/alpha-circle-wiki)\n"
        updated_entry = [("Alpha Circle", "/wiki/custom-page")]
        existing = dict(_parse_wiki_index_entries(existing_content))
        existing.update(updated_entry)
        merged = _build_wiki_index_content(list(existing.items()))
        assert "- [Alpha Circle](/wiki/custom-page)" in merged
        assert "alpha-circle-wiki" not in merged


# ── Work Group → committee kind ───────────────────────────────────────────────

def _kind_for_name(name: str) -> str:
    """Mirror the inline kind-assignment logic from _ensure_group."""
    kind = GROUP_KINDS[0]
    if re.search(r"\bwork\s+group$", name, flags=re.IGNORECASE):
        kind = "committee"
    return kind


class TestWorkGroupKind:
    def test_landscape_work_group_is_committee(self):
        assert _kind_for_name("Landscape Work Group") == "committee"

    def test_furnishings_work_group_is_committee(self):
        assert _kind_for_name("Furnishings Work Group") == "committee"

    def test_case_insensitive(self):
        assert _kind_for_name("landscape work group") == "committee"
        assert _kind_for_name("LANDSCAPE WORK GROUP") == "committee"

    def test_regular_circle_is_circle(self):
        assert _kind_for_name("Technology") == "circle"

    def test_membership_circle_is_circle(self):
        assert _kind_for_name("Membership") == "circle"

    def test_work_group_not_at_end_is_circle(self):
        assert _kind_for_name("Work Group Liaison") == "circle"

    def test_group_needs_update_detects_wrong_kind_for_work_group(self):
        g = GatherGroup(
            group_id="1", name="Landscape Work Group", kind="circle",
            availability="closed", description="", members=[],
        )
        assert _group_needs_update(g, "committee", "closed", "", [], "Landscape Work Group")


# ── _filter_circles ───────────────────────────────────────────────────────────

class TestFilterCircles:
    def _circles(self):
        return [
            make_circle(name="Alpha Circle"),
            make_circle(name="Beta Circle"),
            make_circle(name="Gamma Sub"),
        ]

    def test_exact_prefix_match(self):
        result = _filter_circles(self._circles(), "Alpha")
        assert len(result) == 1
        assert result[0].name == "Alpha Circle"

    def test_partial_prefix_match(self):
        result = _filter_circles(self._circles(), "Al")
        assert result[0].name == "Alpha Circle"

    def test_case_insensitive(self):
        result = _filter_circles(self._circles(), "alpha")
        assert result[0].name == "Alpha Circle"

    def test_full_name_prefix(self):
        result = _filter_circles(self._circles(), "Alpha Circle")
        assert result[0].name == "Alpha Circle"

    def test_no_match_exits(self):
        with pytest.raises(SystemExit):
            _filter_circles(self._circles(), "Zeta")

    def test_ambiguous_exits(self):
        circles = [make_circle(name="Alpha One"), make_circle(name="Alpha Two")]
        with pytest.raises(SystemExit):
            _filter_circles(circles, "Alpha")


# ── _apply_gdrive_link ───────────────────────────────────────────────────────

class TestApplyGdriveLink:
    HREF = "/gdrive/membership-folder"
    NAME = "Membership"

    def test_add_to_blank_page(self):
        new_content, action = _apply_gdrive_link("", self.NAME, self.HREF)
        assert action == "add"
        assert new_content == "[Membership Google Drive documents](/gdrive/membership-folder)\n"

    def test_add_appends_after_existing_content(self):
        new_content, action = _apply_gdrive_link("Some content.", self.NAME, self.HREF)
        assert action == "add"
        assert new_content.startswith("Some content.\n\n")
        assert "[Membership Google Drive documents]" in new_content

    def test_skip_when_text_already_correct(self):
        content = "[Membership Google Drive documents](/gdrive/membership-folder)"
        new_content, action = _apply_gdrive_link(content, self.NAME, self.HREF)
        assert action == "skip"
        assert new_content is None

    def test_update_wrong_link_text(self):
        content = "Some text\n\n[Google Drive documents](/gdrive/membership-folder)"
        new_content, action = _apply_gdrive_link(content, self.NAME, self.HREF)
        assert action == "update"
        assert "[Membership Google Drive documents](/gdrive/membership-folder)" in new_content
        assert "[Google Drive documents]" not in new_content

    def test_update_preserves_existing_href(self):
        content = "[Old Text](/gdrive/custom-path)"
        new_content, action = _apply_gdrive_link(content, self.NAME, self.HREF)
        assert action == "update"
        assert "/gdrive/custom-path" in new_content

    def test_update_preserves_surrounding_content(self):
        content = "Before\n\n[Old Text](/gdrive/foo)\n\nAfter"
        new_content, action = _apply_gdrive_link(content, self.NAME, self.HREF)
        assert action == "update"
        assert "Before" in new_content
        assert "After" in new_content


# ── find_gdrive_link ──────────────────────────────────────────────────────────

class TestFindGdriveLink:
    GDRIVE = [
        ("Technology Circle", "/gdrive/tech-folder"),
        ("Membership", "/gdrive/membership-folder"),
        ("Community Life Circle", "/gdrive/clc-folder"),
        ("Process & Governance", "/gdrive/pg-folder"),
    ]

    def test_exact_match(self):
        assert find_gdrive_link("Membership", self.GDRIVE) == "/gdrive/membership-folder"

    def test_circle_suffix_match(self):
        # Gather group "Technology Circle" → spreadsheet name "Technology"
        assert find_gdrive_link("Technology", self.GDRIVE) == "/gdrive/tech-folder"

    def test_alias_match(self):
        # CLC expands to "Community Life Circle"
        assert find_gdrive_link("CLC", self.GDRIVE) == "/gdrive/clc-folder"

    def test_acronym_match(self):
        assert find_gdrive_link("P & G", self.GDRIVE) == "/gdrive/pg-folder"

    def test_no_match_returns_none(self):
        assert find_gdrive_link("Finance", self.GDRIVE) is None

    def test_ambiguous_returns_none(self):
        links = [
            ("Alpha Circle", "/gdrive/alpha1"),
            ("Alpha Circle (old)", "/gdrive/alpha2"),
        ]
        assert find_gdrive_link("Alpha", links) is None

    def test_empty_list_returns_none(self):
        assert find_gdrive_link("Membership", []) is None
