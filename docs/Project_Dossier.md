# IB Internship Application Platform
## Project Dossier — Master Context Document for LLM-Assisted Development
Version 1.0 — June 2026 — Author: Demir

> **Provenance:** This markdown is the in-repo copy of the canonical dossier
> (originally authored as `IB_Platform_Project_Dossier.docx`). It is the single
> source of truth for the project. Read it before acting.

## How to use this dossier

This document is the single source of truth for the project. When working on any
individual section with an LLM, paste Section 1 (Project Overview), Section 2
(Architecture and Conventions), the canonical schema in Section 7, plus the
specific subsection you are building. Each subsection is written to be
self-contained context: it states what the component does, its inputs and
outputs, its boundaries with neighbouring components, and the decisions already
locked so the LLM does not relitigate them. Open decisions are explicitly marked
`[OPEN]` — those are the only things an LLM session should be allowed to propose
changes to.

---

## 1. Project Overview

### 1.1 What this is
A vertically integrated platform for students applying to investment banking
internships (spring weeks, summer internships, off-cycle roles, graduate
schemes), focused on the UK/EMEA market. It fuses three capabilities that
currently exist only as separate products: (1) a self-maintained database of IB
internship openings scraped from primary sources, (2) an LLM-powered CV tailoring
engine built on a banking-specific rubric, and (3) a browser extension that
autofills the initial application form, with the user always making the final
submit.

### 1.2 Why it exists / market position
Each layer has incumbents: Trackr (formerly Bristol Tracker), Canary Wharfian and
Bright Network own openings tracking; Teal, Jobscan, TailoredCV.ai and
FinanceCVCheck own CV tailoring; Simplify owns autofill. Nobody fuses all three
into one closed loop: opening detected → CV auto-tailored to that bank/group →
form autofilled → user reviews and submits. The integration is the product. The
defensible asset is the openings database (Layer 1); the tailoring and autofill
layers are commodity techniques.

### 1.3 Hard constraints (locked — do not relitigate in LLM sessions)
- **No third-party data dependencies.** The openings database is built by scraping
  primary sources (bank career pages and their ATS endpoints) directly. Never
  scrape a competitor's curated database (e.g. Trackr) — their compiled listings
  are protected (UK database right + ToS) and copying them is both legally exposed
  and strategically weak.
- **No silent auto-submit.** The autofill layer fills forms; the human reviews and
  clicks submit. Silent submission risks users' accounts being flagged at the
  banks they want to work for, and violates portal ToS.
- **Scraping posture:** primary sources only, respect robots.txt and rate limits,
  prefer official JSON/ATS endpoints, identify the bot honestly, never hammer a
  target.
- **CV tailoring must never invent achievements.** The LLM selects and rephrases
  content from the user's structured master CV; it does not fabricate.
- **Python backend.** Solo developer, strong Python background, prior DOCX/XML
  formatting experience.

### 1.4 Scope
- **v1 scope:** all three layers — openings DB + tracker UI, CV tailoring, assisted
  autofill extension. Built in dependency order (ingestion first, autofill last).
- **Later additions:** firm-tier filtering exposed in UI (BB / EB / MM / boutique —
  the `firm_tier` field is seeded in the schema from day one so this is a WHERE
  clause + UI toggle later); sub-24-hour ingestion freshness; expanded firm
  coverage beyond the initial 20–30; region expansion beyond UK/EMEA; keyword-gap
  analytics dashboard.
- **Explicit non-goals:** automating HireVue video interviews or online tests;
  cover-letter mass generation in v1; mobile app; any feature that submits
  applications without user review.

---

## 2. Architecture and Conventions

### 2.1 System shape
Three independent layers behind one API, each testable in isolation. Layer 1
(ingestion) feeds the openings database. Layer 2 (tailoring) consumes a posting +
the user's structured CV. Layer 3 (autofill) consumes the user profile + tailored
CV inside a browser extension. The "tracker framework" (Trackr-style board) is not
a separate layer — it is the canonical schema (Section 7) plus the frontend board
UI rendered over it.

### 2.2 Tech stack

