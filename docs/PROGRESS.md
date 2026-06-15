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

## Session 6 — Verify Lever is wired into the pipeline (+ dispatch guard)

Review pass confirming the Lever adapter is not just coded/tested but actually
**used by** the pipeline. **No integration defect found** — everything from
Session 5 was already in place:

- `LeverAdapter` is imported in `ingestion/pipeline.py` and registered in
  `ADAPTER_REGISTRY` (`AtsType.lever: LeverAdapter`).
- `ingestion/config/registry.yaml` has an `ats_type: lever`, `enabled: true`
  source (Wealthfront), so `build_adapter()` dispatches it to `LeverAdapter`.
- `pytest -q` was green at 60 passed before this session.

**One coverage gap closed (the only change):** `run_ingestion` is always tested with
injected adapters, so `build_adapter()`'s `ats_type → class` lookup had no *direct*
test — the dispatch worked but wasn't asserted. Added
`test_registry_sources_dispatch_to_their_adapters` to
`ingestion/tests/test_cross_source_dedup.py`: it asserts the real registry's `lever`
source builds a `LeverAdapter` (and `greenhouse` a `GreenhouseAdapter`), and that
every registry entry resolves to a concrete `Adapter`. A future edit deleting the
registry line now fails this test loudly. No production code, schema, interface, or
adapter logic changed.

**Verify:**
```powershell
.\.venv\Scripts\python -m pytest -q          # 61 passed (was 60; +1 dispatch guard)
.\.venv\Scripts\python -m pytest -q ingestion/tests/test_cross_source_dedup.py::test_registry_sources_dispatch_to_their_adapters
```

**State now:** Lever is **implemented and integrated** — two live JSON adapters
(Greenhouse + Lever) on one shared interface, dispatched from the data registry,
feeding the dedup/storage pipeline, with the dispatch now explicitly guarded. 61
tests green.

**Doc note (not changed here, scope):** `CLAUDE.md`'s "Current status / where to
continue" block still summarizes the pre-Session-4 state (it names the pipeline
orchestrator as the next task). `docs/PROGRESS.md` is the authoritative log and is
current; refresh that CLAUDE.md block in a future housekeeping pass.

**Next session (unchanged):** the daily scheduler around `run_ingestion()` (§3.7;
`[OPEN]` APScheduler vs Celery, §8.2), or the Workday adapter family (§3.5; capture a
Workday fixture first).

## Session 7 — Coverage discovery: real IB target list + ATS classification (§3.1 / §3.5 / §3.6 / §8.1)

**No code, schema, registry, or test changes.** Discovery-only session: turn the two
placeholder non-IB sources into an evidence-backed, prioritized IB target list and
classify where each firm's *early-careers* postings actually originate, so the next
adapter decision (Workday vs custom) is driven by evidence. Method: inspected each
firm's public careers page / authoritative search-result URLs (primary sources only,
§8.1) and recorded the ATS host its apply links resolve to. Per scope, **only**
`greenhouse`/`lever` firms were to be probed — there were none to probe (see below).
`registry.yaml` was deliberately **not** touched (no unverified firms added).

**Candidate list (28 UK/EMEA IB firms, dossier priority order):** BB (11) → EB (7) →
MM (10). See the classification table below.

**Headline finding — zero IB targets use Greenhouse or Lever.** All 28 split between
**Workday** (10 firms) and a long tail of **custom/other** ATSes (17) plus one
**unknown** (1). Greenhouse/Lever dominate funds/fintech/quant (which is exactly why
the reference boards are Point72 (a fund) and Wealthfront (fintech) — both non-IB), not
traditional advisory/IB. **The existing GH+Lever adapters therefore reach 0 of 28 real
IB targets right now.** This is not a research artifact: targeted GH/Lever searches on
the likeliest candidates (boutiques, small UK firms — Peel Hunt, Numis, Lincoln,
Centerview) returned no `boards.greenhouse.io` / `jobs.lever.co` boards.

**Classification table** (ats_type · token-or-host · confidence). "Probe result" column
omitted — no GH/Lever firms, so no probes were run (correct per scope; never probed
Workday/custom). Confidence reflects ATS-host identification.

