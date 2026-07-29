"""mart_building_summary

Adds the buildings mart (docs/BUILDING_MART_ANALYSIS.md): one row per
(building_id, usage), not a monthly grain — measured before building it that a
monthly grain would be 42% single-sale cells. Sales-only, permanently: the
data.dubai rent export carries no building name to join on.

Table created from its model definition (like 0005), so the column list stays
in one place.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-27
"""

from dxb_core.models import Base

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["mart_building_summary"].create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["mart_building_summary"].drop(bind=bind)
