"""Create governed source registry and runtime observation tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260806_0002"
down_revision = "20260806_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION tradesieve_valid_source_manifest(value JSONB)
        RETURNS BOOLEAN
        LANGUAGE SQL
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
            SELECT CASE
                WHEN jsonb_typeof(value) <> 'array' THEN FALSE
                ELSE (
                    SELECT
                        count(*) <= 256
                        AND COALESCE(
                            bool_and(
                                jsonb_typeof(item) = 'string'
                                AND (item #>> '{}') ~
                                    '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                            ),
                            TRUE
                        )
                        AND COALESCE(
                            array_agg((item #>> '{}') ORDER BY ordinal) =
                            array_agg(
                                (item #>> '{}')
                                ORDER BY (item #>> '{}') COLLATE "C"
                            ),
                            TRUE
                        )
                        AND count(*) = count(DISTINCT (item #>> '{}'))
                    FROM jsonb_array_elements(value)
                        WITH ORDINALITY AS members(item, ordinal)
                )
            END
        $$
        """
    )
    op.create_table(
        "source_set_manifest",
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("source_set_id", sa.String(length=128), nullable=False),
        sa.Column("required_source_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_source_manifest_deployment_id",
        ),
        sa.CheckConstraint(
            "source_set_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_source_manifest_set_id",
        ),
        sa.CheckConstraint(
            "tradesieve_valid_source_manifest(required_source_ids)",
            name="ck_source_manifest_required_members",
        ),
        sa.PrimaryKeyConstraint("deployment_id", "source_set_id"),
    )
    op.create_table(
        "source_registry",
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("source_set_id", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("responsible_operator", sa.Text(), nullable=True),
        sa.Column("jurisdiction", sa.Text(), nullable=True),
        sa.Column("legal_scope", sa.Text(), nullable=True),
        sa.Column("data_scope", sa.Text(), nullable=True),
        sa.Column("access_method", sa.String(length=32), nullable=True),
        sa.Column("credential_secret_ref", sa.Text(), nullable=True),
        sa.Column("licence_summary", sa.Text(), nullable=True),
        sa.Column("contractual_constraints", sa.Text(), nullable=True),
        sa.Column("refresh_expectation_seconds", sa.Integer(), nullable=True),
        sa.Column("stale_after_seconds", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "refresh_expectation_seconds IS NULL "
            "OR refresh_expectation_seconds BETWEEN 1 AND 316224000",
            name="ck_source_refresh_bounds",
        ),
        sa.CheckConstraint(
            "stale_after_seconds IS NULL "
            "OR stale_after_seconds BETWEEN 1 AND 316224000",
            name="ck_source_stale_bounds",
        ),
        sa.CheckConstraint(
            "deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' "
            "AND source_set_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' "
            "AND source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_source_registry_ids",
        ),
        sa.CheckConstraint(
            "access_method IS NULL OR access_method IN "
            "('PUBLIC_DOWNLOAD', 'API', 'SFTP', 'MANUAL_UPLOAD', 'INTERNAL')",
            name="ck_source_access_method",
        ),
        sa.CheckConstraint(
            "credential_secret_ref IS NULL OR credential_secret_ref ~ "
            "'^(env|vault|secret-manager|file-secret):"
            "[A-Za-z0-9][A-Za-z0-9._/:-]{0,239}$'",
            name="ck_source_credential_reference",
        ),
        sa.CheckConstraint(
            "NOT active OR ("
            "NULLIF(BTRIM(name), '') IS NOT NULL AND "
            "NULLIF(BTRIM(owner), '') IS NOT NULL AND "
            "NULLIF(BTRIM(responsible_operator), '') IS NOT NULL AND "
            "NULLIF(BTRIM(jurisdiction), '') IS NOT NULL AND "
            "NULLIF(BTRIM(legal_scope), '') IS NOT NULL AND "
            "NULLIF(BTRIM(data_scope), '') IS NOT NULL AND "
            "access_method IS NOT NULL AND "
            "NULLIF(BTRIM(licence_summary), '') IS NOT NULL AND "
            "refresh_expectation_seconds IS NOT NULL AND "
            "stale_after_seconds IS NOT NULL AND "
            "stale_after_seconds >= refresh_expectation_seconds)",
            name="ck_active_source_governance",
        ),
        sa.CheckConstraint(
            "COALESCE(LENGTH(name), 0) <= 256 AND "
            "COALESCE(LENGTH(owner), 0) <= 256 AND "
            "COALESCE(LENGTH(responsible_operator), 0) <= 256 AND "
            "COALESCE(LENGTH(jurisdiction), 0) <= 256 AND "
            "COALESCE(LENGTH(legal_scope), 0) <= 2000 AND "
            "COALESCE(LENGTH(data_scope), 0) <= 2000 AND "
            "COALESCE(LENGTH(licence_summary), 0) <= 2000 AND "
            "COALESCE(LENGTH(contractual_constraints), 0) <= 2000",
            name="ck_source_governance_lengths",
        ),
        sa.PrimaryKeyConstraint("deployment_id", "source_id"),
    )
    op.create_index(
        "ix_source_registry_set",
        "source_registry",
        ["deployment_id", "source_set_id", "source_id"],
    )
    op.create_table(
        "source_runtime_observation",
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("availability", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_snapshot_id", sa.String(length=128), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "availability IN ('AVAILABLE', 'UNAVAILABLE', 'QUARANTINED')",
            name="ck_source_observation_availability",
        ),
        sa.CheckConstraint(
            "active_snapshot_id IS NULL OR active_snapshot_id ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_source_observation_snapshot_id",
        ),
        sa.CheckConstraint(
            "retrieved_at IS NULL OR retrieved_at <= observed_at",
            name="ck_source_observation_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id", "source_id"],
            ["source_registry.deployment_id", "source_registry.source_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("deployment_id", "source_id"),
    )


def downgrade() -> None:
    op.drop_table("source_runtime_observation")
    op.drop_index("ix_source_registry_set", table_name="source_registry")
    op.drop_table("source_registry")
    op.drop_table("source_set_manifest")
    op.execute("DROP FUNCTION tradesieve_valid_source_manifest(JSONB)")
