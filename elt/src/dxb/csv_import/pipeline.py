"""One-off historical CSV import — not part of the daily scheduler or
backfill. Sequenced: alexefimik (dedup source of truth) -> austinpowers
(dedup-aware) -> geo enrichment -> marts rebuild.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from dxb.csv_import import geo
from dxb.csv_import.loader import stage_csv
from dxb.csv_import.sources import GEO_SOURCES, TRANSACTION_SOURCES, path_for
from dxb.csv_import.transform import transform_alexefimik, transform_austinpowers
from dxb.db.engine import source_id
from dxb.marts import rebuild_marts
from dxb.osm_geo.enrich import enrich_missing_areas

log = logging.getLogger(__name__)

_TRANSFORMERS = {
    "alexefimik": transform_alexefimik,
    "austinpowers": transform_austinpowers,
}
_GEO_ENRICHERS = {
    "area-coords": geo.enrich_area_coords,
    "project-coords": geo.enrich_project_coords,
}


def import_source(session: Session, key: str, source_url: str) -> dict:
    """Stage + transform a single transaction source."""
    src = TRANSACTION_SOURCES[key]
    sid = source_id(session, src.source_code)
    stage_report = stage_csv(
        session, sid, f"csv:{src.key}", path_for(src), src.encoding
    )
    transform_report = _TRANSFORMERS[key](session, source_url)
    return {"stage": stage_report, "transform": transform_report}


def import_geo(session: Session, key: str) -> dict:
    """Stage + enrich a single coordinate source."""
    src = GEO_SOURCES[key]
    sid = source_id(session, src.source_code)
    stage_report = stage_csv(
        session, sid, f"csv:{src.key}", path_for(src), src.encoding
    )
    enrich_report = _GEO_ENRICHERS[key](session)
    return {"stage": stage_report, "enrich": enrich_report}


def import_all(session: Session, source_url: str) -> dict:
    report: dict = {}
    report["alexefimik"] = import_source(session, "alexefimik", source_url)
    report["austinpowers"] = import_source(session, "austinpowers", source_url)
    report["area-coords"] = import_geo(session, "area-coords")
    report["project-coords"] = import_geo(session, "project-coords")
    report["marts"] = rebuild_marts(session)
    # Same non-fatal hook the daily/backfill pipeline uses — the CSV import
    # can also create new area stubs (dim_area grew 428->439 during the
    # historical transaction import).
    report["geo_enrich"] = enrich_missing_areas(session)
    return report
