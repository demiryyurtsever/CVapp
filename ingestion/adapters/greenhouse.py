"""Greenhouse adapter (dossier §3.3) — the reference implementation.

Public JSON API: ``boards-api.greenhouse.io/v1/boards/{company}/jobs`` (with
``?content=true`` for descriptions). No auth. ``parse()`` maps payload fields to
the §7 schema and calls the §3.8 classifiers for the fields Greenhouse does not
supply directly (program_type, division, region).

Notes on fields with no reliable Greenhouse source (confirmed against the
captured fixture):

* ``deadline`` — ``application_deadline`` is present but empty for every job in
  the fixture, so it parses to ``None``.
* ``rolling`` — no field exists; left ``False`` until content-based detection is
  added in a later session.
* ``firm`` / ``firm_tier`` — taken from the registry entry, not the payload.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ingestion.adapters.base import Adapter
from ingestion.classifiers import classify_program_type, extract_division, map_region
from ingestion.models import Posting
from ingestion.registry import AtsType

BOARDS_API = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
USER_AGENT = "IBPlatformBot/0.1 (early-careers ingestion; contact: demiryurtsever008@gmail.com)"


def _parse_date(value: Any) -> date | None:
    """Parse a Greenhouse timestamp/date string to a ``date``; ``None`` if empty."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


class GreenhouseAdapter(Adapter):
    ats_type = AtsType.greenhouse

    def _url(self) -> str:
        return BOARDS_API.format(company=self.entry.endpoint_or_url)

    def fetch(self) -> dict[str, Any]:
        # Lazy import: tests never call fetch(), so httpx is not required to import
        # this module or exercise parse(). One request, honest UA, no retry loop.
        import httpx

        response = httpx.get(
            self._url(),
            params={"content": "true"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def parse(self, raw: dict[str, Any]) -> list[Posting]:
        postings: list[Posting] = []
        for job in raw.get("jobs", []):
            title = job.get("title") or ""
            departments = [d.get("name", "") for d in job.get("departments", []) if d]
            location = (job.get("location") or {}).get("name") or ""
            postings.append(
                Posting(
                    firm=self.entry.firm_name,
                    firm_tier=self.entry.firm_tier,
                    role_title=title,
                    location=location,
                    source_url=job.get("absolute_url", ""),
                    source_id=str(job.get("id", "")),
                    open_date=_parse_date(job.get("first_published")),
                    deadline=_parse_date(job.get("application_deadline")),
                    raw_description=job.get("content"),
                    program_type=classify_program_type(title),
                    division=extract_division(title, departments),
                    region=map_region(location),
                )
            )
        return postings
