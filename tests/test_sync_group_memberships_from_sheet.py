import pytest
from data_import.sync_group_memberships_from_sheet import (
    _normalize_group,
    group_names_match,
    parse_memberships,
    resolve_user,
)
from util.gather_utils import GatherUser


# ── _normalize_group / group_names_match ──────────────────────────────────────

class TestNormalizeGroup:
    def test_lowercase(self):
        assert _normalize_group("Finance") == "finance"

    def test_strips_circle_suffix(self):
        assert _normalize_group("Finance Circle") == "finance"

    def test_strips_team_suffix(self):
        assert _normalize_group("Finance Team") == "finance"

    def test_strips_working_group_suffix(self):
        assert _normalize_group("Landscape Working Group") == "landscape"

    def test_strips_parenthetical(self):
        assert _normalize_group("Finance (sub)") == "finance"

    def test_acronym_clc(self):
        assert _normalize_group("CLC") == "community life"

    def test_acronym_pg(self):
        assert _normalize_group("P & G") == "process and governance"

    def test_acronym_pg_no_spaces(self):
        assert _normalize_group("P&G") == "process and governance"

    def test_acronym_pg_case_insensitive(self):
        assert _normalize_group("p & g") == "process and governance"

    def test_acronym_pg_nonbreaking_space(self):
        # Google Sheets sometimes uses non-breaking spaces around &
        assert _normalize_group("P & G") == "process and governance"

    def test_acronym_dfl(self):
        assert _normalize_group("DFL") == "development finance and legal"

    def test_acronym_dfl_case_insensitive(self):
        assert _normalize_group("dfl") == "development finance and legal"

    def test_ampersand_becomes_and(self):
        assert _normalize_group("Process & Governance") == "process and governance"

    def test_strips_pod_suffix(self):
        assert _normalize_group("Young Families Pod") == "young families"

    def test_strips_gatherings_suffix(self):
        assert _normalize_group("Social Gatherings") == "social"

    def test_alias_tech_technology(self):
        assert _normalize_group("Technology") == _normalize_group("Tech")

    def test_alias_parking(self):
        assert _normalize_group("Parking") == _normalize_group("Parking & Car Share")

    def test_work_group_normalized(self):
        assert _normalize_group("Landscape Work Group") == _normalize_group("Landscape Working Group")


class TestGroupNamesMatch:
    def test_exact(self):
        assert group_names_match("Technology", "Technology")

    def test_circle_suffix(self):
        assert group_names_match("Technology", "Technology Circle")

    def test_acronym_vs_full(self):
        assert group_names_match("CLC", "Community Life Circle")

    def test_pg_variants(self):
        assert group_names_match("P & G", "Process & Governance Circle")

    def test_tech_alias(self):
        assert group_names_match("Technology", "Tech Circle")

    def test_young_families_pod(self):
        assert group_names_match("Young Families", "Young Families Pod")

    def test_social_gatherings(self):
        assert group_names_match("Social", "Social Gatherings")

    def test_parking_car_share(self):
        assert group_names_match("Parking", "Parking & Car Share")

    def test_dfl_full_name(self):
        assert group_names_match("DFL", "Development, Finance, & Legal")

    def test_pg_full_name(self):
        assert group_names_match("P & G", "Process & Governance")

    def test_no_match(self):
        assert not group_names_match("Technology", "Finance")

    def test_case_insensitive(self):
        assert group_names_match("technology", "Technology Circle")


# ── parse_memberships ─────────────────────────────────────────────────────────

def _sheet(*rows: str) -> str:
    """Build a minimal TSV-style CSV string from header + data rows."""
    header = "NOTES,First Name,Last Name,Email Address,Status,CIRCLE MEMBERSHIP"
    return "\n".join([header] + list(rows))


