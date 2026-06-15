"""Lever adapter (dossier §3.4).

Public JSON API: ``api.lever.co/v0/postings/{company}?mode=json``. No auth.
Same mapping pattern as the Greenhouse reference adapter (§3.3): ``parse()`` maps
payload fields to the §7 schema and calls the SHARED §3.8 classifiers for the
derived fields (program_type, division, region) — there is no Lever-specific
classification logic.

Shape of the Lever payload (confirmed against the captured fixture
``tests/fixtures/lever_wealthfront.json``): a FLAT JSON array of postings — not a
``{"jobs": [...]}`` wrapper like Greenhouse. Each posting carries:

* ``text``                       — role title.
* ``categories.location``        — location string ("as posted").
* ``categories.department`` / ``categories.team`` — fed to the division classifier.
* ``createdAt``                  — epoch MILLISECONDS (int), parsed to open_date.
* ``hostedUrl``                  — public posting page (the primary-source link).
* ``id``                         — Lever-native posting id, used for dedup.
* ``description`` / ``lists`` / ``additional`` — the description is SPLIT across
  these HTML fragments; ``parse()`` rejoins them into ``raw_description`` so the
  Layer 2 posting parser sees the full text.

Fields with no reliable Lever source (confirmed against the fixture):

* ``deadline`` — Lever postings carry no deadline field, so it is ``None``.
* ``rolling`` — no field; left ``False`` until content-based detection (later).
* ``firm`` / ``firm_tier`` — taken from the registry entry, not the payload.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ingestion.adapters.base import Adapter
from ingestion.classifiers import classify_program_type, extract_division, map_region
from ingestion.models import Posting
from ingestion.registry import AtsType

POSTINGS_API = "https://api.lever.co/v0/postings/{company}"
USER_AGENT = "IBPlatformBot/0.1 (early-careers ingestion; contact: demiryurtsever008@gmail.com)"


def _epoch_ms_to_date(value: Any) -> date | None:
    """Parse a Lever ``createdAt`` (epoch milliseconds) to a UTC ``date``.

    ``None`` for anything that is not a usable integer timestamp.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None


def _full_description(job: dict[str, Any]) -> str | None:
    """Rejoin Lever's split description fragments into one HTML blob.

    Lever splits a posting body across ``description`` (opening), ``lists``
    (each a heading + an HTML ``<li>`` block), and ``additional`` (closing). §7's
    ``raw_description`` feeds the Layer 2 parser, which needs the WHOLE posting, so
    the fragments are concatenated in source order. ``None`` if nothing is present.
    """
    parts: list[str] = [job.get("description") or ""]
    for section in job.get("lists") or []:
        heading = (section or {}).get("text") or ""
        content = (section or {}).get("content") or ""
        if not content and not heading:
            continue
        block = f"<ul>{content}</ul>" if content else ""
        parts.append(f"<h3>{heading}</h3>\n{block}" if heading else block)
    parts.append(job.get("additional") or "")
    joined = "\n".join(part for part in parts if part).strip()
    return joined or None


class LeverAdapter(Adapter):
    ats_type = AtsType.lever

    def _url(self) -> str:
        return POSTINGS_API.format(company=self.entry.endpoint_or_url)

    def fetch(self) -> list[dict[str, Any]]:
        # Lazy import: tests never call fetch(), so httpx is not required to import
        # this module or exercise parse(). One request, honest UA, no retry loop.
        import httpx

        response = httpx.get(
            self._url(),
            params={"mode": "json"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def parse(self, raw: list[dict[str, Any]]) -> list[Posting]:
        postings: list[Posting] = []
        for job in raw:
            categories = job.get("categories") or {}
            title = job.get("text") or ""
            location = categories.get("location") or ""
            departments = [categories.get("department") or "", categories.get("team") or ""]
            postings.append(
                Posting(
                    firm=self.entry.firm_name,
                    firm_tier=self.entry.firm_tier,
                    role_title=title,
                    location=location,
                    source_url=job.get("hostedUrl") or "",
                    source_id=str(job.get("id") or ""),
                    open_date=_epoch_ms_to_date(job.get("createdAt")),
                    deadline=None,  # Lever postings carry no deadline field.
                    raw_description=_full_description(job),
                    program_type=classify_program_type(title),
                    division=extract_division(title, departments),
                    region=map_region(location),
                )
            )
        return postings
