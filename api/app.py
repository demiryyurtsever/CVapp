"""FastAPI application factory (dossier §6.1).

``create_app()`` builds the read-only API: a health probe plus the postings
router (list + detail). It is a factory (not a module-level singleton) so tests
can construct an app and override the DB-session dependency to point at their
in-memory fixture database.

This session deliberately mounts ONLY read routes. Write endpoints, ingestion
triggers, auth, CV/profile, and the tailor action (the rest of §6.1) are out of
scope and not wired here.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.routers import postings

API_TITLE = "IB Internship Platform — Read API"
API_VERSION = "0.1.0"


def create_app() -> FastAPI:
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        summary="Read-only view over the openings database (dossier §6.1, minimal slice).",
    )

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(postings.router)
    return app


# Module-level app for `uvicorn api.app:app` (local run / the §6.1 demo).
app = create_app()
