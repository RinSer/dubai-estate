"""Unit tests for dxb.csv_import.loader.stage_csv."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from conftest import insert_value_rows

from dxb.csv_import import loader


def _write_csv(tmp_path, name: str, header: list[str], rows: list[list[str]]):
    path = tmp_path / name
    lines = [",".join(header)]
    lines += [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def test_stage_csv_stages_every_row(tmp_path):
    path = _write_csv(
        tmp_path,
        "t.csv",
        ["A", "B"],
        [["1", "x"], ["2", "y"], ["3", "z"]],
    )
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [(1,), (2,), (3,)]

    report = loader.stage_csv(session, source_id=7, endpoint="csv:t", path=path)

    assert report == {"file": "t.csv", "fetched": 3, "staged": 3}
    stmt = session.execute.call_args[0][0]
    rows = insert_value_rows(stmt)
    assert len(rows) == 3
    assert rows[0]["source_id"] == 7
    assert rows[0]["endpoint"] == "csv:t"
    assert rows[0]["payload_json"] == {"A": "1", "B": "x"}
    assert rows[0]["request_json"] == {"file": "t.csv", "row_number": 1}


def test_stage_csv_strips_bom(tmp_path):
    """csv.DictReader + utf-8-sig must not leave a BOM glued to the first
    header name (a real bug the austinpowers file exhibits raw)."""
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿Transaction Number,Area\n102-1-2023,BUSINESS BAY\n".encode())
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [(1,)]

    loader.stage_csv(session, source_id=1, endpoint="csv:bom", path=path)

    rows = insert_value_rows(session.execute.call_args[0][0])
    assert list(rows[0]["payload_json"].keys())[0] == "Transaction Number"


def test_stage_csv_batches_at_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "BATCH", 2)
    path = _write_csv(tmp_path, "t.csv", ["A"], [["1"], ["2"], ["3"], ["4"], ["5"]])
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = [(1,), (2,)]

    report = loader.stage_csv(session, source_id=1, endpoint="csv:t", path=path)

    assert report["fetched"] == 5
    # 5 rows at batch size 2 -> 3 flushes (2, 2, 1)
    assert session.execute.call_count == 3
    assert len(insert_value_rows(session.execute.call_args_list[0][0][0])) == 2
    assert len(insert_value_rows(session.execute.call_args_list[1][0][0])) == 2
    assert len(insert_value_rows(session.execute.call_args_list[2][0][0])) == 1


def test_stage_csv_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        loader.stage_csv(
            MagicMock(), source_id=1, endpoint="csv:x", path=tmp_path / "nope.csv"
        )


def test_record_hash_stable_and_content_sensitive():
    h1 = loader.record_hash("csv:t", {"A": "1", "B": "2"})
    h2 = loader.record_hash("csv:t", {"B": "2", "A": "1"})  # different key order
    h3 = loader.record_hash("csv:t", {"A": "1", "B": "3"})  # different content
    assert h1 == h2
    assert h1 != h3