| Component | Choice | Notes |
|---|---|---|
| API | FastAPI (Python) | Serves frontend + extension; async-friendly |
| Database | PostgreSQL | Openings, users, CVs, ingestion logs |
| Scheduler | `[OPEN]` APScheduler vs Celery+Redis | Daily cadence in v1; APScheduler likely sufficient for solo v1 |
| Scraping | httpx (JSON endpoints) + Playwright (rendered pages) | Playwright only where no structured endpoint exists |
| LLM | Direct API calls (thin wrapper) | No orchestration framework (LangChain etc.) — single well-shaped calls |
| CV rendering | Fixed DOCX template + content injection | Deterministic layout; model supplies content only |
| Frontend | React (web app) | Board/calendar UI, CV management |
| Extension | Browser extension (Manifest V3, content scripts) | Built last; standard public form-fill techniques |

#### 2.2.1 LLM cost posture (locked)
Tailoring cost is a non-issue at v1 scale: a tailoring call is a few thousand
tokens in, ~1–2k out — a fraction of a cent per CV on a mid-tier model. Do not
spend engineering time on LLM cost optimization in v1; do not introduce caching
layers or model-routing complexity for cost reasons. The thin-wrapper decision
stands regardless of cost considerations.

### 2.3 Conventions for all LLM-assisted sessions
- Code is organised per-layer: `/ingestion`, `/tailoring`, `/autofill` (extension
  repo may be separate), `/api`, `/frontend`.
- Adapters share one interface; adding a firm on a known ATS is a config entry,
  not code.
- All postings are normalized into the canonical schema (Section 7) at the adapter
  boundary — no raw shapes leak past adapters.
- Configuration (source registry, field mappings, rubric keyword sets) lives in
  data files, not code, so it can be edited without redeploying.
- Every ingestion run is logged (run id, source, postings found/new/closed,
  errors) for observability.

---

## 3. Layer 1 — Openings Ingestion and Database

The core asset and the only defensible moat. Everything here is about freshness,
coverage, and maintainability. Build this layer first.

### 3.1 Source registry
- **What it is:** a configuration file (YAML/JSON) listing every target firm and
  where its postings originate. One entry per firm-source.
- **Fields per entry:** `firm_name`, `firm_tier` (BB/EB/MM/boutique), `ats_type`
  (greenhouse | lever | workday | custom), `endpoint_or_url`, `region_scope`,
  `enabled` flag, `polling notes`.
- **Behaviour:** the scheduler iterates the registry and dispatches each entry to
  the adapter matching its `ats_type`. Adding a firm on a supported ATS = adding a
  registry entry only.
- **Initial coverage:** start with 20–30 firms — bulge brackets first, then elite
  boutiques, then middle market. Classify each source on discovery as JSON (easy),
  rendered-scrape (medium), or manual-check (hard); build JSON sources first.

### 3.2 Adapter interface
- **Contract:** every adapter implements two operations — `fetch()` returning raw
  source payloads, and `parse(raw)` returning a list of normalized posting objects
  in the canonical schema. Adapters are stateless; they do not touch the database.
- **Why:** isolation. When a bank changes its page, exactly one adapter breaks and
  the run log shows which. The core pipeline never changes when sources change.
- **Error behaviour:** an adapter failure is caught, logged with the source id, and
  skipped — one broken source never halts the run.

### 3.3 Greenhouse adapter
- **Source:** public JSON API: `boards-api.greenhouse.io/v1/boards/{company}/jobs`
  (optionally `?content=true` for descriptions). No auth.
- **Work:** map JSON fields to canonical schema; classify `program_type` from title
  text (spring/summer/off-cycle/graduate keyword rules); extract location/region.
  The easiest adapter — build it first as the reference implementation.

### 3.4 Lever adapter
- **Source:** `api.lever.co/v0/postings/{company}?mode=json`. No auth. Structure
  similar in spirit to Greenhouse.
- **Work:** same mapping pattern as Greenhouse; share the title-classification
  helper.

### 3.5 Workday adapter
- **Source:** most bulge brackets. Postings come from a JSON POST to a per-tenant
  endpoint of the form
  `{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`, paginated, with
  a search/filter payload.
- **Work:** per-tenant configuration (tenant id, site name, any facet filters for
  early-careers postings); pagination handling; response shapes vary slightly per
  tenant so keep tenant quirks in config. This is the highest-value, fiddliest
  adapter family — it covers the BBs.
- `[OPEN]`: how much per-tenant variation to absorb in config vs subclassing;
  senior-engineer input wanted.

### 3.6 Custom/rendered-page adapter
- **Source:** banks on custom portals or vendors (Eightfold, Phenom, in-house). No
  structured endpoint.
- **Work:** Playwright renders the page; per-source CSS/XPath selectors (kept in
  config) extract postings. Most brittle adapter type — only use where no JSON
  source exists, and prefer deferring these firms to post-v1 if coverage allows.

