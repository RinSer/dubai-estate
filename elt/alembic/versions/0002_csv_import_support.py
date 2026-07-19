"""csv import support: dim_source.is_government, nullable is_freehold

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dim_source",
        sa.Column(
            "is_government", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.alter_column(
        "fact_sale_transaction",
        "is_freehold",
        existing_type=sa.Boolean(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "fact_sale_transaction",
        "is_freehold",
        existing_type=sa.Boolean(),
        nullable=False,
    )
    op.drop_column("dim_source", "is_government")
