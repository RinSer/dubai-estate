"""data.dubai bulk-export file discovery.

Exports arrive as multi-part CSVs named
`{dataset}_{export-timestamp}_{NNNN}.csv` (transactions: 2 parts,
rent contracts: 10). See docs/DATADUBAI_ANALYSIS.md.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

from dxb.config import get_settings


@dataclass(frozen=True)
class DataDubaiDataset:
    key: str  # CLI selector
    pattern: str  # filename glob within data/raw
    source_code: str  # dim_source.code
    encoding: str = "utf-8-sig"  # strips a BOM if present, no-op otherwise


DATASETS: dict[str, DataDubaiDataset] = {
    "transactions": DataDubaiDataset(
        key="transactions",
        pattern="transactions_*.csv",
        source_code="datadubai_transactions",
    ),
    "rents": DataDubaiDataset(
        key="rents",
        pattern="rent_contracts_*.csv",
        source_code="datadubai_rents",
    ),
}


def data_raw_dir() -> Path:
    return Path(get_settings().data_raw_dir)


def files_for(dataset: DataDubaiDataset, root: Path | None = None) -> list[Path]:
    """All parts of an export, in stable part order."""
    base = root or data_raw_dir()
    found = sorted(glob.glob(os.path.join(str(base), dataset.pattern)))
    return [Path(p) for p in found]
