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

## Session 3 — Repository setup, GitHub sync, and cross-session context

Infrastructure/docs only — **no application (ingestion) code changed.**

- **Relocated** the project to `…\.claude\sessions\CVapp`; recreated `.venv` there;
  `pytest` still green (**32 passed**) at the new location.
- **Version control:** initialized git (branch `main`); added `.gitignore` (excludes
  `.venv/`, `__pycache__/`, `.pytest_cache/`); connected to GitHub at
  `https://github.com/demiryyurtsever/CVapp.git`. Sync is **manual** (no auto-push hook)
  by user choice; pushes are offered via an interactive in-chat prompt.
- **Cross-session context:** added `CLAUDE.md` (standing rules, architecture, build order,
  sync workflow, where-to-continue pointer) and `docs/Project_Dossier.md` (in-repo copy of
  the canonical dossier; the `.docx` original stays in Downloads).
- **Workflow:** made updating this `PROGRESS.md` a required step after any code work
  (CLAUDE.md standing rule 9).

**State now:** ingestion foundations complete and pushed to GitHub; project is
version-controlled and self-documenting for future sessions.

**Next session (unchanged):** pipeline orchestrator + dedup/change detection (§3.9) +
DB writes/storage (§3.10).

## Session 4 — Pipeline orchestrator + DB storage (§3.7 / §3.9 / §3.10 / §3.11)

Built the ingestion pipeline + its storage on top of the existing adapters. The
adapters, classifiers, schema, and registry were **not** modified — all DB writes
live in the new pipeline (§3.2: adapters stay stateless and DB-free).

**Files added:**
1. **`ingestion/db.py`** — SQLAlchemy `Base`, engine/session factories, `DATABASE_URL`
   resolution (prod: `postgresql+psycopg://…`), and a SQLite FK-pragma listener so
   the portable models enforce the `postings.firm → firms.name` FK under tests.
2. **`ingestion/storage.py`** — ORM models for the three §3.10 tables: `postings`
   (canonical §7), `firms` (name, tier, notes), `ingestion_runs` (run id, timestamps,
   per-source found/new/closed/reappeared/collapsed counts + errors). Indexes on
   `(firm, program_type, region, status)` and on `deadline` (§3.10), plus a unique
   index on `dedup_key`. Enums stored as portable VARCHAR+CHECK (`native_enum=False`).
3. **`ingestion/pipeline.py`** — `run_ingestion()` orchestrator (§3.7): load registry
   → dispatch each enabled source to its adapter → collect §7 Postings → dedup within
   the run (§3.9) → diff vs DB → write → log the run. **Sequential dispatch only**;
   adapter concurrency is marked `[OPEN]` (§8.2) in a comment and not built. An adapter
   that raises is caught, logged with its source id, and skipped — one broken source
   never halts the run (§3.2). Lifecycle (§3.9): new key → insert + `first_seen`;
   present → bump `last_seen`, reset misses; absent N consecutive runs → `closed`
   (N config, default 2, via a per-row `consecutive_misses` counter so a single missed
   run never closes a live posting); closed key returns → `reappeared`.
4. **Alembic** — `alembic.ini` + `ingestion/migrations/{env.py,script.py.mako,versions/0001_initial_schema.py}`.
   `0001` creates all three tables + indexes; URL comes from `DATABASE_URL` (SQLite
   fallback for offline checks). `alembic upgrade head` verified against a scratch DB.
5. **Tests** — `ingestion/tests/test_storage.py` (§7 field-for-field guard + migration
   ↔ model parity) and `ingestion/tests/test_pipeline.py` (driven by the
   `greenhouse_point72.json` fixture; the stub overrides only `fetch()` so the real
   parse + classifiers run). DB fixtures (in-memory SQLite, sockets still blocked)
   added to `conftest.py`.

**§7 field-for-field check (as requested):** the `postings` table carries all 17
canonical §7 fields with matching types; the only extra columns are three documented
§3.9/§3.11 bookkeeping fields — `dedup_key`, `consecutive_misses`, `source`. A test
asserts every §7 field is present and that nothing else but those three has crept in
(drift is flagged, not silently reconciled).

**⚠️ Decision surfaced — fixture yields 233 unique, not 249.** Applying the locked
§3.9 dedup key (`firm + normalized_title + program_type + region`) to the real Point72
fixture collapses the **249** parsed postings to **233** unique keys (16 collapsed
across 13 keys): some are genuine same-title/same-location duplicates, others are
distinct offices flattened by coarse region (e.g. Singapore + Hong Kong → APAC, same
title). This is **not** non-determinism — the §5 stability guard holds (two runs over
the same fixture flip nothing and insert nothing). The dedup key is locked, so 233 new
on run 1 is the spec-faithful outcome; the prompt's "all 249" assumption was falsified
by the data. Per the user's call, the 16 collapsed rows are now **logged**
(`ingestion_runs.collapsed`, per-source breakdown) so they are observable, not invisible.

**Verify:**
```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pytest -q          # 45 passed
# migration applies end-to-end:
$env:DATABASE_URL = "sqlite:///./_scratch.db"; .\.venv\Scripts\python -m alembic upgrade head
```