| # | Firm | Tier | ats_type | Token / host (early-careers) | Confidence |
|---|---|---|---|---|---|
| 1 | Goldman Sachs | BB | custom/other | `higher.gs.com` (in-house; Avature for events) | High |
| 2 | Morgan Stanley | BB | **workday** | tenant `ms`.wd5, site `External` | High |
| 3 | J.P. Morgan | BB | custom/other | Oracle HCM Cloud `jpmc.fa.oraclecloud.com` (CX_1001) | High |
| 4 | Bank of America | BB | custom/other | campus → `bankcampuscareers.tal.net` (TALENTDesk/Oleeo); *lateral* is Workday `ghr`.wd1 | High |
| 5 | Citi | BB | **workday** | tenant `citi`.wd5 (front-end `jobs.citi.com`, Phenom search); site TBD | High |
| 6 | Barclays | BB | **workday** | tenant `barclays`.wd3, site `External_Career_Site_Barclays` | High |
| 7 | UBS | BB | custom/other | `jobs.ubs.com/TGnewUI` (Kenexa/BrassRing; `partnerid`/`siteid`) | High |
| 8 | Deutsche Bank | BB | custom/other | own domain `careers.db.com` (vendor not exposed) | Medium |
| 9 | BNP Paribas | BB | custom/other | `bnpparibasgt.taleo.net` (Taleo) + `bnpparibas.tal.net` | High |
| 10 | HSBC | BB | custom/other | `portal.careers.hsbc.com` (Phenom) | High |
| 11 | Société Générale | BB | custom/other | **SmartRecruiters**, token `SocieteGenerale4` (public JSON API) | High |
| 12 | Centerview Partners | EB | custom/other | in-house `centerviewpartners.com/postings.aspx` | High |
| 13 | Evercore | EB | custom/other | `evercore.tal.net` (TALENTDesk/Oleeo) | High |
| 14 | Lazard | EB | custom/other | Oracle HCM Cloud `icbpjb.fa.ocs.oraclecloud.com` | High |
| 15 | Rothschild & Co | EB | **workday** | tenant `rothschildandco`.wd3 (lateral site `Rothschildandco_Lateral`; EC site TBD) | High |
| 16 | Moelis & Company | EB | **workday** | tenant `moelis`.wd1 (global univ programs); UK/US events on `moelis-careers.tal.net` | High |
| 17 | PJT Partners | EB | **workday** | tenant `pjtpartners`.wd1, site `Careers` | High |
| 18 | Perella Weinberg | EB | **workday** | tenant `pwp` on `wd1.myworkdaysite.com/recruiting/pwp` (campus site TBD) | High |
| 19 | Houlihan Lokey | MM | **workday** | tenant `hl`.wd1, site `Campus` | High |
| 20 | Jefferies | MM | custom/other | `jefferies.tal.net` (TALENTDesk/Oleeo) | High |
| 21 | Macquarie | MM | **workday** | tenant `mq`.wd3, site `CareersatMQ` | High |
| 22 | Nomura | MM | custom/other | `nomuracampus.tal.net` (TALENTDesk/Oleeo) | High |
| 23 | Mizuho | MM | **workday** | tenant `mizuhogroup`.wd102 / `mizuho`.wd1 (site `mizuhoamericas`) | High |
| 24 | RBC Capital Markets | MM | custom/other | Phenom-powered career site | High |
| 25 | William Blair | MM | custom/other | `williamblair.avature.net` (Avature) | High |
| 26 | Lincoln International | MM | **unknown** | own-domain careers; vendor not confirmed; **not** GH/Lever | Low |
| 27 | Numis (Deutsche Numis) | MM | custom/other | folding into Deutsche Bank `careers.db.com` (assumed) | Low |
| 28 | Peel Hunt | MM | custom/other | `careersat.peelhunt.com` (Teamtailor; public JSON API) | High |

**Reachability tally (the decision driver):**
- **Reachable RIGHT NOW (GH+Lever adapters): 0 of 28.** No probes run — there were no
  GH/Lever tokens to probe. The only GH/Lever boards we have remain the non-IB
  reference sources (Point72/GH, Wealthfront/Lever).
