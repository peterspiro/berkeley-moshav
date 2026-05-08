"""
Unit tests for the pure-Python functions in gather_groups.py.

All names, emails, and identifying details are fictional.
"""

import textwrap
from typing import Optional

import pytest

from gather_groups import (
    Circle,
    GatherGroup,
    GatherGroupMember,
    GatherUser,
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

    def test_ambiguous_member_raises(self):
        # Two users named Sam Norris and Taylor Norris; matching "Sam" + None
        # would be fine for Sam, but if we search just first name without last
        # and two users share first name:
        users_with_two_sams = USERS + [make_user("99", "Sam", "Vale")]
        c = make_circle(member_lines=["- Sam"])
        # "Sam" with no last name matches both Sam Norris and Sam Vale
        with pytest.raises(ValueError, match="Ambiguous"):
            resolve_group_members(c, users_with_two_sams)

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