class TestParseMemberships:
    def test_basic_bm_member(self):
        csv = _sheet("notes,Peter,Spiro,peter@example.com,Member,BM; Technology")
        result = parse_memberships(csv)
        assert len(result) == 1
        assert result[0]["first_name"] == "Peter"
        assert result[0]["last_name"] == "Spiro"
        assert result[0]["email"] == "peter@example.com"
        assert result[0]["groups"] == ["Technology"]

    def test_bm_only_no_groups(self):
        csv = _sheet("notes,Alice,Brown,alice@example.com,Member,BM")
        result = parse_memberships(csv)
        assert len(result) == 1
        assert result[0]["groups"] == []

    def test_multiple_groups(self):
        csv = _sheet("notes,Bob,Lee,bob@example.com,Member,BM; Technology; Finance")
        result = parse_memberships(csv)
        assert result[0]["groups"] == ["Technology", "Finance"]

    def test_consultant_with_bm_included(self):
        csv = _sheet("notes,Katie,McCamant,k@example.com,Consultant,BM; Technology")
        result = parse_memberships(csv)
        assert len(result) == 1

    def test_no_bm_skipped(self):
        csv = _sheet("notes,Dave,Jones,dave@example.com,Member,Technology")
        result = parse_memberships(csv)
        assert result == []

    def test_blank_circle_membership_skipped(self):
        csv = _sheet("notes,Eve,Wu,eve@example.com,Member,")
        result = parse_memberships(csv)
        assert result == []

    def test_email_lowercased(self):
        csv = _sheet("notes,Grace,Park,GRACE@Example.COM,Member,BM")
        result = parse_memberships(csv)
        assert result[0]["email"] == "grace@example.com"

    def test_leading_rows_before_header(self):
        csv = (
            "Title row ignored\n"
            "NOTES,First Name,Last Name,Email Address,Status,CIRCLE MEMBERSHIP\n"
            "notes,Henry,Ford,h@example.com,Member,BM; Technology\n"
        )
        result = parse_memberships(csv)
        assert len(result) == 1
        assert result[0]["first_name"] == "Henry"

    def test_multiple_rows(self):
        csv = _sheet(
            "n,Alice,A,a@example.com,Member,BM; Technology",
            "n,Bob,B,b@example.com,Member,BM",
            "n,Carol,C,c@example.com,Non-member,Technology",  # no BM → skipped
        )
        result = parse_memberships(csv)
        assert len(result) == 2
        names = [r["first_name"] for r in result]
        assert "Alice" in names
        assert "Bob" in names


# ── resolve_user ──────────────────────────────────────────────────────────────

def _user(uid, first, last, email=""):
    return GatherUser(
        user_id=uid, first_name=first, last_name=last,
        full_name=f"{first} {last}", email=email,
    )


class TestResolveUser:
    def setup_method(self):
        self.users = [
            _user("1", "Peter", "Spiro", "peter@example.com"),
            _user("2", "Alice", "Brown", "alice@example.com"),
            _user("3", "Bob", "Lee", ""),
        ]
        self.email_index = {u.email: u for u in self.users if u.email}

    def _resolve(self, entry, warnings=None):
        if warnings is None:
            warnings = []
        return resolve_user(entry, self.email_index, self.users, warnings)

    def test_match_by_email(self):
        entry = {"first_name": "Peter", "last_name": "Spiro", "email": "peter@example.com"}
        u = self._resolve(entry)
        assert u is not None
        assert u.user_id == "1"

    def test_match_by_name_fallback(self):
        entry = {"first_name": "Bob", "last_name": "Lee", "email": ""}
        warnings = []
        u = resolve_user(entry, self.email_index, self.users, warnings)
        assert u is not None
        assert u.user_id == "3"
        assert warnings == []

    def test_name_fallback_warns_about_email(self):
        entry = {"first_name": "Bob", "last_name": "Lee", "email": "other@example.com"}
        warnings = []
        u = resolve_user(entry, self.email_index, self.users, warnings)
        assert u is not None
        assert u.user_id == "3"
        assert any("by name" in w for w in warnings)

    def test_not_found(self):
        entry = {"first_name": "Unknown", "last_name": "Person", "email": "x@example.com"}
        warnings = []
        u = resolve_user(entry, self.email_index, self.users, warnings)
        assert u is None
        assert len(warnings) == 1

    def test_ambiguous_name(self):
        users = [
            _user("4", "Alex", "Smith", ""),
            _user("5", "Alex", "Smith", ""),
        ]
        email_index = {}
        warnings = []
        entry = {"first_name": "Alex", "last_name": "Smith", "email": ""}
        u = resolve_user(entry, email_index, users, warnings)
        assert u is None
        assert any("Ambiguous" in w for w in warnings)

    def test_case_insensitive_last_name(self):
        entry = {"first_name": "Peter", "last_name": "spiro", "email": ""}
        u = self._resolve(entry)
        assert u is not None
        assert u.user_id == "1"
