"""Per-CSV-source configuration for the historical import (see
docs/CSV_DATA_ANALYSIS.md for the format comparison and overlap analysis
these choices are based on).

Both transaction files re-host DLD registrations (CC0). The two coordinate
files are companion datasets from the same austinpowers Kaggle collection —
used for geo-enrichment (UPDATEs against existing dims), not new facts, so
they're tagged to the austinpowers source rather than a separate dim_source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dxb.config import get_settings


@dataclass(frozen=True)
class CsvSource:
    key: str  # CLI selector, also the stg_raw endpoint suffix ("csv:{key}")
    filename: str
    source_code: str  # dim_source.code
    encoding: str = (
        "utf-8-sig"  # transparently strips a BOM if present, no-op otherwise
    )


TRANSACTION_SOURCES: dict[str, CsvSource] = {
    "alexefimik": CsvSource(
        key="alexefimik",
        filename="Transactions.csv",
        source_code="kaggle_alexefimik",
    ),
    "austinpowers": CsvSource(
        key="austinpowers",
        filename="transactions-2023-07-02.csv",
        source_code="kaggle_austinpowers_h1_2023",
    ),
}

GEO_SOURCES: dict[str, CsvSource] = {
    "area-coords": CsvSource(
        key="area-coords",
        filename="dataframeAreasWithALLCoord.csv",
        source_code="kaggle_austinpowers_h1_2023",
    ),
    "project-coords": CsvSource(
        key="project-coords",
        filename="dataframeProjectWithALLCoord.csv",
        source_code="kaggle_austinpowers_h1_2023",
    ),
}


def path_for(source: CsvSource) -> Path:
    return Path(get_settings().data_raw_dir) / source.filename
