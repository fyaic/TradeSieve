"""Create immutable official-source projections and an atomic active bundle."""

from __future__ import annotations

from alembic import op

revision = "20260810_0006"
down_revision = "20260806_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE official_source_raw_object (
            content_hash VARCHAR(71) NOT NULL,
            byte_length INTEGER NOT NULL,
            media_type VARCHAR(128) NOT NULL,
            content BYTEA NOT NULL,
            first_retrieved_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_official_source_raw_object PRIMARY KEY (content_hash),
            CONSTRAINT ck_official_source_raw_hash CHECK (
                content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_official_source_raw_shape CHECK (
                byte_length BETWEEN 1 AND 67108864 AND
                octet_length(content) = byte_length AND
                octet_length(media_type) BETWEEN 1 AND 128 AND
                content_hash = 'sha256:' || encode(sha256(content), 'hex')
            ),
            CONSTRAINT ck_official_source_raw_time CHECK (
                isfinite(first_retrieved_at)
            )
        );

        CREATE TABLE eu_fsf_official_snapshot (
            snapshot_id VARCHAR(71) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            raw_content_hash VARCHAR(71) NOT NULL,
            raw_byte_length INTEGER NOT NULL,
            generation_date TIMESTAMPTZ NOT NULL,
            generation_date_text VARCHAR(64) NOT NULL,
            global_file_id VARCHAR(19) NOT NULL,
            first_retrieved_at TIMESTAMPTZ NOT NULL,
            entity_count INTEGER NOT NULL,
            alias_count INTEGER NOT NULL,
            identifier_count INTEGER NOT NULL,
            CONSTRAINT pk_eu_fsf_official_snapshot PRIMARY KEY (snapshot_id),
            CONSTRAINT uq_eu_fsf_official_snapshot_ref UNIQUE
                (snapshot_id, snapshot_content_hash),
            CONSTRAINT uq_eu_fsf_official_snapshot_hash UNIQUE
                (snapshot_content_hash),
            CONSTRAINT ck_eu_fsf_official_snapshot_ids CHECK (
                snapshot_id ~ '^eu-fsf-[a-f0-9]{64}$' AND
                snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                snapshot_id = 'eu-fsf-' || substr(snapshot_content_hash, 8) AND
                raw_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                global_file_id ~ '^[1-9][0-9]{0,18}$'
            ),
            CONSTRAINT ck_eu_fsf_official_snapshot_counts CHECK (
                raw_byte_length BETWEEN 1 AND 67108864 AND
                entity_count BETWEEN 1 AND 20000 AND
                alias_count BETWEEN entity_count AND 5120000 AND
                identifier_count BETWEEN 0 AND 1280000
            ),
            CONSTRAINT ck_eu_fsf_official_snapshot_time CHECK (
                isfinite(generation_date) AND
                isfinite(first_retrieved_at) AND
                generation_date_text ~
                    '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*[+-][0-9]{2}:[0-9]{2}$' AND
                generation_date_text::TIMESTAMPTZ = generation_date AND
                generation_date <= first_retrieved_at
            ),
            CONSTRAINT fk_eu_fsf_official_snapshot_raw FOREIGN KEY
                (raw_content_hash)
                REFERENCES official_source_raw_object (content_hash)
        );

        CREATE TABLE eu_fsf_official_entity (
            snapshot_id VARCHAR(71) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            entity_logical_id VARCHAR(19) NOT NULL,
            eu_reference_number VARCHAR(128) NOT NULL,
            united_nations_id VARCHAR(128),
            designation_date DATE,
            subject_type VARCHAR(16) NOT NULL,
            regulation_programme VARCHAR(64) NOT NULL,
            regulation_number_title VARCHAR(256) NOT NULL,
            regulation_publication_date DATE NOT NULL,
            regulation_entry_into_force_date DATE NOT NULL,
            regulation_publication_url VARCHAR(2048) NOT NULL,
            regulation_native_locator VARCHAR(512) NOT NULL,
            entity_content_hash VARCHAR(71) NOT NULL,
            CONSTRAINT pk_eu_fsf_official_entity PRIMARY KEY
                (snapshot_id, entity_logical_id),
            CONSTRAINT uq_eu_fsf_official_entity_reference UNIQUE
                (snapshot_id, eu_reference_number),
            CONSTRAINT uq_eu_fsf_official_entity_child_ref UNIQUE
                (snapshot_id, snapshot_content_hash, entity_logical_id),
            CONSTRAINT ck_eu_fsf_official_entity_ids CHECK (
                entity_logical_id ~ '^[1-9][0-9]{0,18}$' AND
                eu_reference_number ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' AND
                entity_content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_eu_fsf_official_entity_shape CHECK (
                subject_type IN ('person', 'enterprise') AND
                octet_length(regulation_programme) BETWEEN 1 AND 64 AND
                octet_length(regulation_number_title) BETWEEN 1 AND 256 AND
                octet_length(regulation_publication_url) BETWEEN 1 AND 2048 AND
                octet_length(regulation_native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_eu_fsf_official_entity_snapshot FOREIGN KEY
                (snapshot_id, snapshot_content_hash)
                REFERENCES eu_fsf_official_snapshot
                (snapshot_id, snapshot_content_hash)
        );

        CREATE TABLE eu_fsf_official_alias (
            snapshot_id VARCHAR(71) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            entity_logical_id VARCHAR(19) NOT NULL,
            alias_logical_id VARCHAR(19) NOT NULL,
            whole_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            strong BOOLEAN NOT NULL,
            language VARCHAR(16),
            native_locator VARCHAR(512) NOT NULL,
            assertion_hash VARCHAR(71) NOT NULL,
            CONSTRAINT pk_eu_fsf_official_alias PRIMARY KEY
                (snapshot_id, entity_logical_id, alias_logical_id),
            CONSTRAINT ck_eu_fsf_official_alias_ids CHECK (
                alias_logical_id ~ '^[1-9][0-9]{0,18}$' AND
                assertion_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_eu_fsf_official_alias_shape CHECK (
                octet_length(whole_name) BETWEEN 1 AND 4096 AND
                octet_length(normalized_name) BETWEEN 1 AND 4096 AND
                (language IS NULL OR octet_length(language) BETWEEN 1 AND 16) AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_eu_fsf_official_alias_entity FOREIGN KEY
                (snapshot_id, snapshot_content_hash, entity_logical_id)
                REFERENCES eu_fsf_official_entity
                (snapshot_id, snapshot_content_hash, entity_logical_id)
        );

        CREATE TABLE eu_fsf_official_identifier (
            snapshot_id VARCHAR(71) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            entity_logical_id VARCHAR(19) NOT NULL,
            identifier_logical_id VARCHAR(19) NOT NULL,
            type_code VARCHAR(32) NOT NULL,
            number TEXT NOT NULL,
            normalized_number VARCHAR(256) NOT NULL,
            country_code VARCHAR(2),
            known_expired BOOLEAN NOT NULL,
            known_false BOOLEAN NOT NULL,
            reported_lost BOOLEAN NOT NULL,
            revoked_by_issuer BOOLEAN NOT NULL,
            native_locator VARCHAR(512) NOT NULL,
            assertion_hash VARCHAR(71) NOT NULL,
            CONSTRAINT pk_eu_fsf_official_identifier PRIMARY KEY
                (snapshot_id, entity_logical_id, identifier_logical_id),
            CONSTRAINT ck_eu_fsf_official_identifier_ids CHECK (
                identifier_logical_id ~ '^[1-9][0-9]{0,18}$' AND
                assertion_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_eu_fsf_official_identifier_shape CHECK (
                type_code IN (
                    'birthcert', 'drivinglicence', 'electionid', 'euvat',
                    'fiscalcode', 'id', 'imo', 'nationcert', 'other',
                    'passport', 'regnumber', 'residentperm', 'ssn',
                    'swiftbic', 'taxid', 'tradelic', 'travelcardid', 'unssn'
                ) AND
                octet_length(number) BETWEEN 1 AND 4096 AND
                octet_length(normalized_number) BETWEEN 1 AND 256 AND
                (country_code IS NULL OR country_code ~ '^[A-Z]{2}$') AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_eu_fsf_official_identifier_entity FOREIGN KEY
                (snapshot_id, snapshot_content_hash, entity_logical_id)
                REFERENCES eu_fsf_official_entity
                (snapshot_id, snapshot_content_hash, entity_logical_id)
        );

        CREATE TABLE eu_dual_use_official_snapshot (
            snapshot_id VARCHAR(79) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            raw_content_hash VARCHAR(71) NOT NULL,
            raw_byte_length INTEGER NOT NULL,
            formex_document_hash VARCHAR(71) NOT NULL,
            celex VARCHAR(16) NOT NULL,
            effective_from DATE NOT NULL,
            first_retrieved_at TIMESTAMPTZ NOT NULL,
            entry_count INTEGER NOT NULL,
            CONSTRAINT pk_eu_dual_use_official_snapshot PRIMARY KEY (snapshot_id),
            CONSTRAINT uq_eu_dual_use_official_snapshot_ref UNIQUE
                (snapshot_id, snapshot_content_hash),
            CONSTRAINT uq_eu_dual_use_official_snapshot_hash UNIQUE
                (snapshot_content_hash),
            CONSTRAINT ck_eu_dual_use_official_snapshot_ids CHECK (
                snapshot_id ~ '^eu-dual-use-[a-f0-9]{64}$' AND
                snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                snapshot_id = 'eu-dual-use-' || substr(snapshot_content_hash, 8) AND
                raw_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                formex_document_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_eu_dual_use_official_snapshot_shape CHECK (
                raw_byte_length BETWEEN 1 AND 8388608 AND
                celex = '32025R2003' AND
                effective_from = DATE '2025-11-15' AND
                entry_count BETWEEN 300 AND 1000
            ),
            CONSTRAINT ck_eu_dual_use_official_snapshot_time CHECK (
                isfinite(first_retrieved_at)
            ),
            CONSTRAINT fk_eu_dual_use_official_snapshot_raw FOREIGN KEY
                (raw_content_hash)
                REFERENCES official_source_raw_object (content_hash)
        );

        CREATE TABLE eu_dual_use_official_entry (
            snapshot_id VARCHAR(79) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            control_code VARCHAR(5) NOT NULL,
            entry_sequence INTEGER NOT NULL,
            control_text TEXT NOT NULL,
            native_locator VARCHAR(512) NOT NULL,
            entry_content_hash VARCHAR(71) NOT NULL,
            CONSTRAINT pk_eu_dual_use_official_entry PRIMARY KEY
                (snapshot_id, control_code),
            CONSTRAINT uq_eu_dual_use_official_entry_sequence UNIQUE
                (snapshot_id, entry_sequence),
            CONSTRAINT ck_eu_dual_use_official_entry_ids CHECK (
                control_code ~ '^[0-9][A-E][0-9]{3}$' AND
                entry_sequence BETWEEN 1 AND 1000 AND
                entry_content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_eu_dual_use_official_entry_shape CHECK (
                octet_length(control_text) BETWEEN 1 AND 65536 AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_eu_dual_use_official_entry_snapshot FOREIGN KEY
                (snapshot_id, snapshot_content_hash)
                REFERENCES eu_dual_use_official_snapshot
                (snapshot_id, snapshot_content_hash)
        );

        CREATE TABLE official_screening_source_bundle (
            bundle_id VARCHAR(80) NOT NULL,
            bundle_content_hash VARCHAR(71) NOT NULL,
            fsf_snapshot_id VARCHAR(71) NOT NULL,
            fsf_snapshot_content_hash VARCHAR(71) NOT NULL,
            dual_use_snapshot_id VARCHAR(79) NOT NULL,
            dual_use_snapshot_content_hash VARCHAR(71) NOT NULL,
            first_activated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_official_screening_source_bundle PRIMARY KEY (bundle_id),
            CONSTRAINT uq_official_screening_source_bundle_hash UNIQUE
                (bundle_content_hash),
            CONSTRAINT uq_official_screening_source_bundle_ref UNIQUE
                (bundle_id, bundle_content_hash),
            CONSTRAINT ck_official_screening_source_bundle_ids CHECK (
                bundle_id ~ '^official-bundle-[a-f0-9]{64}$' AND
                bundle_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                bundle_id = 'official-bundle-' || substr(bundle_content_hash, 8)
            ),
            CONSTRAINT ck_official_screening_source_bundle_time CHECK (
                isfinite(first_activated_at)
            ),
            CONSTRAINT fk_official_screening_source_bundle_fsf FOREIGN KEY
                (fsf_snapshot_id, fsf_snapshot_content_hash)
                REFERENCES eu_fsf_official_snapshot
                (snapshot_id, snapshot_content_hash),
            CONSTRAINT fk_official_screening_source_bundle_dual FOREIGN KEY
                (dual_use_snapshot_id, dual_use_snapshot_content_hash)
                REFERENCES eu_dual_use_official_snapshot
                (snapshot_id, snapshot_content_hash)
        );

        CREATE TABLE official_screening_source_activation_event (
            sequence BIGINT GENERATED ALWAYS AS IDENTITY,
            bundle_id VARCHAR(80) NOT NULL,
            bundle_content_hash VARCHAR(71) NOT NULL,
            activated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_official_screening_source_activation_event
                PRIMARY KEY (sequence),
            CONSTRAINT uq_official_screening_source_activation_bundle
                UNIQUE (bundle_id),
            CONSTRAINT ck_official_screening_source_activation_sequence CHECK
                (sequence BETWEEN 1 AND 9223372036854775807),
            CONSTRAINT ck_official_screening_source_activation_time CHECK
                (isfinite(activated_at)),
            CONSTRAINT fk_official_screening_source_activation_bundle FOREIGN KEY
                (bundle_id, bundle_content_hash)
                REFERENCES official_screening_source_bundle
                (bundle_id, bundle_content_hash)
        );

        CREATE TABLE official_screening_source_state (
            singleton BOOLEAN NOT NULL DEFAULT TRUE,
            active_bundle_id VARCHAR(80) NOT NULL,
            active_bundle_content_hash VARCHAR(71) NOT NULL,
            fsf_observed_at TIMESTAMPTZ NOT NULL,
            dual_use_observed_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            sequence BIGINT NOT NULL,
            CONSTRAINT pk_official_screening_source_state PRIMARY KEY (singleton),
            CONSTRAINT ck_official_screening_source_state_singleton CHECK (singleton),
            CONSTRAINT ck_official_screening_source_state_time CHECK (
                isfinite(fsf_observed_at) AND
                isfinite(dual_use_observed_at) AND
                isfinite(updated_at) AND
                fsf_observed_at <= updated_at AND
                dual_use_observed_at <= updated_at
            ),
            CONSTRAINT ck_official_screening_source_state_sequence CHECK
                (sequence BETWEEN 1 AND 9223372036854775807),
            CONSTRAINT fk_official_screening_source_state_bundle FOREIGN KEY
                (active_bundle_id, active_bundle_content_hash)
                REFERENCES official_screening_source_bundle
                (bundle_id, bundle_content_hash),
            CONSTRAINT fk_official_screening_source_state_event FOREIGN KEY
                (sequence)
                REFERENCES official_screening_source_activation_event (sequence)
        );

        CREATE FUNCTION tradesieve_validate_official_projection_counts()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            actual_entities BIGINT;
            actual_aliases BIGINT;
            actual_identifiers BIGINT;
            actual_entries BIGINT;
            expected_entities BIGINT;
            expected_aliases BIGINT;
            expected_identifiers BIGINT;
            expected_entries BIGINT;
        BEGIN
            IF TG_TABLE_NAME LIKE 'eu_fsf_official_%' THEN
                SELECT entity_count, alias_count, identifier_count
                INTO expected_entities, expected_aliases, expected_identifiers
                FROM eu_fsf_official_snapshot
                WHERE snapshot_id = NEW.snapshot_id;
                SELECT count(*) INTO actual_entities
                FROM eu_fsf_official_entity
                WHERE snapshot_id = NEW.snapshot_id;
                SELECT count(*) INTO actual_aliases
                FROM eu_fsf_official_alias
                WHERE snapshot_id = NEW.snapshot_id;
                SELECT count(*) INTO actual_identifiers
                FROM eu_fsf_official_identifier
                WHERE snapshot_id = NEW.snapshot_id;
                IF actual_entities <> expected_entities OR
                   actual_aliases <> expected_aliases OR
                   actual_identifiers <> expected_identifiers THEN
                    RAISE EXCEPTION 'EU FSF projection count mismatch'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT entry_count INTO expected_entries
                FROM eu_dual_use_official_snapshot
                WHERE snapshot_id = NEW.snapshot_id;
                SELECT count(*) INTO actual_entries
                FROM eu_dual_use_official_entry
                WHERE snapshot_id = NEW.snapshot_id;
                IF actual_entries <> expected_entries THEN
                    RAISE EXCEPTION 'EU dual-use projection count mismatch'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION tradesieve_reject_activated_fsf_projection_insert()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM inserted_rows AS inserted
                JOIN official_screening_source_bundle AS bundle
                  ON bundle.fsf_snapshot_id = inserted.snapshot_id
            ) THEN
                RAISE EXCEPTION 'activated official projection is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION tradesieve_reject_activated_dual_projection_insert()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM inserted_rows AS inserted
                JOIN official_screening_source_bundle AS bundle
                  ON bundle.dual_use_snapshot_id = inserted.snapshot_id
            ) THEN
                RAISE EXCEPTION 'activated official projection is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE INDEX ix_eu_fsf_official_alias_lookup
            ON eu_fsf_official_alias (snapshot_id, normalized_name);
        CREATE INDEX ix_eu_fsf_official_identifier_lookup
            ON eu_fsf_official_identifier
            (snapshot_id, type_code, normalized_number);
        CREATE INDEX ix_official_source_activation_time
            ON official_screening_source_activation_event
            (activated_at, sequence);

        CREATE TRIGGER trg_official_source_raw_immutable
            BEFORE UPDATE OR DELETE ON official_source_raw_object
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_fsf_snapshot_immutable
            BEFORE UPDATE OR DELETE ON eu_fsf_official_snapshot
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_fsf_entity_immutable
            BEFORE UPDATE OR DELETE ON eu_fsf_official_entity
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_fsf_alias_immutable
            BEFORE UPDATE OR DELETE ON eu_fsf_official_alias
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_fsf_identifier_immutable
            BEFORE UPDATE OR DELETE ON eu_fsf_official_identifier
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_fsf_entity_activated_insert
            AFTER INSERT ON eu_fsf_official_entity
            REFERENCING NEW TABLE AS inserted_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_fsf_projection_insert();
        CREATE TRIGGER trg_eu_fsf_alias_activated_insert
            AFTER INSERT ON eu_fsf_official_alias
            REFERENCING NEW TABLE AS inserted_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_fsf_projection_insert();
        CREATE TRIGGER trg_eu_fsf_identifier_activated_insert
            AFTER INSERT ON eu_fsf_official_identifier
            REFERENCING NEW TABLE AS inserted_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_fsf_projection_insert();
        CREATE TRIGGER trg_eu_dual_use_snapshot_immutable
            BEFORE UPDATE OR DELETE ON eu_dual_use_official_snapshot
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_dual_use_entry_immutable
            BEFORE UPDATE OR DELETE ON eu_dual_use_official_entry
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_eu_dual_use_entry_activated_insert
            AFTER INSERT ON eu_dual_use_official_entry
            REFERENCING NEW TABLE AS inserted_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_dual_projection_insert();
        CREATE TRIGGER trg_official_source_bundle_immutable
            BEFORE UPDATE OR DELETE ON official_screening_source_bundle
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_official_source_activation_immutable
            BEFORE UPDATE OR DELETE ON official_screening_source_activation_event
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE CONSTRAINT TRIGGER trg_eu_fsf_projection_counts
            AFTER INSERT ON eu_fsf_official_snapshot
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_official_projection_counts();
        CREATE CONSTRAINT TRIGGER trg_eu_dual_use_projection_counts
            AFTER INSERT ON eu_dual_use_official_snapshot
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_official_projection_counts();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE official_screening_source_state;
        DROP TABLE official_screening_source_activation_event;
        DROP TABLE official_screening_source_bundle;
        DROP TABLE eu_dual_use_official_entry;
        DROP TABLE eu_dual_use_official_snapshot;
        DROP TABLE eu_fsf_official_identifier;
        DROP TABLE eu_fsf_official_alias;
        DROP TABLE eu_fsf_official_entity;
        DROP TABLE eu_fsf_official_snapshot;
        DROP TABLE official_source_raw_object;
        DROP FUNCTION tradesieve_reject_activated_dual_projection_insert();
        DROP FUNCTION tradesieve_reject_activated_fsf_projection_insert();
        DROP FUNCTION tradesieve_validate_official_projection_counts();
        """
    )