### 3.7 Scheduler and pipeline orchestration
- **Cadence:** daily in v1. Sub-24-hour freshness is a later optimization (rolling
  deadlines reward being early, so this matters eventually — but a working daily
  pipeline beats a perfect hourly one that does not exist).
- **Flow per run:** load registry → dispatch enabled sources to adapters
  (concurrency `[OPEN]`) → collect normalized postings → dedup → diff against DB
  (new/changed/missing) → write → log run summary.
- **Rate limiting:** per-source delay + honest User-Agent. Never parallel-hammer a
  single host.

### 3.8 Normalization and classification
- **What it is:** shared helpers used by all adapters — title → `program_type`
  classification (keyword rules: "spring", "insight" → spring_week; "summer
  analyst" → summer; "off-cycle", "industrial placement" → off_cycle; "graduate",
  "full-time analyst" → graduate), division extraction (IBD/Markets/Research/etc.),
  location → region mapping, date parsing.
- **Edge cases:** ambiguous titles get `program_type=unclassified` and surface in an
  admin review queue rather than being silently dropped or misfiled.

### 3.9 Deduplication and change detection
- **Dedup key:** firm + normalized_title + program_type + region. The same role
  frequently appears on multiple surfaces (career page, ATS, LinkedIn).
- **Lifecycle:** `first_seen` set on first appearance; `last_seen` updated each run
  the posting is present; a posting absent for N consecutive runs flips to
  `status=closed`; a closed posting reappearing flips to `status=reappeared` (banks
  reopen unfilled roles weeks later — a genuinely useful signal to surface to
  users).

### 3.10 Storage
- **Tables:** `postings` (canonical schema, Section 7); `firms` (name, tier, notes);
  `ingestion_runs` (run id, timestamp, per-source counts, errors); users / cv /
  profile tables for Layers 2–3.
- **Indexes:** on (firm, program_type, region, status) for the board UI's filter
  queries; on `deadline` for the calendar view.

### 3.11 Observability
- **Per-run log:** postings found / new / closed per source; adapter errors with
  source id.
- **Freshness metric per source:** time since last successful run.
- **Coverage check:** alert if a normally-active source returns zero postings
  (likely a broken adapter, not an empty board).

### 3.12 Legal and ethical posture (locked)
- Scrape primary sources only (banks and their ATS endpoints). Never a competitor's
  compiled database.
- Respect robots.txt and rate limits; prefer official JSON endpoints; identify the
  bot honestly.
- Store only public posting metadata — no personal data is scraped.

---

## 4. Layer 2 — CV Tailoring Engine

The fastest win. A thin LLM wrapper (direct API calls, no orchestration framework)
over a banking-specific rubric. The value is the rubric and the structured-input
design, not the wrapper.

### 4.1 Master CV store (structured)
**Design (locked):** the user's CV is stored as structured data, never a text blob
— discrete objects for education entries, experience entries (each with role, org,
dates, and a list of bullet objects), skills, projects, awards. Tailoring then
means selecting and rephrasing a subset, which is reliable and renderable;
free-form rewriting of a blob produces formatting drift and hallucination.

**Ingest:** v1 can use a guided form to build the structure; CV-upload-and-parse
(DOCX/PDF → structure, LLM-assisted) is a fast follow.

### 4.2 Posting parser
**What it does:** takes a posting's description text and extracts: division/group,
required skills and keywords, qualities emphasised, and any explicit requirements.
Output is a structured requirements object.

`[OPEN]` One-call vs two-call design: option A — single LLM call (CV + posting →
tailored CV). Option B — call 1 extracts requirements/keywords, call 2 tailors
against them. B costs marginally more but yields the keyword-gap report as a free
by-product and sharper tailoring. Leaning B; engineer input welcome.

### 4.3 Banking rubric (the actual IP of this layer)
What it encodes: the publicly documented conventions of a competitive banking CV,
expressed as prompt instructions + keyword sets kept in config:
- Deal-Action-Result bullet structure; quantified outcomes wherever the master CV
  provides numbers.
- Technical keyword sets per division: IBD (DCF, LBO, comps, precedent
  transactions, merger models, M&A), Markets, Research — kept as editable config
  lists.
- Group-specific language: TMT vs FIG vs healthcare vs natural resources postings
  use distinct vocabulary; mirror the posting's group language.
- Strict one-page UK format; rigid section order (education first for students);
  concise bullets; no photos/graphics.
