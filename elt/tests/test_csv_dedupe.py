"""Unit tests for dxb.csv_import.dedupe — the matching-key logic that
distinguishes real duplicates from genuinely distinct transactions (see
docs/CSV_DATA_ANALYSIS.md §3)."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

from dxb.csv_import import dedupe


def test_dedupe_key_same_for_datetime_and_date():
    k1 = dedupe.dedupe_key(datetime(2023, 1, 2, 7, 25, 49), 10, 105.75, 2631000)
    k2 = dedupe.dedupe_key(date(2023, 1, 2), 10, 105.75, 2631000)
    assert k1 == k2


def test_dedupe_key_ignores_sub_cent_float_noise():
    """Values arriving as float vs int vs string-ish must key identically
    after the shared formatting — this is the whole point of using one
    formatting function on both sides instead of separate SQL/Python
    rounding that could silently drift apart."""
    k1 = dedupe.dedupe_key(datetime(2023, 1, 2), 10, 105.75, 2631000)
    k2 = dedupe.dedupe_key(datetime(2023, 1, 2), 10, 105.750000001, 2631000.0)
    assert k1 == k2


def test_dedupe_key_differs_on_area():
    k1 = dedupe.dedupe_key(datetime(2023, 1, 2), 10, 105.75, 2631000)
    k2 = dedupe.dedupe_key(datetime(2023, 1, 2), 11, 105.75, 2631000)
    assert k1 != k2


def test_dedupe_key_handles_none_size_or_price():
    k = dedupe.dedupe_key(datetime(2023, 1, 2), 10, None, None)
    assert k == ("2023-01-02", 10, None, None)


def test_load_alexefimik_keys_scopes_to_alexefimik_source_and_window():
    session = MagicMock()
    # first scalar() call resolves the alexefimik dim_source.id
    session.scalar.return_value = 99
    session.execute.return_value.all.return_value = [
        (datetime(2023, 2, 1), 5, 100.0, 1000.0),
    ]

    keys = dedupe.load_alexefimik_keys(session)

    assert keys == {dedupe.dedupe_key(datetime(2023, 2, 1), 5, 100.0, 1000.0)}
    # the source_id resolved via scalar() must be threaded into the facts query
    where_clause = str(session.execute.call_args[0][0])
    assert "fact_sale_transaction.source_id" in where_clause
