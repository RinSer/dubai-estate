"""Unit tests for dxb.csv_import.transform: per-source row mapping and the
dedup-aware batch transform."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from conftest import StubCaches, insert_value_rows

from dxb.csv_import import transform as tr
from dxb.db.models import FactSaleTransaction


def stg(payload: dict, source_id: int = 1):
    return SimpleNamespace(payload_json=payload, source_id=source_id)


# --------------------------------------------------- sale_values_alexefimik


def _alex_row(**overrides) -> dict:
    base = {
        "transaction_id": "1-102-2023-1",
        "instance_date": "02-01-2023",
        "trans_group_en": "Sales",
        "procedure_name_en": "Sell",
        "reg_type_en": "Existing Properties",
        "property_usage_en": "Residential",
        "property_type_en": "Unit",
        "property_sub_type_en": "Flat",
        "rooms_en": "2 B/R",
        "has_parking": "1",
        "area_name_en": "Business Bay",
        "area_name_ar": None,
        "project_name_en": "Marina Gate",
        "project_name_ar": None,
        "master_project_en": None,
        "procedure_area": "105.75",
        "actual_worth": "2631000",
    }
    base.update(overrides)
    return base


def test_alexefimik_maps_offplan_and_null_freehold():
    caches = StubCaches(area_id=10, ptype_id=20, project_id=30)
    v = tr.sale_values_alexefimik(stg(_alex_row()), caches, "http://src")

    assert v["txn_number"] == "1-102-2023-1"
    assert v["txn_date"] == datetime(2023, 1, 2)
    assert v["is_offplan"] is False  # "Existing Properties"
    assert v["is_freehold"] is None  # source has no such column at all
    assert v["amount_aed"] == 2631000.0
    assert v["actual_area_m2"] == 105.75
    assert v["parcel_id"] is None
    assert v["area_id"] == 10 and v["project_id"] == 30


def test_alexefimik_off_plan_properties_maps_true():
    v = tr.sale_values_alexefimik(
        stg(_alex_row(reg_type_en="Off-Plan Properties")), StubCaches(), "u"
    )
    assert v["is_offplan"] is True


def test_alexefimik_bad_date_returns_none():
    # ISO format instead of the real DD-MM-YYYY -> unparseable, not silently wrong
    v = tr.sale_values_alexefimik(
        stg(_alex_row(instance_date="2023-01-02")), StubCaches(), "u"
    )
    assert v is None


def test_alexefimik_none_when_no_txn_id():
    v = tr.sale_values_alexefimik(stg(_alex_row(transaction_id="")), StubCaches(), "u")
    assert v is None


def test_alexefimik_none_when_area_unresolved():
    v = tr.sale_values_alexefimik(stg(_alex_row()), StubCaches(area_id=None), "u")
    assert v is None


# ------------------------------------------------- sale_values_austinpowers


def _ap_row(**overrides) -> dict:
    base = {
        "Transaction Number": "102-1-2023",
        "Transaction Date": "2023-01-02 07:25:49",
        "Transaction Type": "Sales",
        "Transaction sub type": "Sell - Pre registration",
        "Registration type": "Off-Plan",
        "Is Free Hold?": "Free Hold",
        "Usage": "Residential",
        "Area": "BUSINESS BAY",
        "Property Type": "Unit",
        "Property Sub Type": "Flat",
        "Amount": "2631000",
        "Transaction Size (sq.m)": "105.75",
        "Room(s)": "2 B/R",
        "Parking": "1",
        "Project": "AYKON CITY 3",
        "Master Project": None,
    }
    base.update(overrides)
    return base


def test_austinpowers_maps_freehold_and_offplan():
    caches = StubCaches(area_id=11, ptype_id=21, project_id=31)
    v = tr.sale_values_austinpowers(stg(_ap_row(), source_id=2), caches, "http://src")

    assert v["txn_number"] == "102-1-2023"
    assert v["txn_date"] == datetime(2023, 1, 2, 7, 25, 49)
    assert v["is_offplan"] is True  # "Off-Plan"
    assert v["is_freehold"] is True  # "Free Hold"
    assert v["amount_aed"] == 2631000.0
    assert v["actual_area_m2"] == 105.75
    assert v["source_id"] == 2


def test_austinpowers_non_free_hold_maps_false():
    v = tr.sale_values_austinpowers(
        stg(_ap_row(**{"Is Free Hold?": "Non Free Hold"})), StubCaches(), "u"
    )
    assert v["is_freehold"] is False


def test_austinpowers_none_when_no_amount():
    v = tr.sale_values_austinpowers(stg(_ap_row(Amount="")), StubCaches(), "u")
    assert v is None


# -------------------------------------------------------- _transform_source


def _values_row(txn_number, area_id=1, size=100.0, price=1000.0):
    return {
        "txn_number": txn_number,
        "txn_date": datetime(2023, 1, 2),
        "txn_group": "Sales",
        "procedure_name": None,
        "is_offplan": False,
        "is_freehold": None,
        "property_type_id": None,
        "rooms": None,
        "parking": None,
        "area_id": area_id,
        "project_id": None,
        "parcel_id": None,
        "actual_area_m2": size,
        "amount_aed": price,
        "source_id": 2,
        "source_url": "u",
        "source_ref": txn_number,
    }


def test_transform_source_skips_dedupe_matches(monkeypatch):
    """A row whose key is in dedupe_against must not reach _upsert_facts;
    a row with a different key (different area here) must pass through."""
    mapper_calls = []

    def fake_mapper(stg_row, caches, source_url):
        row_id = stg_row.payload_json["id"]
        area = 1 if row_id == "A" else 2  # A matches the dupe key, B doesn't
        row = _values_row(row_id, area_id=area)
        mapper_calls.append(row["txn_number"])
        return row

    session = MagicMock()
    batch = [stg({"id": "A"}), stg({"id": "B"})]
    monkeypatch.setattr(tr, "_staged_batches", lambda s, e: iter([batch]))
    monkeypatch.setattr(tr, "DimCaches", lambda s: MagicMock())
    monkeypatch.setattr(tr, "_mark_processed", lambda s, b: None)

    captured_values = {}

    def spy_upsert(session_, table, constraint, key_cols, update_cols, values):
        captured_values["values"] = values
        return 0

    monkeypatch.setattr(tr, "_upsert_facts", spy_upsert)

    dupe_key = tr.dedupe_key(datetime(2023, 1, 2), 1, 100.0, 1000.0)
    report = tr._transform_source(
        session, "csv:x", fake_mapper, "u", dedupe_against={dupe_key}
    )

    assert mapper_calls == ["A", "B"]
    assert report["duplicates"] == 1
    assert len(captured_values["values"]) == 1
    assert captured_values["values"][0]["txn_number"] == "B"


def test_transform_source_no_dedupe_set_passes_everything(monkeypatch):
    session = MagicMock()
    batch = [stg({"id": "A"})]
    monkeypatch.setattr(tr, "_staged_batches", lambda s, e: iter([batch]))
    monkeypatch.setattr(tr, "DimCaches", lambda s: MagicMock())
    monkeypatch.setattr(tr, "_mark_processed", lambda s, b: None)

    captured = {}

    def spy_upsert(session_, table, constraint, key_cols, update_cols, values):
        captured["values"] = values
        return len(values)

    monkeypatch.setattr(tr, "_upsert_facts", spy_upsert)

    report = tr._transform_source(
        session, "csv:x", lambda s, c, u: _values_row("A"), "u", dedupe_against=None
    )
    assert report["duplicates"] == 0
    assert len(captured["values"]) == 1
