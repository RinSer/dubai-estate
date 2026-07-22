"""One-off historical rebuild from the data.dubai exports.

Not part of the daily scheduler. Sequence: import both datasets -> geo enrich
new areas -> set cutovers/watermarks -> rebuild marts.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from dxb.datadubai.cutover import finalize
from dxb.datadubai.importer import import_dataset
from dxb.marts import rebuild_marts
from dxb.osm_geo.enrich import enrich_missing_areas

log = logging.getLogger(__name__)


def import_all(session: Session, source_url: str, with_geo: bool = True) -> dict:
    report: dict = {}
    report["transactions"] = import_dataset(session, "transactions", source_url)
    report["rents"] = import_dataset(session, "rents", source_url)
    if with_geo:
        report["geo_enrich"] = enrich_missing_areas(session)
    report["bookkeeping"] = finalize(session)
    report["marts"] = rebuild_marts(session)
    return report
