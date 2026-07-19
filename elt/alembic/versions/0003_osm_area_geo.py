"""OSM area geo-enrichment: dim_area.geo_source_id / geo_match_method

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dim_area",
        sa.Column(
            "geo_source_id", sa.Integer(), sa.ForeignKey("dim_source.id"), nullable=True
        ),
    )
    op.add_column("dim_area", sa.Column("geo_match_method", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dim_area", "geo_match_method")
    op.drop_column("dim_area", "geo_source_id")
