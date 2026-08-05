"""Area code migration support

DLD re-coded ~89 established communities under new area codes starting
2026-07-20 (docs/AREA_CODE_MIGRATION_ANALYSIS.md) — same projects/buildings,
new dld_area_code going forward. Adds:

- dim_area.superseded_by_area_id: nullable self-FK, old row -> new row.
  Purely additive — no existing row's other columns change, no fact/dim row
  referencing an old area is ever rewritten. Only set once a matching
  area_code_evidence row is human-reviewed.
- area_code_evidence: audit table for the detector's findings (project/
  building overlap, transaction counts, first-seen date, reviewed gate).
  Never consulted by aggregation/resolution directly — only the pointer is.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from dxb_core.models import Base

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "dim_area",
        sa.Column(
            "superseded_by_area_id",
            sa.Integer(),
            sa.ForeignKey("dim_area.id"),
            nullable=True,
        ),
    )
    Base.metadata.tables["area_code_evidence"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["area_code_evidence"].drop(bind=bind)
    op.drop_column("dim_area", "superseded_by_area_id")
