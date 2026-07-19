"""Area/project coordinate enrichment from the two companion austinpowers
CSVs — UPDATEs against existing dim_area/dim_project rows matched by
normalized name, not new facts. Runs after both transaction CSVs so it also
catches coordinates for any new historical stub rows they created.
"""

from __future__ import annotations

import logging

from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session

from dxb.db.models import DimArea, DimProject
from dxb.transform.dld import _mark_processed, _staged_batches, norm_name, to_float

log = logging.getLogger(__name__)


def enrich_area_coords(session: Session) -> dict:
    matched = unmatched = 0
    for batch in _staged_batches(session, "csv:area-coords"):
        for stg in batch:
            row = stg.payload_json
            name = norm_name(row.get("Area"))
            lat = to_float(row.get("Latitude_Area"))
            lon = to_float(row.get("Longitude_Area"))
            if not name or lat is None or lon is None:
                unmatched += 1
                continue
            area = session.scalar(select(DimArea).where(DimArea.name_en == name))
            if area is None:
                unmatched += 1
                continue
            area.centroid = WKTElement(f"POINT({lon} {lat})", srid=4326)
            matched += 1
        _mark_processed(session, batch)
        session.commit()
    log.info("area coords: matched=%s unmatched=%s", matched, unmatched)
    return {"endpoint": "csv:area-coords", "matched": matched, "unmatched": unmatched}


def enrich_project_coords(session: Session) -> dict:
    matched = unmatched = 0
    for batch in _staged_batches(session, "csv:project-coords"):
        for stg in batch:
            row = stg.payload_json
            name = norm_name(row.get("Project"))
            lat = to_float(row.get("Latitude_Project"))
            lon = to_float(row.get("Longitude_Project"))
            if not name or lat is None or lon is None:
                unmatched += 1
                continue
            project = session.scalar(
                select(DimProject).where(
                    DimProject.name_en == name, DimProject.is_master.is_(False)
                )
            )
            if project is None:
                unmatched += 1
                continue
            project.location = WKTElement(f"POINT({lon} {lat})", srid=4326)
            matched += 1
        _mark_processed(session, batch)
        session.commit()
    log.info("project coords: matched=%s unmatched=%s", matched, unmatched)
    return {
        "endpoint": "csv:project-coords",
        "matched": matched,
        "unmatched": unmatched,
    }
