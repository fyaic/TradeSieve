"""Create immutable source-snapshot artifacts, lifecycle, and command audit."""

from __future__ import annotations

from alembic import op

revision = "20260806_0004"
down_revision = "20260806_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION tradesieve_valid_source_snapshot_payload(value BYTEA)
        RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
        DECLARE
            document JSONB;
            document_text TEXT;
        BEGIN
            IF octet_length(value) = 0 OR
               octet_length(value) > 4194304 THEN
                RETURN FALSE;
            END IF;
            BEGIN
                document_text := convert_from(value, 'UTF8');
                IF NOT (document_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
                    RETURN FALSE;
                END IF;
                document := document_text::jsonb;
            EXCEPTION WHEN OTHERS THEN
                RETURN FALSE;
            END;
            RETURN jsonb_typeof(document) = 'object' AND
                (SELECT count(*) FROM jsonb_object_keys(document)) = 12 AND
                document ?& ARRAY[
                    'schema_version', 'deployment_id', 'source_id',
                    'snapshot_id', 'content_hash', 'raw_object', 'parser_id',
                    'parser_version', 'schema_id', 'declared_record_count',
                    'parsed_at', 'records'
                ] AND
                jsonb_typeof(document -> 'schema_version') = 'string' AND
                document ->> 'schema_version' = '1.0.0' AND
                jsonb_typeof(document -> 'deployment_id') = 'string' AND
                jsonb_typeof(document -> 'source_id') = 'string' AND
                jsonb_typeof(document -> 'snapshot_id') = 'string' AND
                jsonb_typeof(document -> 'content_hash') = 'string' AND
                jsonb_typeof(document -> 'raw_object') = 'object' AND
                jsonb_typeof(document -> 'parser_id') = 'string' AND
                jsonb_typeof(document -> 'parser_version') = 'string' AND
                jsonb_typeof(document -> 'schema_id') = 'string' AND
                jsonb_typeof(document -> 'declared_record_count') = 'number' AND
                document ->> 'declared_record_count' ~ '^(0|[1-9][0-9]*)$' AND
                (document ->> 'declared_record_count')::numeric <= 512 AND
                jsonb_typeof(document -> 'parsed_at') = 'string' AND
                jsonb_typeof(document -> 'records') = 'array';
        END
        $$;

        CREATE FUNCTION tradesieve_valid_source_validation_payload(value BYTEA)
        RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
        DECLARE
            document JSONB;
            document_text TEXT;
        BEGIN
            IF octet_length(value) = 0 OR
               octet_length(value) > 2097152 THEN
                RETURN FALSE;
            END IF;
            BEGIN
                document_text := convert_from(value, 'UTF8');
                IF NOT (document_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
                    RETURN FALSE;
                END IF;
                document := document_text::jsonb;
            EXCEPTION WHEN OTHERS THEN
                RETURN FALSE;
            END;
            RETURN jsonb_typeof(document) = 'object' AND
                (SELECT count(*) FROM jsonb_object_keys(document)) = 10 AND
                document ?& ARRAY[
                    'schema_version', 'deployment_id', 'source_id', 'snapshot',
                    'expected_schema_id', 'passed', 'reasons', 'diff',
                    'validated_at', 'content_hash'
                ] AND
                jsonb_typeof(document -> 'schema_version') = 'string' AND
                document ->> 'schema_version' = '1.0.0' AND
                jsonb_typeof(document -> 'deployment_id') = 'string' AND
                jsonb_typeof(document -> 'source_id') = 'string' AND
                jsonb_typeof(document -> 'snapshot') = 'object' AND
                jsonb_typeof(document -> 'expected_schema_id') = 'string' AND
                jsonb_typeof(document -> 'passed') = 'boolean' AND
                jsonb_typeof(document -> 'reasons') = 'array' AND
                jsonb_typeof(document -> 'diff') = 'object' AND
                jsonb_typeof(document -> 'validated_at') = 'string' AND
                jsonb_typeof(document -> 'content_hash') = 'string';
        END
        $$;

        CREATE TABLE source_raw_object_metadata (
            deployment_id VARCHAR(128) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            object_id VARCHAR(128) NOT NULL,
            original_name TEXT NOT NULL,
            media_type VARCHAR(129) NOT NULL,
            charset VARCHAR(32) NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL,
            effective_from TIMESTAMPTZ,
            byte_length INTEGER NOT NULL,
            content_hash VARCHAR(71) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_source_raw_object_metadata PRIMARY KEY
                (deployment_id, source_id, object_id),
            CONSTRAINT uq_source_raw_object_full_ref UNIQUE
                (deployment_id, source_id, object_id, content_hash, byte_length),
            CONSTRAINT ck_source_raw_object_ids CHECK (
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                object_id ~ '^raw-[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_source_raw_object_name CHECK (
                length(original_name) BETWEEN 1 AND 256 AND
                original_name = btrim(original_name) AND
                original_name !~ '[[:cntrl:]]' AND
                original_name NOT IN ('.', '..') AND
                position('/' IN original_name) = 0 AND
                position(E'\\' IN original_name) = 0
            ),
            CONSTRAINT ck_source_raw_object_media CHECK (
                media_type ~
                    '^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$' AND
                charset ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$'
            ),
            CONSTRAINT ck_source_raw_object_time CHECK (
                isfinite(retrieved_at) AND
                (effective_from IS NULL OR isfinite(effective_from))
            ),
            CONSTRAINT ck_source_raw_object_size_hash CHECK (
                byte_length BETWEEN 0 AND 1048576 AND
                content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT fk_source_raw_object_registry FOREIGN KEY
                (deployment_id, source_id)
                REFERENCES source_registry(deployment_id, source_id)
        );

        CREATE TABLE source_parsed_snapshot (
            deployment_id VARCHAR(128) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            snapshot_id VARCHAR(128) NOT NULL,
            content_hash VARCHAR(71) NOT NULL,
            raw_object_id VARCHAR(128) NOT NULL,
            raw_content_hash VARCHAR(71) NOT NULL,
            raw_byte_length INTEGER NOT NULL,
            parser_id VARCHAR(128) NOT NULL,
            parser_version VARCHAR(32) NOT NULL,
            schema_id VARCHAR(128) NOT NULL,
            declared_record_count INTEGER NOT NULL,
            parsed_at TIMESTAMPTZ NOT NULL,
            private_payload BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_source_parsed_snapshot PRIMARY KEY
                (deployment_id, source_id, snapshot_id),
            CONSTRAINT uq_source_parsed_snapshot_full_ref UNIQUE
                (deployment_id, source_id, snapshot_id, content_hash),
            CONSTRAINT uq_source_parsed_snapshot_event_ref UNIQUE
                (deployment_id, source_id, snapshot_id, content_hash,
                 raw_object_id, raw_content_hash, raw_byte_length),
            CONSTRAINT ck_source_parsed_snapshot_ids CHECK (
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                snapshot_id ~ '^snapshot-[a-f0-9]{64}$' AND
                raw_object_id ~ '^raw-[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_source_parsed_snapshot_hashes CHECK (
                content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                snapshot_id = 'snapshot-' || substr(content_hash, 8) AND
                raw_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                raw_byte_length BETWEEN 0 AND 1048576
            ),
            CONSTRAINT ck_source_parsed_snapshot_binding CHECK (
                parser_id = 'synthetic-json-v1' AND
                parser_version = '1.0.0' AND
                schema_id = 'tradesieve-synthetic-source-v1' AND
                declared_record_count BETWEEN 0 AND 512
            ),
            CONSTRAINT ck_source_parsed_snapshot_time CHECK (
                isfinite(parsed_at)
            ),
            CONSTRAINT ck_source_parsed_snapshot_payload CHECK (
                octet_length(private_payload) BETWEEN 1 AND 4194304 AND
                tradesieve_valid_source_snapshot_payload(private_payload)
            ),
            CONSTRAINT fk_source_parsed_snapshot_registry FOREIGN KEY
                (deployment_id, source_id)
                REFERENCES source_registry(deployment_id, source_id),
            CONSTRAINT fk_source_parsed_snapshot_raw FOREIGN KEY
                (deployment_id, source_id, raw_object_id, raw_content_hash,
                 raw_byte_length)
                REFERENCES source_raw_object_metadata
                (deployment_id, source_id, object_id, content_hash, byte_length)
        );

        CREATE FUNCTION tradesieve_validate_source_parsed_snapshot_insert()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            raw_retrieved_at TIMESTAMPTZ;
        BEGIN
            SELECT retrieved_at INTO raw_retrieved_at
            FROM source_raw_object_metadata
            WHERE deployment_id = NEW.deployment_id
              AND source_id = NEW.source_id
              AND object_id = NEW.raw_object_id
              AND content_hash = NEW.raw_content_hash
              AND byte_length = NEW.raw_byte_length;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'parsed snapshot raw object does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF NEW.parsed_at < raw_retrieved_at THEN
                RAISE EXCEPTION 'parsed snapshot precedes raw retrieval'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_source_parsed_snapshot_insert
            BEFORE INSERT ON source_parsed_snapshot
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_source_parsed_snapshot_insert();

        CREATE TABLE source_snapshot_validation_evidence (
            deployment_id VARCHAR(128) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            snapshot_id VARCHAR(128) NOT NULL,
            snapshot_content_hash VARCHAR(71) NOT NULL,
            previous_deployment_id VARCHAR(128),
            previous_source_id VARCHAR(128),
            previous_snapshot_id VARCHAR(128),
            previous_snapshot_content_hash VARCHAR(71),
            expected_schema_id VARCHAR(128) NOT NULL,
            passed BOOLEAN NOT NULL,
            validated_at TIMESTAMPTZ NOT NULL,
            report_content_hash VARCHAR(71) NOT NULL,
            diff_content_hash VARCHAR(71) NOT NULL,
            private_payload BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_source_snapshot_validation PRIMARY KEY
                (deployment_id, source_id, snapshot_id),
            CONSTRAINT uq_source_snapshot_validation_full_ref UNIQUE
                (deployment_id, source_id, snapshot_id, snapshot_content_hash,
                 report_content_hash, diff_content_hash, passed),
            CONSTRAINT ck_source_snapshot_validation_ids CHECK (
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                snapshot_id ~ '^snapshot-[a-f0-9]{64}$' AND
                (previous_snapshot_id IS NULL OR
                 previous_snapshot_id ~ '^snapshot-[a-f0-9]{64}$')
            ),
            CONSTRAINT ck_source_snapshot_validation_hashes CHECK (
                snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                snapshot_id =
                    'snapshot-' || substr(snapshot_content_hash, 8) AND
                report_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                diff_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                (previous_snapshot_content_hash IS NULL OR
                 previous_snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$')
            ),
            CONSTRAINT ck_source_snapshot_validation_previous CHECK (
                num_nonnulls(
                    previous_deployment_id, previous_source_id,
                    previous_snapshot_id, previous_snapshot_content_hash
                ) IN (0, 4) AND
                (previous_deployment_id IS NULL OR (
                    previous_deployment_id = deployment_id AND
                    previous_source_id = source_id
                ))
            ),
            CONSTRAINT ck_source_snapshot_validation_schema_time CHECK (
                expected_schema_id = 'tradesieve-synthetic-source-v1' AND
                isfinite(validated_at)
            ),
            CONSTRAINT ck_source_snapshot_validation_payload CHECK (
                octet_length(private_payload) BETWEEN 1 AND 2097152 AND
                tradesieve_valid_source_validation_payload(private_payload)
            ),
            CONSTRAINT fk_source_snapshot_validation_current FOREIGN KEY
                (deployment_id, source_id, snapshot_id, snapshot_content_hash)
                REFERENCES source_parsed_snapshot
                (deployment_id, source_id, snapshot_id, content_hash),
            CONSTRAINT fk_source_snapshot_validation_previous FOREIGN KEY
                (previous_deployment_id, previous_source_id,
                 previous_snapshot_id, previous_snapshot_content_hash)
                REFERENCES source_parsed_snapshot
                (deployment_id, source_id, snapshot_id, content_hash)
                MATCH FULL
        );

        CREATE FUNCTION tradesieve_validate_source_validation_insert()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            snapshot_parsed_at TIMESTAMPTZ;
        BEGIN
            SELECT parsed_at INTO snapshot_parsed_at
            FROM source_parsed_snapshot
            WHERE deployment_id = NEW.deployment_id
              AND source_id = NEW.source_id
              AND snapshot_id = NEW.snapshot_id
              AND content_hash = NEW.snapshot_content_hash;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'validation snapshot does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF NEW.validated_at < snapshot_parsed_at THEN
                RAISE EXCEPTION 'validation precedes snapshot parsing'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_source_snapshot_validation_insert
            BEFORE INSERT ON source_snapshot_validation_evidence
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_source_validation_insert();

        CREATE TABLE source_snapshot_lifecycle_state (
            deployment_id VARCHAR(128) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            last_sequence BIGINT NOT NULL DEFAULT 0,
            active_snapshot_id VARCHAR(128),
            active_snapshot_content_hash VARCHAR(71),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_source_snapshot_lifecycle_state PRIMARY KEY
                (deployment_id, source_id),
            CONSTRAINT ck_source_snapshot_state_ids CHECK (
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_source_snapshot_state_sequence CHECK (
                last_sequence >= 0
            ),
            CONSTRAINT ck_source_snapshot_state_active CHECK (
                num_nonnulls(
                    active_snapshot_id, active_snapshot_content_hash
                ) IN (0, 2) AND
                (active_snapshot_id IS NULL OR (
                    active_snapshot_id ~ '^snapshot-[a-f0-9]{64}$' AND
                    active_snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                    active_snapshot_id = 'snapshot-' ||
                        substr(active_snapshot_content_hash, 8)
                ))
            ),
            CONSTRAINT ck_source_snapshot_state_time CHECK (
                isfinite(updated_at)
            ),
            CONSTRAINT fk_source_snapshot_state_registry FOREIGN KEY
                (deployment_id, source_id)
                REFERENCES source_registry(deployment_id, source_id),
            CONSTRAINT fk_source_snapshot_state_active FOREIGN KEY
                (deployment_id, source_id, active_snapshot_id,
                 active_snapshot_content_hash)
                REFERENCES source_parsed_snapshot
                (deployment_id, source_id, snapshot_id, content_hash)
        );

        CREATE TABLE source_snapshot_lifecycle_event (
            deployment_id VARCHAR(128) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            sequence BIGINT NOT NULL,
            event_id VARCHAR(128) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            raw_object_id VARCHAR(128) NOT NULL,
            raw_content_hash VARCHAR(71) NOT NULL,
            raw_byte_length INTEGER NOT NULL,
            snapshot_id VARCHAR(128),
            snapshot_content_hash VARCHAR(71),
            previous_active_snapshot_id VARCHAR(128),
            previous_active_snapshot_content_hash VARCHAR(71),
            validation_report_hash VARCHAR(71),
            validation_diff_hash VARCHAR(71),
            validation_passed BOOLEAN,
            actor_id VARCHAR(128) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            reason TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_source_snapshot_lifecycle_event PRIMARY KEY
                (deployment_id, source_id, sequence),
            CONSTRAINT uq_source_snapshot_lifecycle_event_id UNIQUE (event_id),
            CONSTRAINT ck_source_snapshot_event_ids CHECK (
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                raw_object_id ~ '^raw-[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_source_snapshot_event_sequence CHECK (
                sequence > 0
            ),
            CONSTRAINT ck_source_snapshot_event_type CHECK (
                event_type IN (
                    'RETRIEVED', 'QUARANTINED', 'PARSED',
                    'VALIDATION_FAILED', 'VALIDATED', 'APPROVED',
                    'ACTIVATED', 'ROLLED_BACK'
                )
            ),
            CONSTRAINT ck_source_snapshot_event_raw_ref CHECK (
                raw_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                raw_byte_length BETWEEN 0 AND 1048576
            ),
            CONSTRAINT ck_source_snapshot_event_snapshot_ref CHECK (
                num_nonnulls(snapshot_id, snapshot_content_hash) IN (0, 2) AND
                (snapshot_id IS NULL OR (
                    snapshot_id ~ '^snapshot-[a-f0-9]{64}$' AND
                    snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                    snapshot_id = 'snapshot-' ||
                        substr(snapshot_content_hash, 8)
                ))
            ),
            CONSTRAINT ck_source_snapshot_event_previous_ref CHECK (
                num_nonnulls(
                    previous_active_snapshot_id,
                    previous_active_snapshot_content_hash
                ) IN (0, 2) AND
                (previous_active_snapshot_id IS NULL OR (
                    previous_active_snapshot_id ~
                        '^snapshot-[a-f0-9]{64}$' AND
                    previous_active_snapshot_content_hash ~
                        '^sha256:[a-f0-9]{64}$' AND
                    previous_active_snapshot_id = 'snapshot-' ||
                        substr(previous_active_snapshot_content_hash, 8)
                ))
            ),
            CONSTRAINT ck_source_snapshot_event_actor CHECK (
                actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                actor_type IN ('HUMAN', 'SERVICE') AND
                (event_type NOT IN ('APPROVED', 'ACTIVATED', 'ROLLED_BACK') OR
                 actor_type = 'HUMAN')
            ),
            CONSTRAINT ck_source_snapshot_event_reason_time CHECK (
                length(reason) BETWEEN 1 AND 2000 AND
                reason = btrim(reason) AND
                reason !~ '[[:cntrl:]]' AND
                isfinite(occurred_at)
            ),
            CONSTRAINT ck_source_snapshot_event_snapshot_shape CHECK (
                (event_type IN ('RETRIEVED', 'QUARANTINED') AND
                 snapshot_id IS NULL) OR
                (event_type NOT IN ('RETRIEVED', 'QUARANTINED') AND
                 snapshot_id IS NOT NULL)
            ),
            CONSTRAINT ck_source_snapshot_event_previous_shape CHECK (
                (event_type = 'ROLLED_BACK' AND
                 previous_active_snapshot_id IS NOT NULL) OR
                (event_type = 'ACTIVATED') OR
                (event_type NOT IN ('ACTIVATED', 'ROLLED_BACK') AND
                 previous_active_snapshot_id IS NULL)
            ),
            CONSTRAINT ck_source_snapshot_event_validation_shape CHECK (
                (event_type = 'VALIDATED' AND validation_passed IS TRUE AND
                 validation_report_hash IS NOT NULL AND
                 validation_diff_hash IS NOT NULL) OR
                (event_type = 'VALIDATION_FAILED' AND
                 validation_passed IS FALSE AND
                 validation_report_hash IS NOT NULL AND
                 validation_diff_hash IS NOT NULL) OR
                (event_type NOT IN ('VALIDATED', 'VALIDATION_FAILED') AND
                 validation_passed IS NULL AND
                 validation_report_hash IS NULL AND
                 validation_diff_hash IS NULL)
            ),
            CONSTRAINT ck_source_snapshot_event_validation_hashes CHECK (
                validation_report_hash IS NULL OR (
                    validation_report_hash ~ '^sha256:[a-f0-9]{64}$' AND
                    validation_diff_hash ~ '^sha256:[a-f0-9]{64}$'
                )
            ),
            CONSTRAINT fk_source_snapshot_event_registry FOREIGN KEY
                (deployment_id, source_id)
                REFERENCES source_registry(deployment_id, source_id),
            CONSTRAINT fk_source_snapshot_event_raw FOREIGN KEY
                (deployment_id, source_id, raw_object_id, raw_content_hash,
                 raw_byte_length)
                REFERENCES source_raw_object_metadata
                (deployment_id, source_id, object_id, content_hash, byte_length),
            CONSTRAINT fk_source_snapshot_event_snapshot_raw FOREIGN KEY
                (deployment_id, source_id, snapshot_id, snapshot_content_hash,
                 raw_object_id, raw_content_hash, raw_byte_length)
                REFERENCES source_parsed_snapshot
                (deployment_id, source_id, snapshot_id, content_hash,
                 raw_object_id, raw_content_hash, raw_byte_length),
            CONSTRAINT fk_source_snapshot_event_previous FOREIGN KEY
                (deployment_id, source_id, previous_active_snapshot_id,
                 previous_active_snapshot_content_hash)
                REFERENCES source_parsed_snapshot
                (deployment_id, source_id, snapshot_id, content_hash),
            CONSTRAINT fk_source_snapshot_event_validation FOREIGN KEY
                (deployment_id, source_id, snapshot_id, snapshot_content_hash,
                 validation_report_hash, validation_diff_hash,
                 validation_passed)
                REFERENCES source_snapshot_validation_evidence
                (deployment_id, source_id, snapshot_id, snapshot_content_hash,
                 report_content_hash, diff_content_hash, passed)
        );

        CREATE TABLE source_snapshot_command_audit (
            command_event_id VARCHAR(128) PRIMARY KEY,
            authorization_event_id VARCHAR(128) NOT NULL,
            lifecycle_event_id VARCHAR(128),
            tenant_id VARCHAR(128) NOT NULL,
            deployment_id VARCHAR(128) NOT NULL,
            source_id VARCHAR(128) NOT NULL,
            raw_object_id VARCHAR(128) NOT NULL,
            snapshot_id VARCHAR(128),
            snapshot_content_hash VARCHAR(71),
            target_type VARCHAR(128) NOT NULL,
            target_id VARCHAR(128) NOT NULL,
            actor_id VARCHAR(128) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            outcome VARCHAR(16) NOT NULL,
            reason VARCHAR(64) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_source_snapshot_audit_lifecycle UNIQUE
                (lifecycle_event_id),
            CONSTRAINT ck_source_snapshot_audit_ids CHECK (
                command_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                authorization_event_id ~
                    '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                raw_object_id ~ '^raw-[a-f0-9]{64}$' AND
                actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_source_snapshot_audit_snapshot_ref CHECK (
                num_nonnulls(snapshot_id, snapshot_content_hash) IN (0, 2) AND
                (snapshot_id IS NULL OR (
                    snapshot_id ~ '^snapshot-[a-f0-9]{64}$' AND
                    snapshot_content_hash ~ '^sha256:[a-f0-9]{64}$' AND
                    snapshot_id = 'snapshot-' ||
                        substr(snapshot_content_hash, 8)
                ))
            ),
            CONSTRAINT ck_source_snapshot_audit_snapshot_shape CHECK (
                (reason IN (
                    'RETRIEVED', 'RETRIEVE_IDEMPOTENT',
                    'QUARANTINED', 'QUARANTINE_IDEMPOTENT'
                 ) AND snapshot_id IS NULL) OR
                (reason IN (
                    'PARSED', 'PARSE_IDEMPOTENT',
                    'VALIDATION_FAILED', 'VALIDATION_FAILURE_IDEMPOTENT',
                    'VALIDATED', 'VALIDATE_IDEMPOTENT',
                    'APPROVED', 'APPROVE_IDEMPOTENT',
                    'ACTIVATED', 'ACTIVATE_IDEMPOTENT',
                    'ROLLED_BACK', 'ROLLBACK_IDEMPOTENT'
                 ) AND snapshot_id IS NOT NULL) OR
                outcome = 'FAILURE'
            ),
            CONSTRAINT ck_source_snapshot_audit_target CHECK (
                (target_type = 'source' AND
                 target_id ~ '^source-[a-f0-9]{64}$' AND
                 operation IN (
                    'SOURCE_SNAPSHOT_INGEST', 'SOURCE_SNAPSHOT_PARSE',
                    'SOURCE_SNAPSHOT_VALIDATE'
                 )) OR
                (target_type = 'source_snapshot' AND
                 target_id ~ '^source-snapshot-[a-f0-9]{64}$' AND
                 operation IN (
                    'SOURCE_SNAPSHOT_APPROVE', 'SOURCE_SNAPSHOT_ACTIVATE',
                    'SOURCE_SNAPSHOT_ROLLBACK'
                 ))
            ),
            CONSTRAINT ck_source_snapshot_audit_operation CHECK (
                operation IN (
                    'SOURCE_SNAPSHOT_INGEST', 'SOURCE_SNAPSHOT_PARSE',
                    'SOURCE_SNAPSHOT_VALIDATE', 'SOURCE_SNAPSHOT_APPROVE',
                    'SOURCE_SNAPSHOT_ACTIVATE', 'SOURCE_SNAPSHOT_ROLLBACK'
                )
            ),
            CONSTRAINT ck_source_snapshot_audit_actor CHECK (
                actor_type IN ('HUMAN', 'SERVICE') AND
                (operation NOT IN (
                    'SOURCE_SNAPSHOT_APPROVE', 'SOURCE_SNAPSHOT_ACTIVATE',
                    'SOURCE_SNAPSHOT_ROLLBACK'
                 ) OR actor_type = 'HUMAN')
            ),
            CONSTRAINT ck_source_snapshot_audit_outcome CHECK (
                outcome IN ('SUCCESS', 'FAILURE')
            ),
            CONSTRAINT ck_source_snapshot_audit_reason CHECK (
                reason IN (
                    'RETRIEVED', 'RETRIEVE_IDEMPOTENT',
                    'QUARANTINED', 'QUARANTINE_IDEMPOTENT',
                    'PARSED', 'PARSE_IDEMPOTENT',
                    'VALIDATION_FAILED', 'VALIDATION_FAILURE_IDEMPOTENT',
                    'VALIDATED', 'VALIDATE_IDEMPOTENT',
                    'APPROVED', 'APPROVE_IDEMPOTENT',
                    'ACTIVATED', 'ACTIVATE_IDEMPOTENT',
                    'ROLLED_BACK', 'ROLLBACK_IDEMPOTENT',
                    'CONFLICT', 'AUTHORIZATION_BINDING_DENIED',
                    'CREATOR_SEPARATION_DENIED', 'VALIDATION_BLOCKED',
                    'INVALID_LIFECYCLE', 'PARSER_REJECTED',
                    'MEDIA_BINDING_DENIED', 'PERSISTENCE_FAILURE'
                )
            ),
            CONSTRAINT ck_source_snapshot_audit_outcome_reason CHECK (
                (outcome = 'SUCCESS' AND reason IN (
                    'RETRIEVED', 'RETRIEVE_IDEMPOTENT',
                    'QUARANTINED', 'QUARANTINE_IDEMPOTENT',
                    'PARSED', 'PARSE_IDEMPOTENT',
                    'VALIDATION_FAILED', 'VALIDATION_FAILURE_IDEMPOTENT',
                    'VALIDATED', 'VALIDATE_IDEMPOTENT',
                    'APPROVED', 'APPROVE_IDEMPOTENT',
                    'ACTIVATED', 'ACTIVATE_IDEMPOTENT',
                    'ROLLED_BACK', 'ROLLBACK_IDEMPOTENT'
                )) OR
                (outcome = 'FAILURE' AND reason IN (
                    'CONFLICT', 'AUTHORIZATION_BINDING_DENIED',
                    'CREATOR_SEPARATION_DENIED', 'VALIDATION_BLOCKED',
                    'INVALID_LIFECYCLE', 'PARSER_REJECTED',
                    'MEDIA_BINDING_DENIED', 'PERSISTENCE_FAILURE'
                ))
            ),
            CONSTRAINT ck_source_snapshot_audit_operation_reason CHECK (
                (reason IN ('RETRIEVED', 'RETRIEVE_IDEMPOTENT',
                            'QUARANTINED', 'QUARANTINE_IDEMPOTENT') AND
                 operation = 'SOURCE_SNAPSHOT_INGEST') OR
                (reason IN ('PARSED', 'PARSE_IDEMPOTENT') AND
                 operation = 'SOURCE_SNAPSHOT_PARSE') OR
                (reason IN ('VALIDATION_FAILED',
                            'VALIDATION_FAILURE_IDEMPOTENT', 'VALIDATED',
                            'VALIDATE_IDEMPOTENT') AND
                 operation = 'SOURCE_SNAPSHOT_VALIDATE') OR
                (reason IN ('APPROVED', 'APPROVE_IDEMPOTENT') AND
                 operation = 'SOURCE_SNAPSHOT_APPROVE') OR
                (reason IN ('ACTIVATED', 'ACTIVATE_IDEMPOTENT') AND
                 operation = 'SOURCE_SNAPSHOT_ACTIVATE') OR
                (reason IN ('ROLLED_BACK', 'ROLLBACK_IDEMPOTENT') AND
                 operation = 'SOURCE_SNAPSHOT_ROLLBACK') OR
                (outcome = 'FAILURE' AND reason IN (
                    'CONFLICT', 'AUTHORIZATION_BINDING_DENIED',
                    'CREATOR_SEPARATION_DENIED', 'VALIDATION_BLOCKED',
                    'INVALID_LIFECYCLE', 'PARSER_REJECTED',
                    'MEDIA_BINDING_DENIED', 'PERSISTENCE_FAILURE'
                ))
            ),
            CONSTRAINT ck_source_snapshot_audit_lifecycle_link CHECK (
                (reason IN (
                    'RETRIEVED', 'QUARANTINED', 'PARSED',
                    'VALIDATION_FAILED', 'VALIDATED', 'APPROVED',
                    'ACTIVATED', 'ROLLED_BACK'
                )) = (lifecycle_event_id IS NOT NULL)
            ),
            CONSTRAINT ck_source_snapshot_audit_time CHECK (
                isfinite(occurred_at)
            ),
            CONSTRAINT fk_source_snapshot_audit_authorization FOREIGN KEY
                (authorization_event_id)
                REFERENCES authorization_audit_event(event_id),
            CONSTRAINT fk_source_snapshot_audit_lifecycle FOREIGN KEY
                (lifecycle_event_id)
                REFERENCES source_snapshot_lifecycle_event(event_id),
            CONSTRAINT fk_source_snapshot_audit_registry FOREIGN KEY
                (deployment_id, source_id)
                REFERENCES source_registry(deployment_id, source_id)
            -- Standalone failure audits intentionally have no unconditional
            -- artifact FK. Applied provenance is enforced through the linked
            -- lifecycle event, whose raw/snapshot references are exact FKs.
        );

        CREATE FUNCTION tradesieve_validate_source_snapshot_audit_link()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            linked_authorization authorization_audit_event%ROWTYPE;
            linked_event source_snapshot_lifecycle_event%ROWTYPE;
            expected_reason TEXT;
            expected_operation TEXT;
            expected_target_type TEXT;
        BEGIN
            SELECT * INTO linked_authorization
            FROM authorization_audit_event
            WHERE event_id = NEW.authorization_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'linked authorization event does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF linked_authorization.outcome <> 'ALLOW' OR
               linked_authorization.actor_subject <> NEW.actor_id OR
               linked_authorization.actor_type <> NEW.actor_type OR
               linked_authorization.actor_tenant_id <> NEW.tenant_id OR
               linked_authorization.request_tenant_id <> NEW.tenant_id OR
               linked_authorization.target_tenant_id <> NEW.tenant_id OR
               linked_authorization.operation <> NEW.operation OR
               linked_authorization.target_ref <>
                   NEW.target_type || ':' || NEW.target_id THEN
                RAISE EXCEPTION 'command audit does not match authorization event'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.lifecycle_event_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT * INTO linked_event
            FROM source_snapshot_lifecycle_event
            WHERE event_id = NEW.lifecycle_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'linked lifecycle event does not exist'
                    USING ERRCODE = '23503';
            END IF;
            expected_reason := CASE linked_event.event_type
                WHEN 'RETRIEVED' THEN 'RETRIEVED'
                WHEN 'QUARANTINED' THEN 'QUARANTINED'
                WHEN 'PARSED' THEN 'PARSED'
                WHEN 'VALIDATION_FAILED' THEN 'VALIDATION_FAILED'
                WHEN 'VALIDATED' THEN 'VALIDATED'
                WHEN 'APPROVED' THEN 'APPROVED'
                WHEN 'ACTIVATED' THEN 'ACTIVATED'
                WHEN 'ROLLED_BACK' THEN 'ROLLED_BACK'
            END;
            expected_operation := CASE linked_event.event_type
                WHEN 'RETRIEVED' THEN 'SOURCE_SNAPSHOT_INGEST'
                WHEN 'QUARANTINED' THEN 'SOURCE_SNAPSHOT_INGEST'
                WHEN 'PARSED' THEN 'SOURCE_SNAPSHOT_PARSE'
                WHEN 'VALIDATION_FAILED' THEN 'SOURCE_SNAPSHOT_VALIDATE'
                WHEN 'VALIDATED' THEN 'SOURCE_SNAPSHOT_VALIDATE'
                WHEN 'APPROVED' THEN 'SOURCE_SNAPSHOT_APPROVE'
                WHEN 'ACTIVATED' THEN 'SOURCE_SNAPSHOT_ACTIVATE'
                WHEN 'ROLLED_BACK' THEN 'SOURCE_SNAPSHOT_ROLLBACK'
            END;
            expected_target_type := CASE
                WHEN linked_event.event_type IN
                    ('APPROVED', 'ACTIVATED', 'ROLLED_BACK')
                THEN 'source_snapshot'
                ELSE 'source'
            END;
            IF NEW.outcome <> 'SUCCESS' OR
               NEW.reason <> expected_reason OR
               NEW.operation <> expected_operation OR
               NEW.target_type <> expected_target_type OR
               NEW.deployment_id <> linked_event.deployment_id OR
               NEW.source_id <> linked_event.source_id OR
               NEW.raw_object_id <> linked_event.raw_object_id OR
               NEW.snapshot_id IS DISTINCT FROM linked_event.snapshot_id OR
               NEW.snapshot_content_hash IS DISTINCT FROM
                   linked_event.snapshot_content_hash OR
               NEW.actor_id <> linked_event.actor_id OR
               NEW.actor_type <> linked_event.actor_type OR
               NEW.occurred_at <> linked_event.occurred_at THEN
                RAISE EXCEPTION 'command audit does not match lifecycle event'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE FUNCTION tradesieve_require_source_snapshot_event_audit()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            linked_count BIGINT;
        BEGIN
            SELECT count(*) INTO linked_count
            FROM source_snapshot_command_audit
            WHERE lifecycle_event_id = NEW.event_id;
            IF linked_count <> 1 THEN
                RAISE EXCEPTION 'lifecycle event requires exactly one command audit'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE INDEX ix_source_raw_object_scope_retrieved
            ON source_raw_object_metadata
            (deployment_id, source_id, retrieved_at, object_id);
        CREATE INDEX ix_source_parsed_snapshot_scope_parsed
            ON source_parsed_snapshot
            (deployment_id, source_id, parsed_at, snapshot_id);
        CREATE INDEX ix_source_snapshot_validation_scope_time
            ON source_snapshot_validation_evidence
            (deployment_id, source_id, validated_at, snapshot_id);
        CREATE INDEX ix_source_snapshot_event_scope_time
            ON source_snapshot_lifecycle_event
            (deployment_id, source_id, occurred_at, sequence);
        CREATE INDEX ix_source_snapshot_event_scope_type
            ON source_snapshot_lifecycle_event
            (deployment_id, source_id, event_type, sequence);
        CREATE INDEX ix_source_snapshot_audit_scope_time
            ON source_snapshot_command_audit
            (tenant_id, deployment_id, source_id, occurred_at, command_event_id);

        CREATE TRIGGER trg_source_raw_object_immutable
            BEFORE UPDATE OR DELETE ON source_raw_object_metadata
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_source_parsed_snapshot_immutable
            BEFORE UPDATE OR DELETE ON source_parsed_snapshot
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_source_snapshot_validation_immutable
            BEFORE UPDATE OR DELETE ON source_snapshot_validation_evidence
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_source_snapshot_event_immutable
            BEFORE UPDATE OR DELETE ON source_snapshot_lifecycle_event
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_source_snapshot_audit_immutable
            BEFORE UPDATE OR DELETE ON source_snapshot_command_audit
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_source_snapshot_audit_link
            BEFORE INSERT ON source_snapshot_command_audit
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_source_snapshot_audit_link();
        CREATE CONSTRAINT TRIGGER trg_source_snapshot_event_requires_audit
            AFTER INSERT ON source_snapshot_lifecycle_event
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_require_source_snapshot_event_audit();

        ALTER TABLE source_runtime_observation
            ADD CONSTRAINT fk_source_observation_active_snapshot
            FOREIGN KEY (deployment_id, source_id, active_snapshot_id)
            REFERENCES source_parsed_snapshot
                (deployment_id, source_id, snapshot_id)
            NOT VALID;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE source_runtime_observation
            DROP CONSTRAINT fk_source_observation_active_snapshot;
        DROP TABLE source_snapshot_command_audit;
        DROP FUNCTION tradesieve_validate_source_snapshot_audit_link();
        DROP TRIGGER trg_source_snapshot_event_requires_audit
            ON source_snapshot_lifecycle_event;
        DROP FUNCTION tradesieve_require_source_snapshot_event_audit();
        DROP TABLE source_snapshot_lifecycle_event;
        DROP TABLE source_snapshot_lifecycle_state;
        DROP TABLE source_snapshot_validation_evidence;
        DROP FUNCTION tradesieve_validate_source_validation_insert();
        DROP TABLE source_parsed_snapshot;
        DROP FUNCTION tradesieve_validate_source_parsed_snapshot_insert();
        DROP TABLE source_raw_object_metadata;
        DROP FUNCTION tradesieve_valid_source_validation_payload(BYTEA);
        DROP FUNCTION tradesieve_valid_source_snapshot_payload(BYTEA);
        """
    )
