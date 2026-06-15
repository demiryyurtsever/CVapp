"""Workday adapter (dossier §3.5) — the highest-value, fiddliest adapter family.

Most bulge brackets post through Workday. Postings come from a JSON **POST** to a
per-tenant endpoint:

    {tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

with a paginated search payload (``{"appliedFacets": {}, "limit": N, "offset": M,
"searchText": "..."}``). The listing response is **shallow** — each ``jobPostings``
item carries only ``title`` / ``externalPath`` / ``locationsText`` / ``postedOn``
(a *relative* string like "Posted 5 Days Ago", not an absolute date) /
``bulletFields`` (the ``JR-…`` req id). The full description and an absolute date
live on a **per-posting follow-up**:

    GET {tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

whose ``jobPostingInfo`` carries ``jobDescription`` (HTML), ``startDate`` (ISO),
and ``externalUrl`` (the canonical public link). ``fetch()`` paginates the listing
and attaches each posting's detail under the ``"_detail"`` key; ``parse()`` reads
that detail when present and falls back to listing-only fields otherwise.

``parse()`` maps the payload to the §7 schema and calls the SHARED §3.8 classifiers
(``classify_program_type`` / ``extract_division`` / ``map_region``) — there is no
Workday-specific classification logic.

[OPEN] §8.2 — Workday tenant variation: config vs subclasses. Response shapes and
connection params (dc number ``wd{n}``, site name, early-careers facet/search
filter) vary per tenant. **This session chose CONFIG**: every per-tenant quirk
lives in the registry entry's ``config`` block (``ingestion/config/registry.yaml``),
so a new Workday firm is a data entry, not a subclass. This is a proposal, not a
lock — if a future tenant needs behaviour config cannot express (e.g. a genuinely
different response shape), a thin per-tenant subclass over this base is the escape
hatch. Until then, keep tenant quirks in registry data.

Fields with no reliable Workday source (confirmed against the captured fixture
``tests/fixtures/workday_barclays.json``):

* ``deadline`` — neither listing nor detail carries an application deadline -> ``None``.
* ``rolling`` — no field; left ``False`` until content-based detection (later).
* ``open_date`` — the listing's ``postedOn`` is a relative string, so it is taken
  from the detail's ``startDate`` where available, else ``None`` (never guessed
  from "Posted N Days Ago").
* ``firm`` / ``firm_tier`` — taken from the registry entry, not the payload.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from ingestion.adapters.base import Adapter
from ingestion.classifiers import classify_program_type, extract_division, map_region
from ingestion.models import Posting
from ingestion.registry import AtsType

USER_AGENT = "IBPlatformBot/0.1 (early-careers ingestion; contact: demiryurtsever008@gmail.com)"
PAGE_LIMIT = 20
# Politeness (§3.12): a per-request delay so a paginated + per-posting-detail run
# never parallel-hammers a single Workday host.
REQUEST_DELAY_SECONDS = 1.0


def _parse_iso_date(value: Any) -> date | None:
    """Parse a Workday ``startDate`` (``"YYYY-MM-DD"``) to a ``date``; ``None`` if
    absent or unparseable. The shallow listing has no absolute date, so this only
    ever sees the per-posting detail's ``startDate``."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


