"""CSV row -> canonical fact dict -> DimCaches/_upsert_facts.

Reuses transform.dld's machinery end to end (DimCaches, the batched guarded
upsert, staging-batch iteration) — only the per-source row mapping is new,
since each CSV has a different column layout and date format (see
docs/CSV_DATA_ANALYSIS.md §2 for the verified field mapping).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from dxb.csv_import.dedupe import dedupe_key, load_alexefimik_keys
from dxb.db.models import FactSaleTransaction, StgRaw
from dxb.transform.dld import (
    DimCaches,
    _mark_processed,
    _staged_batches,
    _upsert_facts,
    to_float,
    to_text,
)

log = logging.getLogger(__name__)

_SALE_UPDATE_COLS = [
    "txn_group",
    "procedure_name",
    "is_offplan",
    "is_freehold",
    "property_type_id",
    "rooms",
    "parking",
    "area_id",
    "project_id",
    "parcel_id",
    "amount_aed",
    "source_ref",
]
_SALE_KEY_COLS = ["source_id", "txn_number", "txn_date", "actual_area_m2"]


def _freehold_from_text(value) -> bool | None:
    """ "Free Hold" / "Non Free Hold" -> bool (same vocabulary as the DLD
    gateway's own IS_FREE_HOLD_EN field)."""
    text = to_text(value)
    if text is None:
        return None
    return text.strip().lower() == "free hold"


def sale_values_alexefimik(
    stg: StgRaw, caches: DimCaches, source_url: str
) -> dict | None:
    row = stg.payload_json
    txn_number = to_text(row.get("transaction_id"))
    date_raw = to_text(row.get("instance_date"))  # DD-MM-YYYY
    amount = to_float(row.get("actual_worth"))
    area_id = caches.area(row.get("area_name_en"), row.get("area_name_ar"))
    if not txn_number or not date_raw or amount is None or area_id is None:
        return None
    try:
        txn_date = datetime.strptime(date_raw, "%d-%m-%Y")
    except ValueError:
        return None
    return {
        "txn_number": txn_number,
        "txn_date": txn_date,
        "txn_group": to_text(row.get("trans_group_en")) or "Unknown",
        "procedure_name": to_text(row.get("procedure_name_en")),
        "is_offplan": to_text(row.get("reg_type_en")) == "Off-Plan Properties",
        # No freehold-flag column exists in this source at all — NULL
        # ("unknown"), not a fabricated default. See models.py.
        "is_freehold": None,
        "property_type_id": caches.ptype(
            row.get("property_usage_en"),
            row.get("property_type_en"),
            row.get("property_sub_type_en"),
        ),
        "rooms": to_text(row.get("rooms_en")),
        "parking": to_text(row.get("has_parking")),
        "area_id": area_id,
        "project_id": caches.project(
            row.get("project_name_en"),
            master_name=to_text(row.get("master_project_en")),
            area_id=area_id,
            name_ar=row.get("project_name_ar"),
        ),
        "parcel_id": None,  # not present in this source
        "actual_area_m2": to_float(row.get("procedure_area")),
        "amount_aed": amount,
        "source_id": stg.source_id,
        "source_url": source_url,
        "source_ref": txn_number,
    }


def sale_values_austinpowers(
    stg: StgRaw, caches: DimCaches, source_url: str
) -> dict | None:
    row = stg.payload_json
    txn_number = to_text(row.get("Transaction Number"))
    date_raw = to_text(row.get("Transaction Date"))  # YYYY-MM-DD HH:MM:SS
    amount = to_float(row.get("Amount"))
    area_id = caches.area(row.get("Area"))
    if not txn_number or not date_raw or amount is None or area_id is None:
        return None
    try:
        txn_date = datetime.fromisoformat(date_raw)
    except ValueError:
        return None
    return {
        "txn_number": txn_number,
        "txn_date": txn_date,
        "txn_group": to_text(row.get("Transaction Type")) or "Unknown",
        "procedure_name": to_text(row.get("Transaction sub type")),
        "is_offplan": to_text(row.get("Registration type")) == "Off-Plan",
        "is_freehold": _freehold_from_text(row.get("Is Free Hold?")),
        "property_type_id": caches.ptype(
            row.get("Usage"), row.get("Property Type"), row.get("Property Sub Type")
        ),
        "rooms": to_text(row.get("Room(s)")),
        "parking": to_text(row.get("Parking")),
        "area_id": area_id,
        "project_id": caches.project(
            row.get("Project"),
            master_name=to_text(row.get("Master Project")),
            area_id=area_id,
        ),
        "parcel_id": None,  # not present in this source
        "actual_area_m2": to_float(row.get("Transaction Size (sq.m)")),
        "amount_aed": amount,
        "source_id": stg.source_id,
        "source_url": source_url,
        "source_ref": txn_number,
    }


def _transform_source(
    session: Session,
    endpoint: str,
    mapper,
    source_url: str,
    *,
    dedupe_against: set[tuple] | None = None,
) -> dict:
    caches = DimCaches(session)
    written = skipped = duplicates = 0
    for batch in _staged_batches(session, endpoint):
        values = []
        for stg in batch:
            v = mapper(stg, caches, source_url)
            if v is None:
                skipped += 1
                continue
            if dedupe_against is not None:
                key = dedupe_key(
                    v["txn_date"], v["area_id"], v["actual_area_m2"], v["amount_aed"]
                )
                if key in dedupe_against:
                    duplicates += 1
                    continue
            values.append(v)
        written += _upsert_facts(
            session,
            FactSaleTransaction.__table__,
            "ux_sale_natural",
            _SALE_KEY_COLS,
            _SALE_UPDATE_COLS,
            values,
        )
        _mark_processed(session, batch)
        session.commit()
    return {
        "endpoint": endpoint,
        "written": written,
        "skipped": skipped,
        "duplicates": duplicates,
    }


def transform_alexefimik(session: Session, source_url: str) -> dict:
    return _transform_source(
        session, "csv:alexefimik", sale_values_alexefimik, source_url
    )


def transform_austinpowers(session: Session, source_url: str) -> dict:
    dedupe_against = load_alexefimik_keys(session)
    log.info(
        "loaded %s alexefimik dedupe keys for austinpowers import", len(dedupe_against)
    )
    return _transform_source(
        session,
        "csv:austinpowers",
        sale_values_austinpowers,
        source_url,
        dedupe_against=dedupe_against,
    )
