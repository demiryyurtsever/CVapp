"""Shared normalization/classification helpers (dossier §3.8).

These DERIVE the §7 fields that source payloads (e.g. Greenhouse) do not supply
directly:

* ``program_type`` — from the role title (keyword rules).
* ``division``     — from the title, then the department names.
* ``region``       — from the location string.

Keyword sets live in config (``config/classifier_keywords.yaml``), not code
(§2.3), so they are editable without redeploying. Matching is case-insensitive
and word-boundary aware so short tokens (``uk``, ``us``) do not match inside
unrelated words (``campus``, ``Belarus``).

The §3.8 contract: an ambiguous title returns ``ProgramType.unclassified`` (it
surfaces in the admin review queue) — it is never dropped or guessed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

import yaml

from ingestion.models import ProgramType, Region

DEFAULT_KEYWORDS_PATH = Path(__file__).resolve().parent / "config" / "classifier_keywords.yaml"

Rules = Mapping[str, Sequence[str]]


@lru_cache(maxsize=None)
def _load(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _keywords(section: str, override: Rules | None) -> Rules:
    if override is not None:
        return override
    return _load(str(DEFAULT_KEYWORDS_PATH))[section]


def _matches(text: str, keyword: str) -> bool:
    return re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE) is not None


def classify_program_type(title: str, keywords: Rules | None = None) -> ProgramType:
    """Map a role title to a §7 program_type. Rules are checked in order; first
    match wins. No match -> ``unclassified`` (kept for review, never dropped)."""
    rules = _keywords("program_type", keywords)
    for program_type, words in rules.items():
        if any(_matches(title, word) for word in words):
            return ProgramType(program_type)
    return ProgramType.unclassified


def extract_division(
    title: str,
    departments: Sequence[str] = (),
    keywords: Rules | None = None,
) -> str | None:
    """Extract a division from the title, then the department names. ``None`` when
    nothing matches (§7 division is populated only "where extractable")."""
    rules = _keywords("division", keywords)
    haystacks = [title, *departments]
    for division, words in rules.items():
        if any(_matches(text, word) for text in haystacks for word in words):
            return division
    return None


def map_region(location: str, keywords: Rules | None = None) -> Region:
    """Normalize a location string to a §7 region. No match -> ``unknown``."""
    if not location:
        return Region.unknown
    rules = _keywords("region", keywords)
    for region, words in rules.items():
        if any(_matches(location, word) for word in words):
            return Region(region)
    return Region.unknown