- **Hard rule:** select and rephrase only — never fabricate achievements, never add
  skills not present in the master CV. The keyword-gap report is where missing
  items are surfaced to the user instead.

### 4.4 Tailoring service
- **Input:** structured master CV + parsed posting requirements + rubric.
- **Output:** a tailored-CV content object (which experiences/bullets selected,
  rephrased text per bullet, ordering) + a keyword-gap report ("this TMT posting
  emphasises X; your CV does not surface it").
- **Validation:** post-process check that every output bullet maps to a source
  bullet in the master CV (anti-fabrication guard); length budget check for the
  one-page constraint before rendering.

### 4.5 Renderer
**Design (locked):** a fixed one-page banking DOCX template; the tailoring output is
injected into named slots. The model never controls layout. PDF export via headless
conversion. Deterministic, testable, and avoids the OMML/layout pain of
model-generated formatting.

---

## 5. Layer 3 — Assisted Autofill (build last)

Highest-risk, most brittle, least defensible layer. Deliberately scheduled last so
it never blocks a working v1.

### 5.1 User profile store
**What it holds:** standing application answers — contact details, education
history, work authorisation, languages, standard demographic/disclosure answers the
user chooses to store — as structured fields the extension can map onto forms.

### 5.2 Browser extension
**Mechanism:** Manifest V3 content script. On a recognised application page it
detects form fields, maps stored profile + the tailored CV file into them, fills,
highlights what it filled, and stops. The user reviews and clicks submit. This is
the standard, public form-fill technique — the same underlying approach commodity
tools use.

**Hard rule (locked):** no silent submission, no CAPTCHA circumvention, no headless
automation of the portals. If a portal blocks programmatic filling, degrade
gracefully to copy-paste assistance rather than fighting bot detection.

### 5.3 Per-portal field mapping
**Design:** mappings from portal field signatures (label text, input name/id
patterns) to profile fields, kept as per-portal config data (Workday, Greenhouse,
etc.), so new portals are config additions. Expect ongoing maintenance — this is
the layer that rots fastest.

`[OPEN]`: heuristic field detection (label-matching + LLM-assisted mapping for
unknown forms) vs purely hand-maintained mappings.

---

## 6. Cross-Cutting Components

### 6.1 API layer
FastAPI service exposing: openings list with filters (program_type, firm_tier,
region, status, deadline range); posting detail; CV CRUD; tailor action (posting id
+ cv id → tailored CV + gap report + rendered file); profile CRUD for the
extension; auth.

### 6.2 Frontend (the visible "tracker framework")
- **Board view:** openings grouped/filterable by program_type, firm, status — the
  Trackr-style categorisation rebuilt over our own data.
- **Calendar/deadline view:** deadline-sorted with rolling-deadline flag prominent
  (rolling roles reward early application — surface "new this week" and
  "reappeared").
- **Posting detail:** parsed requirements, tailor button, gap report, link to
  source.
- **CV manager:** structured master CV editing; tailored-version history per
  application.

### 6.3 Auth and user data
Standard email auth in v1. CV and profile data are personal data — store minimally,
encrypt at rest, provide delete. No scraped personal data exists in the system by
design.

### 6.4 Firm-tier filtering (later addition, seeded now)
`firm_tier` (BB / EB / MM / boutique) lives in the firms table from day one,
populated when each firm is added to the registry. Exposing the consumer-facing
filter later is a WHERE clause and a UI toggle — zero schema work at that point.

---

## 7. Canonical Posting Schema

Every adapter outputs this shape. Nothing downstream ever sees a raw source format.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | Internal |
| `firm` | string (FK firms) | Canonical firm name |
| `firm_tier` | enum: BB \| EB \| MM \| boutique | Seeded from registry; powers later filtering |
| `role_title` | string | As posted |
| `program_type` | enum: spring_week \| summer \| off_cycle \| graduate \| unclassified | Classified from title; unclassified → review queue |
| `division` | string/enum | IBD / Markets / Research / … where extractable |
| `location` | string | As posted |
| `region` | enum | Normalized (UK, EMEA, US, …) |
| `open_date` | date \| null | Where stated |
| `deadline` | date \| null | Where stated |
| `rolling` | bool | Rolling-review flag — prominent in UI |
| `source_url` | string | Application link (primary source) |
| `source_id` | string | ATS-native id where available, for dedup |
| `first_seen` | timestamp | Set on first ingestion |
| `last_seen` | timestamp | Updated each run while present |
| `status` | enum: open \| closed \| reappeared | Lifecycle via change detection |
| `raw_description` | text | For the posting parser (Layer 2) |

---

## 8. Decisions Log

### 8.1 Locked decisions (do not relitigate in LLM sessions)

| Decision | Rationale |
|---|---|
| Build own openings DB from primary sources; never scrape competitors | Independence (no reliance on partners); competitor DBs are legally protected and copying them is strategically downstream |
| Assisted autofill only — human always clicks submit | Portal ToS + bot detection; silent submit risks users' accounts being flagged at target banks |
| Thin LLM wrapper, direct API calls, no orchestration framework | Single well-shaped call; frameworks add weight and obscure the rubric (the actual edge); hosted resume-APIs reintroduce dependency |
| Structured master CV, not a text blob | Reliable selection/rephrasing; deterministic rendering; anti-hallucination |
| Fixed DOCX template, model supplies content only | Deterministic one-page layout; avoids model-generated formatting drift |
| `firm_tier` in schema from day one | Later BB/EB/MM filter becomes trivial |
| Build order: ingestion → tailoring → frontend → autofill | Dependency order; autofill is highest-risk and must not block v1 |
| Python backend (FastAPI + Postgres) | Solo developer strength |

### 8.2 Open decisions `[OPEN]` — fair game for LLM/engineer input
- **Scheduler:** APScheduler (simple, in-process) vs Celery+Redis (heavier,
  scales). Leaning APScheduler for v1.
- **Adapter concurrency:** sequential vs bounded-concurrent dispatch across sources.
- **Workday tenant variation:** config-driven quirks vs per-tenant subclasses.
- **Tailoring:** one-call vs two-call design (leaning two-call for the free
  keyword-gap report).
- **Autofill field detection:** hand-maintained per-portal mappings vs
  heuristic/LLM-assisted detection for unknown forms.
- **DOCX→PDF conversion mechanism** in the rendering pipeline.
- **CV ingest:** guided form only in v1, or include upload-and-parse from day one.

---

## 9. Risks and Constraints

| Risk | Mitigation |
|---|---|
| Ingestion rot — the moat is operational, not technical; adapters break when banks change pages | Adapter isolation + per-source observability + zero-postings alerts; keep custom-scrape sources to a minimum; accept daily (not hourly) cadence in v1 |
| Portal bot detection / ToS (autofill layer) | Human-on-submit hard rule; graceful degradation; build last |
| LLM fabrication in tailored CVs | Structured input; select-and-rephrase-only rule; post-process mapping check of every output bullet to a source bullet |
| Legal exposure from scraping | Primary sources only; robots.txt + rate limits; honest UA; no personal data; never competitor DBs |
| Incumbent response — integration is copyable | Acknowledged: the loop is a feature, not a moat. Value to builder is partly the system itself (portfolio/credential); data freshness is the only durable edge |
| Solo maintenance bandwidth (degree starting autumn 2026) | Ruthless v1 scope; JSON-endpoint sources first; defer custom scrapes; observability so breakage is visible not silent |

---

## 10. Build Order and Milestones

- **Weeks 1–3** — Schema + source registry + Greenhouse/Lever adapters (~10 JSON
  sources). Deliverable: a real, fresh openings DB.
- **Weeks 3–5** — Workday adapter family (covers BBs) + dedup/change detection + run
  logging. Deliverable: coverage comparable to a tracker.
- **Weeks 4–6 (parallel)** — Tailoring pipeline: structured CV store, posting
  parser, rubric, tailoring call, DOCX renderer. Standalone-testable.
- **Weeks 5–7** — API + frontend: board view, calendar, posting detail with tailor
  action, CV manager.
- **Later** — Browser extension (assisted autofill); per-portal mappings for
  Greenhouse + Workday first.
- **Later** — Consumer firm-tier filter (BB/EB/MM/boutique), expanded coverage,
  sub-24h freshness, upload-and-parse CV ingest.

### 10.1 Per-section LLM prompting guide
When opening an LLM session for a given component, include: Section 1 + Section 2 +
Section 7 (always), the component's own subsection, and the subsections of its
direct neighbours (e.g. for the dedup module: 3.8, 3.9, 3.10). State explicitly:
"Locked decisions in Section 8.1 are constraints, not suggestions. Only `[OPEN]`
items may be redesigned." For adapter work, also paste the adapter interface
contract (3.2) so every adapter session converges on the same shape.
