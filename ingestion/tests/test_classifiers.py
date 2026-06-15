"""Classifier unit tests (dossier §3.8) — pure functions, no I/O."""

from __future__ import annotations

import pytest

from ingestion.classifiers import classify_program_type, extract_division, map_region
from ingestion.models import ProgramType, Region


@pytest.mark.parametrize(
    "title, expected",
    [
        # Real titles from greenhouse_point72.json that DO classify.
        ("2026 Point72 Academy Insight Program - Japan", ProgramType.spring_week),
        (
            "2027 Point72 Academy Investment Analyst Summer Internship Program - Hong Kong",
            ProgramType.summer,
        ),
        (
            "Point72 Academy Investment Analyst Program for Upcoming Graduates (2027 - UK)",
            ProgramType.graduate,
        ),
        # Real titles that match no rule -> unclassified (NOT guessed, NOT dropped).
        ("2026 Technology Internship - Software Engineer", ProgramType.unclassified),
        ("Quantitative Developer Intern", ProgramType.unclassified),
        ("Administrative Assistant", ProgramType.unclassified),
        (
            "Point72 Academy 2026 Investment Analyst Program for Experienced Professionals - UK",
            ProgramType.unclassified,
        ),
    ],
)
def test_program_type_on_real_titles(title: str, expected: ProgramType) -> None:
    assert classify_program_type(title) == expected


def test_off_cycle_and_placement_rules() -> None:
    assert classify_program_type("Off-Cycle Analyst Internship") == ProgramType.off_cycle
    assert classify_program_type("Industrial Placement, Finance") == ProgramType.off_cycle


def test_ambiguous_title_is_unclassified_not_guessed() -> None:
    # §3.8: unknown -> unclassified (surfaced to review), never forced into a bucket.
    assert classify_program_type("Senior Vice President, Strategy") == ProgramType.unclassified
    assert classify_program_type("") == ProgramType.unclassified


def test_short_region_tokens_do_not_match_inside_words() -> None:
    # "us"/"uk" must not match "campus"/"Belarus" etc. (word-boundary matching).
    assert map_region("Campus Recruiting Hub") == Region.unknown


@pytest.mark.parametrize(
    "location, expected",
    [
        ("London", Region.UK),
        ("London, UK", Region.UK),
        ("New York, NY", Region.US),
        ("United States", Region.US),
        ("Stamford, CT", Region.US),
        ("Warsaw", Region.EMEA),
        ("Warsaw, Poland", Region.EMEA),
        ("Japan", Region.APAC),
        ("Singapore", Region.APAC),
        ("Bengaluru, India", Region.APAC),
        ("Mars Base Alpha", Region.unknown),
        ("", Region.unknown),
    ],
)
def test_region_mapping(location: str, expected: Region) -> None:
    assert map_region(location) == expected


# Coverage gap closed in Session 9 (§3.8): these are the EXACT location strings that
# appear in the three captured fixtures (greenhouse_point72 / lever_wealthfront /
# workday_barclays) and used to fall to `unknown` because the keyword config had no
# mapping for them. Asserting the real strings (not just bare city names) means a
# future deletion of any of these region keywords fails this test LOUDLY, and proves
# the actual fixture postings now classify instead of dropping to `unknown`.
@pytest.mark.parametrize(
    "location, expected",
    [
        # Indian Workday offices (workday_barclays.json) — Session 8 named these.
        ("Pune, Gera Commerzone SEZ", Region.APAC),
        ("Noida, Candor TechSpace", Region.APAC),
        ("Chennai, DLF IT Park", Region.APAC),
        # Other Indian cities Session 8 named (defensive, for future captures).
        ("Gurugram", Region.APAC),
        ("Gurgaon", Region.APAC),
        ("Hyderabad", Region.APAC),
        # Czech Workday office (workday_barclays.json).
        ("Gemini Building B, Prague", Region.EMEA),
        # Surfaced by the fixture audit on the JSON boards.
        ("Taiwan", Region.APAC),  # greenhouse_point72
        ("Florida", Region.US),  # greenhouse_point72
        ("Miami", Region.US),  # greenhouse_point72
        ("Palo Alto, CA", Region.US),  # lever_wealthfront (bare; no "US-based" tail)
        # Belfast completes the London/Glasgow/Belfast multi-office UK shape the
        # dedup region-grain revisit needs to stress (Glasgow/London already mapped).
        ("Belfast", Region.UK),
    ],
)
def test_previously_unknown_fixture_offices_now_classify(
    location: str, expected: Region
) -> None:
    assert map_region(location) == expected
    assert map_region(location) != Region.unknown


def test_new_city_keywords_are_word_boundary_safe() -> None:
    # The campus/Belarus precedent extended to the Session-9 additions: a city
    # keyword must not match inside an unrelated longer word.
    assert map_region("Puneville Holdings") == Region.unknown  # not "Pune"
    assert map_region("Praguerie Festival Office") == Region.unknown  # not "Prague"
    assert map_region("Floridaman Logistics") == Region.unknown  # not "Florida"


def test_division_extraction_from_title_then_departments() -> None:
    assert extract_division("Cubist Quantitative Researcher Intern", ["Quant Management"]) == "Quant"
    assert (
        extract_division("2026 Technology Internship - Software Engineer", ["Technology"])
        == "Technology"
    )
    assert extract_division("Administrative Assistant", ["Human Capital"]) is None
