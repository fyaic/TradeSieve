"""Create runtime coverage and worker-heartbeat tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260806_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_coverage",
        sa.Column("coverage_kind", sa.String(length=32), nullable=False),
        sa.Column("coverage_id", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("coverage_kind", "coverage_id"),
    )
    op.create_table(
        "runtime_component_heartbeat",
        sa.Column("component", sa.String(length=32), primary_key=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("runtime_component_heartbeat")
    op.drop_table("runtime_coverage")
