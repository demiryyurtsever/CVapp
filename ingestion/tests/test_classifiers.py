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


def test_division_extraction_from_title_then_departments() -> None:
    assert extract_division("Cubist Quantitative Researcher Intern", ["Quant Management"]) == "Quant"
    assert (
        extract_division("2026 Technology Internship - Software Engineer", ["Technology"])
        == "Technology"
    )
    assert extract_division("Administrative Assistant", ["Human Capital"]) is None
