# CLAUDE.md

Guidance for Claude (or any LLM agent) working in this repository.

## What this repo is

A Dubai real estate analytics platform. Currently only the data layer is
built: a Postgres star schema plus a Python ELT service (`elt/`) that
collects, normalizes, deduplicates, and geo-enriches real transaction data
from multiple sources. See the root [README.md](README.md) for the product
picture and [docs/PLAN.md](docs/PLAN.md) for the full architecture and the
reasoning behind it — read that before making structural changes; a lot of
non-obvious decisions (schema shape, source selection, dedup strategy) are
recorded there with the evidence that drove them, not just the conclusion.

## Stack

- **Python 3.12+**, managed with **`uv`** — not pip, not poetry, not bare `python`.
- **SQLAlchemy 2.0** (declarative `Mapped[...]` style) + **Alembic** for migrations.
- **PostgreSQL 17 + PostGIS** (`postgis/postgis:17-3.5`), geography columns for spatial data.
- **Typer** for the CLI (`dxb ...`), **Docker Compose** for local orchestration.
- **pytest** + **pytest-mock** for tests, **ruff** for lint + format.
- **httpx** for HTTP clients, **tenacity** for retries, **APScheduler** for the daily job.

All of this lives under `elt/`. Run every Python command from inside that
directory via `uv run ...` (e.g. `uv run pytest -q`, `uv run ruff check .`) —
`uv` resolves the project's own `.venv` regardless of what's on the host PATH.

## Required workflow for every change

1. **Write or update tests alongside any code change.** Not optional, not a
   follow-up — the same change. Follow the existing patterns: mock
   `httpx` with `httpx.MockTransport` (see `tests/test_client.py`), mock
   SQLAlchemy sessions with `MagicMock` (see `tests/conftest.py`'s
   `insert_value_rows` helper for introspecting `pg_insert` statements
   without a real DB), never hit a real network or real database in a unit
   test.
2. **Run the full suite, not just the new tests, and it must be 100% green
   before the change is considered done**: `uv run pytest -q` from `elt/`.
   A change that breaks an unrelated existing test is not finished.
3. **Lint clean**: `uv run ruff format .` then `uv run ruff check .` (add
   `--fix` for auto-fixable issues) from `elt/`. The tracked pre-commit hook
   (`.githooks/pre-commit`, activate once per clone with
   `git config core.hooksPath .githooks`) enforces `ruff check` and blocks
   commits that fail it — don't rely on it as your only check; run ruff
   yourself before you consider a change finished.
4. **If you touched anything Docker-relevant** (source code, dependencies,
   the Dockerfile), rebuild and verify tests pass *inside the container too*,
   not just on the host:
   ```
   docker compose build elt
   docker compose run --rm elt python -m pytest -q
   ```
   This isn't redundant — the container can silently run a stale image after
   a fix and appear to still have the bug (this happened for real during
   development: a scheduled job kept failing on an already-fixed bug because
   the long-running scheduler container hadn't been rebuilt).
5. **Schema changes go through Alembic**, following the existing numbered
   sequence in `elt/alembic/versions/` (`0001`, `0002`, `0003`, ...). Never
   hand-edit the live schema without a migration backing it.

## Conventions worth knowing before you're surprised by them

- **Provenance is load-bearing, not decorative.** Every fact row carries
  `source_id` / `source_url` / `source_ref`; every `dim_source` row has an
  `is_government` flag so queries can filter to verified government data
  only. A new data source needs a new entry in `SOURCES` in
  `elt/src/dxb/db/engine.py` before it's used anywhere.
- **Env-var settings are hermetic in tests on purpose.** `tests/conftest.py`
  has an autouse fixture that clears every `DXB_*`/`SMTP_*`/etc. env var
  before each test, and a `_SETTINGS_DEFAULTS` dict used to build a full
  `Settings` object for tests. If you add a new setting to `config.py`, add
  it to **both** `_DXB_ENV_VARS` and `_SETTINGS_DEFAULTS` in `conftest.py` or
  every test that builds `Settings` breaks with a confusing missing-argument
  error.
- **Side-enrichment steps must be non-fatal.** Anything that piggybacks on
  the core pipeline (the OSM geo-enrichment hook is the example) must never
  let its own failure fail or retry the actual data-collection run — wrap it,
  log it, move on. Collecting today's transactions is always the priority.
- **DLD gateway dates are `MM/DD/YYYY`.** The wrong order doesn't error
  cleanly — it silently returns an HTML 500 page. Already handled in
  `collectors/dld.py`'s `fmt_date`; don't reintroduce this bug elsewhere.
- **Don't add a new heavy dependency for a narrow conversion need** —
  `osm_geo/geojson_wkt.py` hand-rolls GeoJSON→WKT instead of pulling in
  shapely, because the actual scope (Point/Polygon/MultiPolygon from one API)
  didn't justify it. Match that judgment call rather than reflexively
  reaching for a library.

## Git

Never commit unless explicitly asked, even if a change is complete and
tests are green. There is usually real uncommitted work sitting in the tree
between sessions — check `git status` before doing anything that could
discard changes.
