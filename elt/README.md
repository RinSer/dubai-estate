# dxb ELT service

Collects Dubai Land Department open data (transactions, rents, projects) into a local
Postgres star schema. See ../docs/PLAN.md for the full design.

Run everything from the repo root:

```
docker compose up -d --build         # db + scheduled daily pipeline
docker compose run --rm elt dxb init                      # migrations + seed
docker compose run --rm elt dxb backfill --from 2026-01-01  # historical load
docker compose run --rm elt dxb stats
```