- **Need Workday (§3.5): 10** — MS, Citi, Barclays (3 BBs); Rothschild, Moelis, PJT,
  Perella (4 EBs); Houlihan Lokey, Macquarie, Mizuho (3 MMs). (+ BofA's *lateral* board,
  not its campus one.) Single largest bucket; covers the BBs/EBs as §3.5 predicted.
- **Need custom/rendered (§3.6) or new JSON adapter: 17** — dominated by a
  **tal.net / TALENTDesk (Oleeo)** cluster of ~6 early-careers IB boards (BofA-campus,
  BNP, Evercore, Jefferies, Nomura, Moelis-UK/US), then Oracle HCM Cloud (JPM, Lazard),
  Phenom (HSBC, RBC), plus single instances of Avature (William Blair), BrassRing (UBS),
  Taleo (BNP), in-house (GS, Centerview, DB/Numis). Two are clean public-JSON sources
  shaped like GH/Lever and cheap to add later: **SmartRecruiters** (SocGen, token
  `SocieteGenerale4`) and **Teamtailor** (Peel Hunt).
- **Unknown: 1** — Lincoln International (confirm vendor with a click-through later).

**Dedup-collapse flag (§3.9, FLAG ONLY — key is locked, rule 2).** No GH/Lever IB firm
resolves, so nothing in *this* list to flag now. The substantive point: the coarse-region
collapse first seen on Point72's APAC roles (Session 4) will become **acute on the Workday
BBs**, which post one early-careers program (e.g. "2027 Summer Analyst") across many
offices under near-identical titles — Barclays London + Glasgow both map to `region=UK`
and collapse to one row under `firm+title+program_type+region`, hiding a genuinely
distinct opening. **Recommendation: time the `[OPEN]` dedup-key revisit (§8.2) to the
Workday adapter session**, when real multi-office BB data exists to decide whether the
region grain needs city/office. Not changed here.

**Recommendation — build the Workday adapter family next (§3.5).** It is the single
highest-leverage adapter: it unlocks 10 of 28 targets (incl. 3 BBs + 4 EBs) that are
reachable *no other way*, and "just add GH/Lever firms to the registry first" is **not
available** — there are none. Capture a Workday fixture first (rule 5); the per-tenant
table above is the seed (tenant + dc + site). The `[OPEN]` tenant-variation strategy
(§8.2) and the dedup-key revisit should both be decided in that session. After Workday,
the next cluster is tal.net/Oleeo (§3.6 rendered, ~6 firms); SmartRecruiters + Teamtailor
are cheap JSON follow-ups (2 firms) that reuse the GH/Lever adapter pattern.

**Verify:** discovery only — no code changed, so the suite is unaffected (61 passed as of
Session 6). The classification table above is the durable artifact for the Workday session.

**State now:** ingestion layer unchanged (2 JSON adapters, pipeline, storage, migration,
61 tests green). We now have an evidence-backed, prioritized 28-firm IB target list with
ATS classification; the next adapter is **Workday**, not another JSON board.

**Next session:** Workday adapter family (§3.5) — capture a Workday fixture first (start
with a confirmed tenant from the table, e.g. `barclays`.wd3 / `External_Career_Site_Barclays`
or `ms`.wd5 / `External`), then build `WorkdayAdapter` on the shared interface; resolve
the `[OPEN]` tenant-variation strategy and the dedup-key region-grain revisit there.

## Session 8 — Workday adapter family + first real IB firm + region-collapse measurement (§3.5 / §3.9)

Built the Workday adapter on the existing shared interface and **measured** (did not
fix) the coarse-region dedup collapse on real bulge-bracket data. The dedup key stays
**locked** (rule 2) — the key revisit remains a separate later session (per Session 7).
Adapters stayed stateless and DB-free (§3.2); the schema, classifiers, pipeline
orchestrator logic, and storage were **not** changed beyond one registry field, one
registry data entry, and the one-line adapter wiring.

