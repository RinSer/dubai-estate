"""Unit tests for dxb.csv_import.geo — coordinate enrichment matches
existing dim rows by normalized name and UPDATEs in place (no new facts)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from dxb.csv_import import geo


def stg(payload: dict):
    return SimpleNamespace(payload_json=payload, source_id=1)


def test_enrich_area_coords_sets_centroid_on_match(monkeypatch):
    area = SimpleNamespace(centroid=None)
    session = MagicMock()
    session.scalar.return_value = area
    batch = [
        stg(
            {
                "Area": "Business Bay",
                "Latitude_Area": "25.189",
                "Longitude_Area": "55.264",
            }
        )
    ]
    monkeypatch.setattr(geo, "_staged_batches", lambda s, e: iter([batch]))
    monkeypatch.setattr(geo, "_mark_processed", lambda s, b: None)

    report = geo.enrich_area_coords(session)

    assert report == {"endpoint": "csv:area-coords", "matched": 1, "unmatched": 0}
    assert area.centroid is not None
    assert "POINT(55.264 25.189)" in str(area.centroid)


def test_enrich_area_coords_unmatched_when_no_dim_row(monkeypatch):
    session = MagicMock()
    session.scalar.return_value = None  # no matching dim_area
    batch = [stg({"Area": "Nowhereville", "Latitude_Area": "1", "Longitude_Area": "2"})]
    monkeypatch.setattr(geo, "_staged_batches", lambda s, e: iter([batch]))
    monkeypatch.setattr(geo, "_mark_processed", lambda s, b: None)

    report = geo.enrich_area_coords(session)

    assert report == {"endpoint": "csv:area-coords", "matched": 0, "unmatched": 1}


def test_enrich_area_coords_unmatched_on_missing_lat_lon(monkeypatch):
    session = MagicMock()
    batch = [stg({"Area": "Business Bay", "Latitude_Area": "", "Longitude_Area": ""})]
    monkeypatch.setattr(geo, "_staged_batches", lambda s, e: iter([batch]))
    monkeypatch.setattr(geo, "_mark_processed", lambda s, b: None)

    report = geo.enrich_area_coords(session)

    assert report["unmatched"] == 1
    session.scalar.assert_not_called()


def test_enrich_project_coords_sets_location_on_match(monkeypatch):
    project = SimpleNamespace(location=None)
    session = MagicMock()
    session.scalar.return_value = project
    batch = [
        stg(
            {
                "Project": "Aykon City 3",
                "Latitude_Project": "25.11",
                "Longitude_Project": "55.39",
            }
        )
    ]
    monkeypatch.setattr(geo, "_staged_batches", lambda s, e: iter([batch]))
    monkeypatch.setattr(geo, "_mark_processed", lambda s, b: None)

    report = geo.enrich_project_coords(session)

    assert report == {"endpoint": "csv:project-coords", "matched": 1, "unmatched": 0}
    assert project.location is not None
