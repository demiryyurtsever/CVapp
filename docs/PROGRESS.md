# Progress Log — IB Internship Application Platform

Append-only record of what each build session delivered and how to verify it.
Single source of truth: the Project Dossier (`§` references below point to it).

---

## Session 1 — Greenhouse fixture capture (ingestion)

- Captured one live Greenhouse board to
  `ingestion/tests/fixtures/greenhouse_point72.json` (company token `point72`,
  `?content=true`, **249** postings). Raw and unmodified; one polite request.
- Mapped the payload to §7 and identified the fields Greenhouse does **not**
  supply directly: `firm_tier` (registry-seeded), `program_type`/`division`/
  `region` (derived), `rolling` (no field), and `deadline` (`application_deadline`
  present but empty for all jobs).

## Session 2 — Adapter interface, registry, classifiers, Greenhouse adapter (ingestion)

Built exactly the scoped pieces (no pipeline / dedup / scheduler / DB writes):

1. **Canonical schema** — `ingestion/models.py`: `Posting` (Pydantic v2, matches
   §7 exactly, `extra="forbid"`) + enums `FirmTier`, `ProgramType`, `Region`,
   `Status`. Pipeline-owned fields (`id`, `first_seen`, `last_seen`) are
   Optional/`None`; `status` defaults to `open` (the §3.9 change-detection logic
   owns `closed`/`reappeared` transitions, a later session).
2. **Adapter interface** — `ingestion/adapters/base.py`: abstract `Adapter` with
   `fetch()` and `parse(raw) -> list[Posting]`. Stateless; holds only its
   immutable registry entry; no DB access (§3.2). Guards against an ats_type
   mismatch between adapter and registry entry.
3. **Source registry** — `ingestion/registry.py` + `ingestion/config/registry.yaml`:
   typed `SourceEntry` loader, one entry (Point72 / greenhouse / token `point72`).
   `firm_tier: MM` is a **placeholder** — Point72 is a multi-manager fund, not an
   IB BB/EB/MM/boutique; it is the reference live Greenhouse source.
4. **Classifiers (§3.8)** — `ingestion/classifiers.py` +
   `ingestion/config/classifier_keywords.yaml`: title→`program_type` (ambiguous →
   `unclassified`, never dropped), division extraction, location→`region`.
   Keyword sets live in config (§2.3); matching is case-insensitive and
   word-boundary aware (so `us`/`uk` don't match inside `campus`/`Belarus`).
5. **Greenhouse adapter (§3.3)** — `ingestion/adapters/greenhouse.py`: `parse()`
   maps fixture fields to §7 and calls the classifiers for the derived fields;
   `fetch()` hits the live boards-api with an honest User-Agent and a single
   request (lazy `httpx` import — tests never call it). `rolling` left `False`
   and `deadline` `None` (no reliable source in this payload).
6. **Tests** — `ingestion/tests/`: fixture-only; `conftest.py` blocks the network
   at the socket layer (CLAUDE.md rule 5). Assert: every posting validates against
   §7; one posting per job (249, nothing dropped); `program_type` on real fixture
   titles; ambiguous titles → `unclassified` and retained.

**Result:** `32 passed`.

**Verify:**
```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q
```

**Next session:** pipeline orchestrator + dedup/change detection (§3.9) + DB
writes (§3.10). Do not start it here.