**Fixture captured first (rule 5).** No Workday fixture existed, so before any parsing
logic I captured one from a confirmed tenant —
`ingestion/tests/fixtures/workday_barclays.json` (Barclays / `barclays`.wd3 / site
`External_Career_Site_Barclays`). Workday postings come from a **paginated JSON POST** to
`{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (§3.5); polite, minimal
requests with an honest User-Agent and a 2s inter-request delay (§3.12). The listing is
**shallow** (title / externalPath / locationsText / a *relative* `postedOn` / bulletFields
`JR-…`), so I also captured **one** verbatim per-posting **detail** follow-up
(`GET …/{externalPath}` → `jobPostingInfo` with `jobDescription`, an absolute `startDate`,
and `externalUrl`). **Fixture holds 23 postings** (early-careers "graduate" search; 2
paginated pages of 20 + 3) plus the one detail example.

*Fixture note (honest, per the Session-4 precedent of letting data falsify assumptions):*
this Barclays external site is lateral/experienced-role heavy and Workday free-text search
is loose. "graduate" was the most defensibly early-careers term (it surfaces the real
"Technology Developer Graduate Programme 2026" and one detail example); probes of
"internship"/"summer analyst"/"spring"/"industrial placement" each returned either ~1
unrelated senior role or framework-keyword noise (e.g. "spring" → Java *Spring* roles), so
they were not used. The genuine multi-office UK early-careers programmes are not surfaced
by free-text on this site (likely a job-family facet or a separate early-careers site).

**Files added/touched:**
1. **`ingestion/adapters/workday.py`** — `WorkdayAdapter(Adapter)`. `parse()` maps the
   Workday payload to §7 and calls the **shared** §3.8 classifiers
   (`classify_program_type` / `extract_division` / `map_region`) — no parallel classifier.
   Workday specifics: the payload is a `{"total": N, "jobPostings": [...]}` object (POST
   result), not a flat array (Lever) or `{"jobs": …}` (Greenhouse); `title`→`role_title`,
   `locationsText`→`location`, `bulletFields[0]` (the `JR-…` req id)→`source_id`,
   `externalPath`→a built public `source_url`. The shallow listing has no description or
   absolute date, so `raw_description`/`open_date` come from the per-posting **detail**
   (`jobDescription` / ISO `startDate` / `externalUrl`), which `fetch()` attaches under
   `"_detail"`; `parse()` reads it when present and falls back to listing-only fields
   otherwise (honest `None`, never guessed from the relative "Posted N Days Ago").
   `deadline` is `None` (no Workday field) and `rolling` `False` (as the others).
   `fetch()` lazy-imports httpx, does the **paginated POST** (offset/limit loop to `total`)
   + per-posting detail follow-up, honest UA, polite per-request delay — tests never call it.
2. **`[OPEN]` §8.2 tenant variation — this session chose CONFIG, not subclasses (a
   proposal, not a lock).** Every per-tenant quirk (tenant token, dc number `wd{n}`, site
   name, early-careers `search_text`/`applied_facets`) lives in the registry entry's new
   `config` block, so a new Workday firm is a **data** entry, not a subclass. The adapter
   reads tenant from `endpoint_or_url` (consistent with greenhouse/lever) and dc/site/filter
   from `config`. A module comment marks config-vs-subclass as still `[OPEN]` and names the
   escape hatch (a thin per-tenant subclass) if a tenant ever needs behaviour config cannot
   express.
3. **`ingestion/registry.py`** — added one optional typed field to `SourceEntry`:
   `config: dict[str, Any] | None = None` (ATS-specific config kept in registry **data**,
   §2.3). greenhouse/lever leave it `None`.
4. **`ingestion/config/registry.yaml`** — added the **first real IB firm**: Barclays
   (a bulge bracket) / `workday` / tenant `barclays`, `config: {dc: wd3, site:
   External_Career_Site_Barclays, search_text: graduate}`. The two placeholder JSON sources
   (Point72/GH, Wealthfront/Lever) are kept as-is — they are test-fixture reference boards,
   not targets.
5. **`ingestion/pipeline.py`** — wired Workday into `ADAPTER_REGISTRY` (one line,
   `AtsType.workday: WorkdayAdapter`) + its import. Orchestrator logic otherwise unchanged.
6. **`ingestion/tests/test_workday_adapter.py`** — fixture-only adapter tests mirroring the
   Lever set: the `{"jobPostings": …}` shape, pagination aggregation (23 across 2 pages),
   empty/missing-key robustness, one-posting-per-job (nothing dropped), every posting
   validates against §7, listing-only boundary mapping (built `source_url`), detail-enriched
   mapping (`raw_description`/`open_date`/`externalUrl`), no deadline + `rolling False`,
   derived fields equal the §3.8 classifier output for each row, and unclassifiable titles
   kept not dropped (22 of 23).
7. **`ingestion/tests/test_workday_collapse.py`** — the region-collapse **report**
   (Option A, measure don't fix): runs the pipeline over the Workday fixture and asserts the
   collapse count, that it is logged on the run row (`ingestion_runs.collapsed`) and in the
   per-source breakdown, and characterises the collapsing group.

**⚠️ Region-collapse measurement (the deliverable for the later `[OPEN]` revisit).**
Over the real Barclays fixture under the locked §3.9 key (firm + normalized_title +
program_type + region):

> **found 23 → unique 21 → COLLAPSED = 2** (logged on `ingestion_runs.collapsed`).

**The 2 collapses are NOT coarse-region flattening of distinct offices.** They are three
identical "Third Party Risk Manager" postings in the **same** office ("Noida, Candor
TechSpace") collapsing 3 → 1 — a genuine same-title/same-location duplicate, exactly what
the key *should* merge. **Zero** genuinely-distinct office/city openings were flattened by
region on this slice. So Session 7's prediction that region-flattening would be *acute* on
the Workday BBs was **not** borne out by this particular early-careers slice — because the
site is lateral-heavy with distinct titles, and the region classifier currently maps
Pune/Noida/Chennai/Prague to `unknown` (a keyword-config gap, not an adapter bug), so even
repeated titles don't all share a region. **Evidence for the revisit:** to actually
stress-test the region grain, capture a true multi-office UK early-careers programme (e.g.
a Summer Analyst across London/Glasgow/Belfast), which this site's free-text search did not
surface; and the region keyword set needs Indian/Czech cities added before any region-grain
conclusion. Key unchanged — this is the evidence, not a fix.

**Verify:**
```powershell
.\.venv\Scripts\python -m pytest -q          # 76 passed (was 61; +15 Workday)
.\.venv\Scripts\python -m pytest -q ingestion/tests/test_workday_adapter.py ingestion/tests/test_workday_collapse.py
```
Key tests: `test_pagination_aggregates_all_pages`, `test_detail_followup_populates_description_url_and_open_date`,
`test_derived_fields_use_the_shared_classifiers`, `test_unclassifiable_postings_kept_not_dropped`,
`test_workday_fixture_collapse_count` (found 23 / new 21 / collapsed 2),
`test_collapse_is_logged_on_the_run_row`,
`test_collapsed_group_is_a_same_office_duplicate_not_region_flattening`.

**State now:** ingestion layer has **three** live JSON/HTTP adapters — Greenhouse + Lever +
Workday — on one shared interface, dispatched from the data registry into the dedup/storage
pipeline. The registry now holds its **first real IB target** (Barclays, a BB). 76 tests
green. Scheduler and the other adapters (tal.net/Oracle/Phenom) deliberately NOT built
(rule 7); the dedup key and all locked decisions unchanged.

**Next session:** either the daily scheduler around `run_ingestion()` (§3.7; `[OPEN]`
APScheduler vs Celery, §8.2), or the **dedup-key region-grain revisit** (`[OPEN]` §8.2) —
which now needs a true multi-office UK early-careers Workday capture (a facet-filtered or
separate early-careers site) plus region-keyword coverage for the missing cities before it
can decide whether the region grain needs city/office. After that, the tal.net/Oleeo
cluster (§3.6, ~6 firms) and the cheap SmartRecruiters + Teamtailor JSON follow-ups.