Key tests: `test_run1_inserts_unique_postings_as_new` (233 new / found 249 / 16 collapsed),
`test_rerun_same_fixture_is_stable` (§5 guard: run 2 → 0 new/closed/reappeared, only
`last_seen` bumped), `test_absent_for_n_runs_flips_to_closed`,
`test_closed_posting_that_returns_reappears`, `test_one_broken_source_is_logged_and_skipped`,
`test_postings_table_matches_section7_field_for_field`, `test_alembic_migration_matches_models`.

**State now:** ingestion layer has a working, tested pipeline + Postgres storage
(driven on SQLite in tests). 45 tests green. Scheduler/API/frontend deliberately NOT
built (standing rule 7).

**Next session:** wrap `run_ingestion()` in the daily scheduler (§3.7 cadence; the
APScheduler-vs-Celery choice is `[OPEN]`, §8.2), or begin the Lever adapter (§3.4) to
exercise multi-source dedup. Neither started here.

## Session 5 — Lever adapter + first cross-source dedup test (§3.4 / §3.9)

Built the Lever adapter and nothing else (standing rule 7). Adapters stayed
stateless and DB-free (§3.2); the existing schema, classifiers, registry loader,
and pipeline orchestrator logic were **not** changed beyond one registry data
entry and the one-line adapter wiring.

**Fixture captured first (rule 5).** No Lever fixture existed, so before writing
any parsing logic I captured one real board:
`ingestion/tests/fixtures/lever_wealthfront.json` — a single polite GET to
`api.lever.co/v0/postings/wealthfront?mode=json` (honest UA), raw and unmodified,
**15** postings. (Probed candidate tokens politely; Plaid's board was empty `[]`,
Wealthfront's was populated.) Wealthfront is a fintech wealth-management firm used
as the reference live Lever source — like Point72 on Greenhouse, it is not an IB
firm, so its `firm_tier` is a documented placeholder.

**Files added/touched:**
1. **`ingestion/adapters/lever.py`** — `LeverAdapter(Adapter)`. `parse()` maps the
   Lever payload to §7 and calls the **shared** §3.8 classifiers
   (`classify_program_type` / `extract_division` / `map_region`) — no parallel
   classifier. Lever specifics handled: the payload is a **flat JSON array** (not a
   `{"jobs": […]}` wrapper); `text`→`role_title`, `categories.location`→`location`,
   `categories.{department,team}`→division input, `hostedUrl`→`source_url`,
   `id`→`source_id`; `createdAt` is **epoch milliseconds**→`open_date`; and the
   description, which Lever **splits** across `description`/`lists`/`additional`, is
   rejoined into one HTML `raw_description` so the Layer 2 parser sees the whole
   posting. `deadline` is `None` (no Lever field) and `rolling` `False` (as
   Greenhouse). `fetch()` lazy-imports httpx, one request, honest UA — tests never
   call it.
2. **`ingestion/config/registry.yaml`** — added one **data** entry: Wealthfront /
   `lever` / token `wealthfront` (rule 6 — registry config, not hardcoded).
3. **`ingestion/pipeline.py`** — wired Lever into `ADAPTER_REGISTRY` (one line,
   `AtsType.lever: LeverAdapter`) + its import. Orchestrator logic otherwise
   unchanged.
4. **`ingestion/tests/test_lever_adapter.py`** — fixture-only adapter tests: flat-
   array shape, one-posting-per-job (nothing dropped), every posting validates
   against §7, boundary-field mapping, `createdAt`→date, no deadline,
   `raw_description` rejoins the split fragments, derived fields come from the §3.8
   helpers, and — explicitly — all 15 titles are `unclassified` and **kept, not
   dropped**.
5. **`ingestion/tests/test_cross_source_dedup.py`** — the **first real test of the
   multi-source premise (§3.9)**, driving the pipeline over the Greenhouse +
   Lever fixtures **together**. Asserts both halves of §3.9: (a) different firms
   never merge — the firm-scoped key gives **zero** Point72↔Wealthfront key overlap,
   so combined `new` = 233 + 15 = **248** from `found` **264**, with per-source
   counts kept separate and a §5 stability rerun flipping nothing; and (b) the
   **same role on two surfaces collapses** — re-presenting three real Wealthfront
   postings via a second source collapses them to one row each (first-seen/Lever URL
   wins, collapse logged against the duplicate's source).

**Verify:**
```powershell
.\.venv\Scripts\python -m pytest -q          # 60 passed (was 45; +15 new)
.\.venv\Scripts\python -m pytest -q ingestion/tests/test_lever_adapter.py ingestion/tests/test_cross_source_dedup.py
```
Key tests: `test_pipeline_runs_greenhouse_and_lever_together` (found 264 / new 248 /
collapsed 16, per-source split), `test_different_firms_never_merge_firm_scoped_key`,
`test_combined_run_is_stable_on_rerun`, `test_same_role_on_two_sources_collapses_to_one`,
`test_unclassifiable_postings_kept_not_dropped`.

**State now:** ingestion layer has two live JSON adapters (Greenhouse + Lever) on
one shared interface, dispatched from the data registry, feeding the dedup/storage
pipeline; multi-source dedup is now tested against two real boards. 60 tests green.
Scheduler / Workday / other adapters deliberately NOT built (rule 7).

**Next session:** the daily scheduler around `run_ingestion()` (§3.7; APScheduler vs
Celery is `[OPEN]`, §8.2), or the Workday adapter family (§3.5, covers the BBs;
tenant-variation strategy is `[OPEN]`, §8.2). Capture a Workday fixture first.
