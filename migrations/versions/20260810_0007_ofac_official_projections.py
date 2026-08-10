"""Add immutable OFAC projections to the complete active source set."""

from __future__ import annotations

from alembic import op

revision = "20260810_0007"
down_revision = "20260810_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE ofac_sls_official_snapshot (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            publish_date DATE NOT NULL,
            declared_record_count INTEGER NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL,
            source_last_modified TIMESTAMPTZ,
            raw_content_hash VARCHAR(71) NOT NULL,
            raw_byte_length INTEGER NOT NULL,
            entry_count INTEGER NOT NULL,
            program_count INTEGER NOT NULL,
            alias_count INTEGER NOT NULL,
            address_count INTEGER NOT NULL,
            identifier_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            vessel_count INTEGER NOT NULL,
            CONSTRAINT pk_ofac_sls_official_snapshot PRIMARY KEY (snapshot_id),
            CONSTRAINT uq_ofac_sls_official_snapshot_ref UNIQUE
                (snapshot_id, snapshot_content_hash),
            CONSTRAINT uq_ofac_sls_official_snapshot_kind UNIQUE
                (snapshot_id, snapshot_content_hash, list_kind),
            CONSTRAINT uq_ofac_sls_official_snapshot_hash UNIQUE
                (snapshot_content_hash),
            CONSTRAINT ck_ofac_sls_official_snapshot_ids CHECK (
                list_kind IN ('SDN', 'CONSOLIDATED') AND
                snapshot_id = 'ofac-sls-' || lower(list_kind) || '-' ||
                    substr(snapshot_content_hash, 8) AND
                snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                raw_content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_ofac_sls_official_snapshot_counts CHECK (
                raw_byte_length BETWEEN 1 AND 67108864 AND
                declared_record_count BETWEEN 1 AND 50000 AND
                entry_count = declared_record_count AND
                program_count BETWEEN entry_count AND 6400000 AND
                alias_count BETWEEN 0 AND 25600000 AND
                address_count BETWEEN 0 AND 25600000 AND
                identifier_count BETWEEN 0 AND 25600000 AND
                fact_count BETWEEN 0 AND 12800000 AND
                vessel_count BETWEEN 0 AND entry_count
            ),
            CONSTRAINT ck_ofac_sls_official_snapshot_time CHECK (
                isfinite(retrieved_at) AND
                (source_last_modified IS NULL OR
                 isfinite(source_last_modified)) AND
                publish_date <= retrieved_at::DATE
            ),
            CONSTRAINT fk_ofac_sls_official_snapshot_raw FOREIGN KEY
                (raw_content_hash)
                REFERENCES official_source_raw_object (content_hash)
        );

        CREATE TABLE ofac_sls_official_entry (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            first_name TEXT,
            last_name TEXT NOT NULL,
            whole_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            title TEXT,
            subject_type VARCHAR(16) NOT NULL,
            remarks TEXT,
            entry_content_hash VARCHAR(71) NOT NULL,
            CONSTRAINT pk_ofac_sls_official_entry PRIMARY KEY
                (snapshot_id, entry_uid),
            CONSTRAINT uq_ofac_sls_official_entry_child_ref UNIQUE
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid),
            CONSTRAINT ck_ofac_sls_official_entry_ids CHECK (
                entry_uid ~ '^[1-9][0-9]{0,18}$' AND
                entry_content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_ofac_sls_official_entry_shape CHECK (
                octet_length(last_name) BETWEEN 1 AND 4096 AND
                octet_length(whole_name) BETWEEN 1 AND 4096 AND
                octet_length(normalized_name) BETWEEN 1 AND 4096 AND
                (first_name IS NULL OR octet_length(first_name) BETWEEN 1 AND 4096) AND
                (title IS NULL OR octet_length(title) BETWEEN 1 AND 4096) AND
                (remarks IS NULL OR octet_length(remarks) BETWEEN 1 AND 4096) AND
                subject_type IN ('Aircraft', 'Entity', 'Individual', 'Vessel')
            ),
            CONSTRAINT fk_ofac_sls_official_entry_snapshot FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind)
                REFERENCES ofac_sls_official_snapshot
                (snapshot_id, snapshot_content_hash, list_kind)
        );

        CREATE TABLE ofac_sls_official_program (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            program_sequence INTEGER NOT NULL,
            program TEXT NOT NULL,
            CONSTRAINT pk_ofac_sls_official_program PRIMARY KEY
                (snapshot_id, entry_uid, program_sequence),
            CONSTRAINT uq_ofac_sls_official_program_value UNIQUE
                (snapshot_id, entry_uid, program),
            CONSTRAINT ck_ofac_sls_official_program_shape CHECK (
                program_sequence BETWEEN 1 AND 128 AND
                octet_length(program) BETWEEN 1 AND 256
            ),
            CONSTRAINT fk_ofac_sls_official_program_entry FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
                REFERENCES ofac_sls_official_entry
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
        );

        CREATE TABLE ofac_sls_official_alias (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            alias_uid VARCHAR(19) NOT NULL,
            alias_type VARCHAR(64) NOT NULL,
            category VARCHAR(8) NOT NULL,
            whole_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            native_locator VARCHAR(512) NOT NULL,
            CONSTRAINT pk_ofac_sls_official_alias PRIMARY KEY
                (snapshot_id, entry_uid, alias_uid),
            CONSTRAINT ck_ofac_sls_official_alias_ids CHECK (
                alias_uid ~ '^[1-9][0-9]{0,18}$'
            ),
            CONSTRAINT ck_ofac_sls_official_alias_shape CHECK (
                octet_length(alias_type) BETWEEN 1 AND 64 AND
                category IN ('strong', 'weak') AND
                octet_length(whole_name) BETWEEN 1 AND 4096 AND
                octet_length(normalized_name) BETWEEN 1 AND 4096 AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_ofac_sls_official_alias_entry FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
                REFERENCES ofac_sls_official_entry
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
        );

        CREATE TABLE ofac_sls_official_address (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            address_uid VARCHAR(19) NOT NULL,
            address1 TEXT,
            address2 TEXT,
            address3 TEXT,
            city TEXT,
            state_or_province TEXT,
            postal_code TEXT,
            country TEXT,
            region TEXT,
            native_locator VARCHAR(512) NOT NULL,
            CONSTRAINT pk_ofac_sls_official_address PRIMARY KEY
                (snapshot_id, entry_uid, address_uid),
            CONSTRAINT ck_ofac_sls_official_address_ids CHECK (
                address_uid ~ '^[1-9][0-9]{0,18}$'
            ),
            CONSTRAINT ck_ofac_sls_official_address_shape CHECK (
                (address1 IS NULL OR octet_length(address1) BETWEEN 1 AND 4096) AND
                (address2 IS NULL OR octet_length(address2) BETWEEN 1 AND 4096) AND
                (address3 IS NULL OR octet_length(address3) BETWEEN 1 AND 4096) AND
                (city IS NULL OR octet_length(city) BETWEEN 1 AND 4096) AND
                (state_or_province IS NULL OR octet_length(state_or_province) BETWEEN 1 AND 4096) AND
                (postal_code IS NULL OR octet_length(postal_code) BETWEEN 1 AND 4096) AND
                (country IS NULL OR octet_length(country) BETWEEN 1 AND 4096) AND
                (region IS NULL OR octet_length(region) BETWEEN 1 AND 4096) AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_ofac_sls_official_address_entry FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
                REFERENCES ofac_sls_official_entry
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
        );

        CREATE TABLE ofac_sls_official_identifier (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            identifier_uid VARCHAR(19) NOT NULL,
            type_code VARCHAR(256) NOT NULL,
            number TEXT NOT NULL,
            normalized_number VARCHAR(512),
            country TEXT,
            issue_date TEXT,
            expiration_date TEXT,
            native_locator VARCHAR(512) NOT NULL,
            CONSTRAINT pk_ofac_sls_official_identifier PRIMARY KEY
                (snapshot_id, entry_uid, identifier_uid),
            CONSTRAINT ck_ofac_sls_official_identifier_ids CHECK (
                identifier_uid ~ '^[1-9][0-9]{0,18}$'
            ),
            CONSTRAINT ck_ofac_sls_official_identifier_shape CHECK (
                octet_length(type_code) BETWEEN 1 AND 256 AND
                octet_length(number) BETWEEN 1 AND 4096 AND
                (normalized_number IS NULL OR octet_length(normalized_number) BETWEEN 1 AND 512) AND
                (country IS NULL OR octet_length(country) BETWEEN 1 AND 4096) AND
                (issue_date IS NULL OR octet_length(issue_date) BETWEEN 1 AND 4096) AND
                (expiration_date IS NULL OR octet_length(expiration_date) BETWEEN 1 AND 4096) AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_ofac_sls_official_identifier_entry FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
                REFERENCES ofac_sls_official_entry
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
        );

        CREATE TABLE ofac_sls_official_fact (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            fact_uid VARCHAR(19) NOT NULL,
            fact_kind VARCHAR(32) NOT NULL,
            fact_value TEXT NOT NULL,
            main_entry BOOLEAN NOT NULL,
            native_locator VARCHAR(512) NOT NULL,
            CONSTRAINT pk_ofac_sls_official_fact PRIMARY KEY
                (snapshot_id, entry_uid, fact_uid),
            CONSTRAINT ck_ofac_sls_official_fact_ids CHECK (
                fact_uid ~ '^[1-9][0-9]{0,18}$'
            ),
            CONSTRAINT ck_ofac_sls_official_fact_shape CHECK (
                fact_kind IN ('nationality', 'citizenship', 'date_of_birth', 'place_of_birth') AND
                octet_length(fact_value) BETWEEN 1 AND 4096 AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_ofac_sls_official_fact_entry FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
                REFERENCES ofac_sls_official_entry
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
        );

        CREATE TABLE ofac_sls_official_vessel (
            snapshot_id VARCHAR(96) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            list_kind VARCHAR(16) NOT NULL,
            entry_uid VARCHAR(19) NOT NULL,
            call_sign TEXT,
            vessel_type TEXT,
            vessel_flag TEXT,
            vessel_owner TEXT,
            tonnage INTEGER,
            gross_registered_tonnage INTEGER,
            native_locator VARCHAR(512) NOT NULL,
            CONSTRAINT pk_ofac_sls_official_vessel PRIMARY KEY
                (snapshot_id, entry_uid),
            CONSTRAINT ck_ofac_sls_official_vessel_shape CHECK (
                (call_sign IS NULL OR octet_length(call_sign) BETWEEN 1 AND 4096) AND
                (vessel_type IS NULL OR octet_length(vessel_type) BETWEEN 1 AND 4096) AND
                (vessel_flag IS NULL OR octet_length(vessel_flag) BETWEEN 1 AND 4096) AND
                (vessel_owner IS NULL OR octet_length(vessel_owner) BETWEEN 1 AND 4096) AND
                (tonnage IS NULL OR tonnage >= 0) AND
                (gross_registered_tonnage IS NULL OR gross_registered_tonnage >= 0) AND
                octet_length(native_locator) BETWEEN 1 AND 512
            ),
            CONSTRAINT fk_ofac_sls_official_vessel_entry FOREIGN KEY
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
                REFERENCES ofac_sls_official_entry
                (snapshot_id, snapshot_content_hash, list_kind, entry_uid)
        );

        ALTER TABLE official_screening_source_bundle
            ADD COLUMN ofac_sdn_snapshot_id VARCHAR(96),
            ADD COLUMN ofac_sdn_snapshot_content_hash VARCHAR(71),
            ADD COLUMN ofac_consolidated_snapshot_id VARCHAR(96),
            ADD COLUMN ofac_consolidated_snapshot_content_hash VARCHAR(71),
            ADD CONSTRAINT ck_official_screening_source_bundle_ofac_complete CHECK (
                (ofac_sdn_snapshot_id IS NULL AND
                 ofac_sdn_snapshot_content_hash IS NULL AND
                 ofac_consolidated_snapshot_id IS NULL AND
                 ofac_consolidated_snapshot_content_hash IS NULL) OR
                (ofac_sdn_snapshot_id IS NOT NULL AND
                 ofac_sdn_snapshot_content_hash IS NOT NULL AND
                 ofac_consolidated_snapshot_id IS NOT NULL AND
                 ofac_consolidated_snapshot_content_hash IS NOT NULL)
            ),
            ADD CONSTRAINT fk_official_screening_source_bundle_ofac_sdn FOREIGN KEY
                (ofac_sdn_snapshot_id, ofac_sdn_snapshot_content_hash)
                REFERENCES ofac_sls_official_snapshot
                (snapshot_id, snapshot_content_hash),
            ADD CONSTRAINT fk_official_screening_source_bundle_ofac_consolidated FOREIGN KEY
                (ofac_consolidated_snapshot_id,
                 ofac_consolidated_snapshot_content_hash)
                REFERENCES ofac_sls_official_snapshot
                (snapshot_id, snapshot_content_hash);

        ALTER TABLE official_screening_source_state
            DROP CONSTRAINT ck_official_screening_source_state_time,
            ADD COLUMN ofac_sdn_observed_at TIMESTAMPTZ,
            ADD COLUMN ofac_consolidated_observed_at TIMESTAMPTZ,
            ADD CONSTRAINT ck_official_screening_source_state_time CHECK (
                isfinite(fsf_observed_at) AND
                isfinite(dual_use_observed_at) AND
                isfinite(updated_at) AND
                fsf_observed_at <= updated_at AND
                dual_use_observed_at <= updated_at AND
                ((ofac_sdn_observed_at IS NULL AND
                  ofac_consolidated_observed_at IS NULL) OR
                 (isfinite(ofac_sdn_observed_at) AND
                  isfinite(ofac_consolidated_observed_at) AND
                  ofac_sdn_observed_at <= updated_at AND
                  ofac_consolidated_observed_at <= updated_at))
            );

        CREATE FUNCTION tradesieve_validate_ofac_projection_counts()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            actual_entries BIGINT;
            actual_programs BIGINT;
            actual_aliases BIGINT;
            actual_addresses BIGINT;
            actual_identifiers BIGINT;
            actual_facts BIGINT;
            actual_vessels BIGINT;
        BEGIN
            SELECT count(*) INTO actual_entries FROM ofac_sls_official_entry
                WHERE snapshot_id = NEW.snapshot_id;
            SELECT count(*) INTO actual_programs FROM ofac_sls_official_program
                WHERE snapshot_id = NEW.snapshot_id;
            SELECT count(*) INTO actual_aliases FROM ofac_sls_official_alias
                WHERE snapshot_id = NEW.snapshot_id;
            SELECT count(*) INTO actual_addresses FROM ofac_sls_official_address
                WHERE snapshot_id = NEW.snapshot_id;
            SELECT count(*) INTO actual_identifiers FROM ofac_sls_official_identifier
                WHERE snapshot_id = NEW.snapshot_id;
            SELECT count(*) INTO actual_facts FROM ofac_sls_official_fact
                WHERE snapshot_id = NEW.snapshot_id;
            SELECT count(*) INTO actual_vessels FROM ofac_sls_official_vessel
                WHERE snapshot_id = NEW.snapshot_id;
            IF actual_entries <> NEW.entry_count OR
               actual_programs <> NEW.program_count OR
               actual_aliases <> NEW.alias_count OR
               actual_addresses <> NEW.address_count OR
               actual_identifiers <> NEW.identifier_count OR
               actual_facts <> NEW.fact_count OR
               actual_vessels <> NEW.vessel_count THEN
                RAISE EXCEPTION 'OFAC projection count mismatch'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION tradesieve_reject_activated_ofac_projection_insert()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM inserted_rows AS inserted
                JOIN official_screening_source_bundle AS bundle
                  ON bundle.ofac_sdn_snapshot_id = inserted.snapshot_id OR
                     bundle.ofac_consolidated_snapshot_id = inserted.snapshot_id
            ) THEN
                RAISE EXCEPTION 'activated OFAC projection is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION tradesieve_require_complete_ofac_active_state()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.ofac_sdn_observed_at IS NULL OR
               NEW.ofac_consolidated_observed_at IS NULL OR NOT EXISTS (
                SELECT 1 FROM official_screening_source_bundle AS bundle
                JOIN ofac_sls_official_snapshot AS sdn
                  ON sdn.snapshot_id = bundle.ofac_sdn_snapshot_id AND
                     sdn.snapshot_content_hash =
                         bundle.ofac_sdn_snapshot_content_hash AND
                     sdn.list_kind = 'SDN'
                JOIN ofac_sls_official_snapshot AS consolidated
                  ON consolidated.snapshot_id =
                         bundle.ofac_consolidated_snapshot_id AND
                     consolidated.snapshot_content_hash =
                         bundle.ofac_consolidated_snapshot_content_hash AND
                     consolidated.list_kind = 'CONSOLIDATED'
                WHERE bundle.bundle_id = NEW.active_bundle_id AND
                      bundle.bundle_content_hash = NEW.active_bundle_content_hash AND
                      bundle.ofac_sdn_snapshot_id IS NOT NULL AND
                      bundle.ofac_consolidated_snapshot_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'active official source set requires OFAC'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE INDEX ix_ofac_sls_official_alias_lookup
            ON ofac_sls_official_alias (snapshot_id, normalized_name);
        CREATE INDEX ix_ofac_sls_official_identifier_lookup
            ON ofac_sls_official_identifier
            (snapshot_id, type_code, normalized_number);

        CREATE TRIGGER trg_ofac_snapshot_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_snapshot
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_entry_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_entry
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_program_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_program
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_alias_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_alias
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_address_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_address
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_identifier_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_identifier
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_fact_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_fact
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_ofac_vessel_immutable
            BEFORE UPDATE OR DELETE ON ofac_sls_official_vessel
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();

        CREATE TRIGGER trg_ofac_entry_activated_insert
            AFTER INSERT ON ofac_sls_official_entry
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE TRIGGER trg_ofac_program_activated_insert
            AFTER INSERT ON ofac_sls_official_program
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE TRIGGER trg_ofac_alias_activated_insert
            AFTER INSERT ON ofac_sls_official_alias
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE TRIGGER trg_ofac_address_activated_insert
            AFTER INSERT ON ofac_sls_official_address
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE TRIGGER trg_ofac_identifier_activated_insert
            AFTER INSERT ON ofac_sls_official_identifier
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE TRIGGER trg_ofac_fact_activated_insert
            AFTER INSERT ON ofac_sls_official_fact
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE TRIGGER trg_ofac_vessel_activated_insert
            AFTER INSERT ON ofac_sls_official_vessel
            REFERENCING NEW TABLE AS inserted_rows FOR EACH STATEMENT
            EXECUTE FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        CREATE CONSTRAINT TRIGGER trg_ofac_projection_counts
            AFTER INSERT ON ofac_sls_official_snapshot
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_ofac_projection_counts();
        CREATE TRIGGER trg_official_state_complete_ofac
            BEFORE INSERT OR UPDATE ON official_screening_source_state
            FOR EACH ROW EXECUTE FUNCTION tradesieve_require_complete_ofac_active_state();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_official_state_complete_ofac
            ON official_screening_source_state;
        ALTER TABLE official_screening_source_state
            DROP CONSTRAINT ck_official_screening_source_state_time,
            DROP COLUMN ofac_consolidated_observed_at,
            DROP COLUMN ofac_sdn_observed_at,
            ADD CONSTRAINT ck_official_screening_source_state_time CHECK (
                isfinite(fsf_observed_at) AND
                isfinite(dual_use_observed_at) AND
                isfinite(updated_at) AND
                fsf_observed_at <= updated_at AND
                dual_use_observed_at <= updated_at
            );
        ALTER TABLE official_screening_source_bundle
            DROP CONSTRAINT fk_official_screening_source_bundle_ofac_consolidated,
            DROP CONSTRAINT fk_official_screening_source_bundle_ofac_sdn,
            DROP CONSTRAINT ck_official_screening_source_bundle_ofac_complete,
            DROP COLUMN ofac_consolidated_snapshot_content_hash,
            DROP COLUMN ofac_consolidated_snapshot_id,
            DROP COLUMN ofac_sdn_snapshot_content_hash,
            DROP COLUMN ofac_sdn_snapshot_id;
        DROP TABLE ofac_sls_official_vessel;
        DROP TABLE ofac_sls_official_fact;
        DROP TABLE ofac_sls_official_identifier;
        DROP TABLE ofac_sls_official_address;
        DROP TABLE ofac_sls_official_alias;
        DROP TABLE ofac_sls_official_program;
        DROP TABLE ofac_sls_official_entry;
        DROP TABLE ofac_sls_official_snapshot;
        DROP FUNCTION tradesieve_require_complete_ofac_active_state();
        DROP FUNCTION tradesieve_reject_activated_ofac_projection_insert();
        DROP FUNCTION tradesieve_validate_ofac_projection_counts();
        """
    )