class WorkdayAdapter(Adapter):
    ats_type = AtsType.workday

    # --- per-tenant connection params, all from registry config (§3.5 / [OPEN]) ---
    @property
    def _tenant(self) -> str:
        # Consistent with greenhouse/lever: endpoint_or_url is the company/tenant token.
        return self.entry.endpoint_or_url

    @property
    def _config(self) -> dict[str, Any]:
        cfg = self.entry.config or {}
        for required in ("dc", "site"):
            if not cfg.get(required):
                raise ValueError(
                    f"Workday source {self.entry.firm_name!r} is missing required "
                    f"config key {required!r} (registry.yaml). Workday needs tenant "
                    f"(endpoint_or_url) + config.dc + config.site."
                )
        return cfg

    def _base_url(self) -> str:
        cfg = self._config
        return f"https://{self._tenant}.{cfg['dc']}.myworkdayjobs.com"

    def _jobs_url(self) -> str:
        cfg = self._config
        return f"{self._base_url()}/wday/cxs/{self._tenant}/{cfg['site']}/jobs"

    def _detail_url(self, external_path: str) -> str:
        cfg = self._config
        return f"{self._base_url()}/wday/cxs/{self._tenant}/{cfg['site']}{external_path}"

    def _public_url(self, external_path: str) -> str:
        """The canonical public posting link, built from the relative externalPath
        when the detail follow-up (with its ``externalUrl``) is not present."""
        cfg = self._config
        return f"{self._base_url()}/{cfg['site']}{external_path}"

    # ------------------------------------------------------------------------- #
    # fetch() — paginated POST + per-posting detail. Tests never call this.
    # ------------------------------------------------------------------------- #
    def fetch(self) -> dict[str, Any]:
        # Lazy import: tests never call fetch(), so httpx is not needed to import
        # this module or exercise parse(). Honest UA, polite per-request delay.
        import httpx

        cfg = self._config
        search_text = cfg.get("search_text", "")
        applied_facets = cfg.get("applied_facets") or {}
        jobs_url = self._jobs_url()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            postings: list[dict[str, Any]] = []
            total: int | None = None
            offset = 0
            while True:
                payload = {
                    "appliedFacets": applied_facets,
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                    "searchText": search_text,
                }
                resp = client.post(jobs_url, headers=headers, json=payload)
                resp.raise_for_status()
                page = resp.json()
                total = page.get("total") if total is None else total
                batch = page.get("jobPostings") or []
                postings.extend(batch)
                offset += len(batch)
                if not batch or total is None or offset >= total:
                    break
                time.sleep(REQUEST_DELAY_SECONDS)

            # Per-posting detail follow-up for raw_description / open_date (§3.5).
            detail_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            for job in postings:
                external_path = job.get("externalPath")
                if not external_path:
                    continue
                time.sleep(REQUEST_DELAY_SECONDS)
                d = client.get(self._detail_url(external_path), headers=detail_headers)
                d.raise_for_status()
                job["_detail"] = d.json().get("jobPostingInfo")

        return {"total": total, "jobPostings": postings}

    # ------------------------------------------------------------------------- #
    # parse() — pure mapping to §7; no I/O, no DB. Operates on the fetch() shape.
    # ------------------------------------------------------------------------- #
    def parse(self, raw: dict[str, Any]) -> list[Posting]:
        postings: list[Posting] = []
        for job in raw.get("jobPostings", []):
            title = job.get("title") or ""
            location = job.get("locationsText") or ""
            external_path = job.get("externalPath") or ""
            bullet_fields = job.get("bulletFields") or []
            source_id = str(bullet_fields[0]) if bullet_fields else ""

            # Detail (per-posting follow-up) enriches description / date / URL when
            # present; otherwise we fall back to what the shallow listing gives us.
            detail = job.get("_detail") or {}
            raw_description = detail.get("jobDescription")
            open_date = _parse_iso_date(detail.get("startDate"))
            source_url = detail.get("externalUrl") or (
                self._public_url(external_path) if external_path else ""
            )
            if not source_id:
                source_id = str(detail.get("jobReqId") or "")

            postings.append(
                Posting(
                    firm=self.entry.firm_name,
                    firm_tier=self.entry.firm_tier,
                    role_title=title,
                    location=location,
                    source_url=source_url,
                    source_id=source_id,
                    open_date=open_date,
                    deadline=None,  # No deadline field in the Workday payload.
                    raw_description=raw_description,
                    program_type=classify_program_type(title),
                    division=extract_division(title),
                    region=map_region(location),
                )
            )
        return postings
