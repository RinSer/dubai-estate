"""Cross-source dedup for the alexefimik/austinpowers overlap window.

See docs/CSV_DATA_ANALYSIS.md §3: ~32.6% of austinpowers's transactions in
the shared window (2023-01-02..2023-03-17) are exact-key duplicates of an
alexefimik row already loaded. Six relaxed-matching strategies (dropping
size, dropping price, +/-1 day date tolerance, ...) all landed within
32.6-33.0%, ruling out rounding/precision noise as the explanation for the
other ~67% — those are genuinely distinct transactions, not duplicates, and
must NOT be discarded by a blanket date cutoff.

The key must be computed identically on both sides (the preloaded alexefimik
set and each austinpowers row being checked) or the comparison is meaningless
— hence one shared `dedupe_key` function, not parallel SQL/Python rounding
logic that could silently drift apart.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from dxb.db.models import DimSource, FactSaleTransaction

# Verified in docs/CSV_DATA_ANALYSIS.md: austinpowers spans 2023-01-02..
# 2023-06-26; alexefimik ends 2023-03-17. This range fully covers the only
# period where a cross-source duplicate is possible.
OVERLAP_FROM = date(2023, 1, 1)
OVERLAP_TO = date(2023, 6, 30)


def dedupe_key(
    txn_date: datetime | date, area_id: int | None, size_m2, price_aed
) -> tuple:
    day = txn_date.date() if isinstance(txn_date, datetime) else txn_date
    size = f"{float(size_m2):.2f}" if size_m2 is not None else None
    price = f"{float(price_aed):.0f}" if price_aed is not None else None
    return (day.isoformat(), area_id, size, price)


def load_alexefimik_keys(
    session: Session, date_from: date = OVERLAP_FROM, date_to: date = OVERLAP_TO
) -> set[tuple]:
    """Preload the matching key for every alexefimik fact in the overlap
    window (~32K rows — trivial in memory). Explicitly scoped to the
    alexefimik source_id rather than relying on the date range alone to
    exclude other sources."""
    alex_source_id = session.scalar(
        select(DimSource.id).where(DimSource.code == "kaggle_alexefimik")
    )
    rows = session.execute(
        select(
            FactSaleTransaction.txn_date,
            FactSaleTransaction.area_id,
            FactSaleTransaction.actual_area_m2,
            FactSaleTransaction.amount_aed,
        ).where(
            FactSaleTransaction.source_id == alex_source_id,
            FactSaleTransaction.txn_date >= date_from,
            FactSaleTransaction.txn_date <= date_to,
        )
    ).all()
    return {
        dedupe_key(txn_date, area_id, size, price)
        for txn_date, area_id, size, price in rows
    }
