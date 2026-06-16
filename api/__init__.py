"""Layer: read API (dossier §2.3 / §6.1).

The HTTP surface over the openings database the ingestion layer (§3) fills. This
session builds only the MINIMAL read-only slice of §6.1 — list + detail over
``postings`` — so the stored postings become visible and filterable. No write
endpoints, no ingestion triggers, no auth/CV/profile/tailor routes yet.

The API NEVER writes postings: only the pipeline orchestrator does (§3.2). It
reads the same §7 storage models the pipeline persists and projects them into the
read-facing response models in ``api/schemas.py``.
"""
