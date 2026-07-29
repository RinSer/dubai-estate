# Dubai Real Estate Analytics

An analytical platform for the Dubai real estate market: which districts and
projects are actually appreciating, how sale prices and rents move over
time, and where gross yield looks best — based on real registered
transactions, not asking prices.

The core idea is to ground everything in **real, verifiable data with a
traceable source** — every stored record keeps a reference back to where it
came from (a government open-data API, a specific dataset, or OpenStreetMap)
— rather than estimates or scraped listing prices.

## What's here today

Only the data layer is built so far: a Postgres star schema plus a Python
ELT service that collects, normalizes, and enriches the data. A REST API and
a map UI are planned but not started (see [Roadmap](#roadmap)).

## Data sources

| Source | What it provides | Coverage |
|---|---|---|
| [Dubai Land Department open-data gateway](https://dubailand.gov.ae/en/open-data/real-estate-data/) | Live government transactions, rents, projects | **2026 only** — the gateway has a hard cutoff at the start of the current year, verified empirically |
| [Kaggle: alexefimik](https://www.kaggle.com/datasets/alexefimik/dubai-real-estate-transactions-dataset) / [austinpowers](https://www.kaggle.com/datasets/austinpowers/dubai-real-estate-transaction-first-semester-2023) | Historical DLD transaction mirrors (CC0) | 1995-03-07 → 2023-06-26, deduplicated against each other (see [docs/CSV_DATA_ANALYSIS.md](docs/CSV_DATA_ANALYSIS.md)) |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) (via Nominatim) | Area centroids and boundary polygons | 319/439 areas have coordinates, 189/439 have real boundary polygons (see [docs/OSM_AREA_GEO_ENRICHMENT.md](docs/OSM_AREA_GEO_ENRICHMENT.md)) |

**Known gap**: mid-2023 through 2025 isn't covered by any source yet — the
live gateway only reaches back to 2026 and the historical CSVs stop at
mid-2023. Options were researched (PropAPIS, Property Monitor) but not
pursued — see [docs/PLAN.md](docs/PLAN.md) for the reasoning.

Combined: **1,233,571 sale transactions** and **484,485 rent contracts**
across every source, with zero double-counting.

## Repository layout

```
dubai-estate/
├── docker-compose.yml   # local stack: Postgres+PostGIS, elt, api, mcp
├── data/raw/            # downloaded historical CSVs (gitignored — see docs/CSV_DATA_ANALYSIS.md)
├── docs/
│   ├── PLAN.md                      # architecture, schema design, source research
│   ├── API_DESIGN.md                # REST API design & the honesty guarantees
│   ├── MCP_DESIGN.md                # MCP server design, transport, tool surface
│   ├── BUILDING_MART_ANALYSIS.md    # why buildings are a summary, not a monthly mart
│   ├── CSV_DATA_ANALYSIS.md         # historical-CSV format comparison & dedup analysis
│   ├── OSM_AREA_GEO_ENRICHMENT.md   # area geocoding methodology & results
│   └── PROJECT_GEO_ENRICHMENT.md    # project/building geocoding (Makani)
├── packages/dxb-core/    # shared SQLAlchemy tables + the constants that define
│                         #   what the data means — imported by every service
├── elt/                  # data collection & loading (writes, sync)
├── api/                  # read-only REST analytics service (async)
├── mcp/                  # MCP server — an HTTP client of api/, owns no SQL
└── .githooks/            # tracked pre-commit hook (ruff)
```

The three services are deliberately separated by what they may do: `elt/`
writes and is strictly synchronous, `api/` only reads and is strictly async,
and `mcp/` touches no database at all. See CLAUDE.md for why that split is a
hard rule rather than a preference.

## Authentication

Both the REST API and the MCP server authenticate with API keys. Only the
**hash** is stored in configuration — the plaintext is shown once, when you
generate it, and cannot be recovered afterwards.

Generate a key and its hash:

```bash
uv run --project api python -c "import secrets; from dxb_api.auth import hash_api_key; k = secrets.token_urlsafe(32); print('plaintext (give to the consumer, store nowhere):', k); print('hash (put in DXB_API_KEYS):', hash_api_key(k))"
```

Then in `.env`:

- add the **hash** to `DXB_API_KEYS`, as one entry in the JSON array along with
  a `name` identifying the consumer and its `scopes`;
- give the **plaintext** to that consumer. For the MCP server, which is just
  another consumer, that means setting `DXB_MCP_API_KEY` to the plaintext of
  the entry named `mcp`.

```
DXB_API_KEYS=[{"name":"mcp","key_hash":"<hash>","scopes":["read"]}]
DXB_MCP_API_KEY=<plaintext of that same key>
```

Two things that will otherwise cost you an afternoon:

- **Hashing is SHA-256, not argon2, on purpose.** An API key is high-entropy
  and is checked on *every* request, unlike a password which is checked once
  per login — a deliberately slow hash there would be a self-inflicted
  bottleneck, and buys nothing against a 256-bit random key.
- **Argon2 hashes for `DXB_API_USERS` contain `$`, which Docker Compose
  interpolates** when it reads `.env`. Every `$` must be doubled to `$$` there
  or login fails with a confusing "malformed hash". API-key hashes are hex, so
  they are unaffected.

## The ELT component (`elt/`)

A Python service (FastAPI-adjacent tooling, SQLAlchemy 2.0, Alembic, Typer
CLI) that turns the raw sources above into a normalized Postgres **star
schema**: dimension tables for areas, projects, developers, and property
types; fact tables for sale transactions and rent contracts; and monthly
analytics marts (median price/m², percentiles, gross yield) rebuilt after
every load.

Every fact row carries `source_id` / `source_url` / `source_ref` for
provenance, and every source is flagged `is_government` so any query can
filter to verified government data only or include the historical/OSM
enrichment too.

**Runs as three kinds of work**, all sharing the same
collect → transform → enrich-geo → rebuild-marts pipeline:
- **Scheduled daily** (`dxb run-scheduler`) — incremental, watermark-based,
  retried with backoff and cancellation-aware (SIGTERM → marked `cancelled`,
  not stuck at `running`).
- **One-off backfills** (`dxb backfill --from ... --to ...`) — the same
  pipeline over an explicit historical range, resumable if interrupted.
- **One-off imports** (`dxb import-csv`, `dxb enrich-geo`) — the historical
  CSV load and the OSM geocoding sweep, each documented in `docs/`.

See [elt/README.md](elt/README.md) for how to run it locally.

## Roadmap

- [x] Star-schema Postgres design + Alembic migrations
- [x] Live DLD gateway collector (daily scheduler, backfill, retries, alerting)
- [x] Historical CSV import with cross-source deduplication
- [x] OSM area geo-enrichment (centroids + boundary polygons)
- [x] Makani building geo-enrichment + geometric-median project placement
- [x] REST API (FastAPI) over the marts/facts, read-only and key-authenticated
- [x] Buildings mart (`mart_building_summary`) — a summary, not a monthly grain
- [x] MCP server exposing the analytics to any MCP-capable agent
- [ ] nginx edge: TLS + separate rate-limit zones for REST and MCP
- [ ] Name aliases so colloquial district names resolve to official DLD ones
- [ ] ML price/rent forecasting
- [ ] Map UI
