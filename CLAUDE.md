# CLAUDE.md — IB Internship Application Platform

This repo is built from `docs/Project_Dossier.md` (the dossier). The dossier is the
single source of truth. Read it before acting.

## Standing rules (apply to EVERY session — non-negotiable)

1. **Locked decisions are locked.** Section 8.1 of the dossier lists locked decisions
   and Sections 1.3 / 4.x / 5.x mark others "(locked)". You may NOT propose, "improve,"
   or work around any locked decision. Examples of forbidden moves: swapping the thin LLM
   wrapper for LangChain/LlamaIndex, redesigning the canonical schema (§7), changing the
   build order, scraping a competitor's compiled DB, adding silent auto-submit.

2. **Only `[OPEN]` items are open.** The only decisions you may propose changes to are the
   ones explicitly tagged `[OPEN]` in §8.2 (scheduler choice, adapter concurrency, Workday
   tenant-variation strategy). If you think a locked decision is wrong, say so in one
   sentence and move on — do not act on it.

3. **Adapters never touch the database.** (§3.2) Adapters implement `fetch()` and
   `parse(raw)` only, are stateless, and return normalized posting objects. Writing to
   Postgres, reading config from the DB, or caching inside an adapter is a boundary
   violation. The pipeline orchestrator is the only thing that writes postings.

4. **Everything crosses the adapter boundary as the canonical schema (§7).** No raw source
   shape leaks past an adapter. Downstream code only ever sees §7 objects.

5. **Tests use captured fixtures, never live endpoints.** Network calls in tests are
   forbidden. Each adapter is tested against a saved real-response fixture in
   `<layer>/tests/fixtures/`. If a fixture doesn't exist yet, stop and ask me to capture
   one — do not invent a fake payload that you guess matches the source.

6. **Config lives in data files, not code.** (§2.3) Source registry, field mappings,
   rubric keyword sets are YAML/JSON, editable without redeploying. Do not hardcode firm
   lists, selectors, or keyword sets into Python.

7. **One scope per session.** Build only what the session prompt asks for. Do not
   "get ahead" by scaffolding later layers. If you finish early, stop and report — do not
   start the next layer.

8. **Stop conditions are hard.** Each prompt ends with an explicit stop condition. When you
   hit it, stop and summarize what you did + how I verify it. Do not continue past it.

9. **Log every code change in `docs/PROGRESS.md`.** Any session that writes or improves
   code is NOT done until you append a dated entry to `docs/PROGRESS.md` stating: (a) exactly
   what changed — files touched and behaviour added/altered; (b) how to verify it (command +
   result, e.g. `pytest` output); and (c) where the project now stands and what the next
   step is. Treat this like a stop-condition, not an optional nicety — it is how every future
   session knows where we are. Keep entries append-only; do not rewrite history.

## Architecture (from §2)

- Layers: `/ingestion`, `/tailoring`, `/autofill`, `/api`, `/frontend`. Extension repo may
  be separate.
- Stack: FastAPI + PostgreSQL + SQLAlchemy + Alembic. httpx for JSON endpoints, Playwright
  only where no structured endpoint exists. Direct LLM API calls, thin wrapper, no
  orchestration framework.
- Build order (locked): ingestion → tailoring → frontend → autofill.

## Conventions

- Python: type hints everywhere, `ruff` + `black`, `pytest`.
- Adapters share one interface; adding a firm on a known ATS = a registry config entry,
  not new code.
- Every ingestion run is logged (run id, source, found/new/closed, errors) — §3.11.
- Personal data (CV, profile) is minimized, encrypted at rest, deletable. No scraped
  personal data exists by design.

## When unsure

Ask one sharp question rather than guessing across a boundary. A wrong guess inside an
adapter is cheap; a wrong guess about the schema or a locked decision is expensive.

---

## Repository & GitHub sync

- **GitHub remote:** `https://github.com/demiryyurtsever/CVapp.git` — branch `main`.
- **Sync is MANUAL by the user's choice — there is NO auto-push hook.** Changes reach
  GitHub only when explicitly pushed. Do not set up an auto-commit/auto-push hook unless
  the user asks for one.
- **Offer to push interactively.** When you've made changes worth syncing, proactively
  offer to push via an in-chat interactive prompt (a pause-and-choose question — e.g.
  options like "Push now" / "Not yet") rather than only mentioning it in prose. The turn
  continues after the user chooses; never push without that explicit go-ahead.
- **To mirror local changes to GitHub**, run from the project root (only when the user asks
  to "sync" / "push"):
  ```powershell
  git add -A
  git commit -m "describe what changed"
  git push
  ```
  End commit messages with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Local dev environment

- Python via the `py` launcher (Python 3.12). Virtualenv at `.venv` (git-ignored;
  recreate with `py -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt`).
- Run tests: `.venv\Scripts\python -m pytest -q`.
- `.gitignore` already excludes `.venv/`, `__pycache__/`, `.pytest_cache/`.

## Current status / where to continue

See `docs/PROGRESS.md` for the authoritative, per-session build log. As of the last entry:

- **Done:** ingestion layer foundations — canonical schema (`ingestion/models.py`), adapter
  interface (`ingestion/adapters/base.py`), source registry + loader, §3.8 classifiers, the
  Greenhouse reference adapter, and fixture-only tests. Test suite green.
- **Next session:** the pipeline orchestrator + deduplication/change detection (§3.9) + DB
  writes/storage (§3.10). Do not start it ahead of its session prompt (standing rule 7).
