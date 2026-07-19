"""Stream a CSV file into stg_raw, batched + hash-deduped — the same
staging idempotency pattern as collectors/dld.py's stage_rows, adapted for
reading from disk instead of an API response.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dxb.db.models import StgRaw

log = logging.getLogger(__name__)

BATCH = 5000


def record_hash(endpoint: str, row: dict) -> str:
    canonical = json.dumps(
        row, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(f"{endpoint}|{canonical}".encode()).hexdigest()


def stage_csv(
    session: Session,
    source_id: int,
    endpoint: str,
    path: Path,
    encoding: str = "utf-8-sig",
) -> dict:
    """Stream `path` row-by-row into stg_raw. Idempotent: a conflicting
    record_hash means byte-identical content, safe to re-run on the same file."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — place the downloaded CSV under data/raw/ "
            "(see docs/CSV_DATA_ANALYSIS.md)"
        )

    fetched = staged = 0
    batch: list[dict] = []

    def flush() -> None:
        nonlocal staged
        if not batch:
            return
        stmt = (
            pg_insert(StgRaw.__table__)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["record_hash"])
            .returning(StgRaw.__table__.c.id)
        )
        result = session.execute(stmt)
        staged += len(result.fetchall())
        session.commit()
        batch.clear()

    with open(path, encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            fetched += 1
            batch.append(
                {
                    "source_id": source_id,
                    "endpoint": endpoint,
                    "request_json": {"file": path.name, "row_number": i + 1},
                    "payload_json": row,
                    "record_hash": record_hash(endpoint, row),
                }
            )
            if len(batch) >= BATCH:
                flush()
    flush()

    log.info("staged csv %s: fetched=%s staged(new)=%s", path.name, fetched, staged)
    return {"file": path.name, "fetched": fetched, "staged": staged}
