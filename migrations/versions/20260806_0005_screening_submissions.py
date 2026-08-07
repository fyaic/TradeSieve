"""Create immutable canonical screening submission storage."""

from __future__ import annotations

from alembic import op

revision = "20260806_0005"
down_revision = "20260806_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION tradesieve_screening_scope_fingerprint(
            p_tenant_id TEXT,
            p_client_id TEXT,
            p_operation TEXT,
            p_key_digest TEXT
        ) RETURNS TEXT LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT 'sha256:' || encode(
                sha256(convert_to(
                    '{"client_id":"' || p_client_id ||
                    '","key_digest":"' || p_key_digest ||
                    '","operation":"' || p_operation ||
                    '","tenant_id":"' || p_tenant_id || '"}',
                    'UTF8'
                )),
                'hex'
            )
        $$;

        CREATE TABLE screening_opaque_id_registry (
            opaque_id VARCHAR(128) NOT NULL,
            object_kind VARCHAR(16) NOT NULL,
            CONSTRAINT pk_screening_opaque_id_registry PRIMARY KEY (opaque_id),
            CONSTRAINT uq_screening_opaque_id_kind UNIQUE
                (opaque_id, object_kind),
            CONSTRAINT ck_screening_opaque_id CHECK (
                opaque_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_screening_opaque_kind CHECK (
                object_kind IN ('INTAKE', 'SCREENING', 'ATTEMPT', 'OUTBOX')
            )
        );

        CREATE TABLE screening_accepted_intake (
            intake_id VARCHAR(128) NOT NULL,
            id_kind VARCHAR(16) NOT NULL,
            tenant_id VARCHAR(128) NOT NULL,
            client_id VARCHAR(128) NOT NULL,
            actor_subject VARCHAR(128) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            correlation_id VARCHAR(128) NOT NULL,
            scope_fingerprint VARCHAR(71) NOT NULL,
            schema_version VARCHAR(16) NOT NULL,
            media_type VARCHAR(32) NOT NULL,
            raw_body BYTEA NOT NULL,
            byte_length INTEGER NOT NULL,
            byte_hash VARCHAR(71) NOT NULL,
            canonical_hash VARCHAR(71) NOT NULL,
            received_at TIMESTAMPTZ NOT NULL,
            private_context BYTEA NOT NULL,
            CONSTRAINT pk_screening_accepted_intake PRIMARY KEY (intake_id),
            CONSTRAINT uq_screening_intake_result_ref UNIQUE
                (intake_id, tenant_id, client_id, scope_fingerprint,
                 canonical_hash),
            CONSTRAINT ck_screening_intake_kind CHECK (id_kind = 'INTAKE'),
            CONSTRAINT ck_screening_intake_ids CHECK (
                intake_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                client_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                actor_subject ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_screening_intake_actor CHECK (
                actor_type IN ('HUMAN', 'SERVICE', 'AGENT')
            ),
            CONSTRAINT ck_screening_intake_document CHECK (
                schema_version = '1.0.0' AND
                media_type = 'application/json' AND
                byte_length BETWEEN 1 AND 1048576 AND
                octet_length(raw_body) = byte_length AND
                octet_length(private_context) BETWEEN 1 AND 8192
            ),
            CONSTRAINT ck_screening_intake_hashes CHECK (
                scope_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND
                byte_hash ~ '^sha256:[a-f0-9]{64}$' AND
                canonical_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_screening_intake_time CHECK (isfinite(received_at)),
            CONSTRAINT fk_screening_intake_registry FOREIGN KEY
                (intake_id, id_kind)
                REFERENCES screening_opaque_id_registry
                (opaque_id, object_kind)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE screening_identity (
            screening_id VARCHAR(128) NOT NULL,
            id_kind VARCHAR(16) NOT NULL,
            intake_id VARCHAR(128) NOT NULL,
            tenant_id VARCHAR(128) NOT NULL,
            client_id VARCHAR(128) NOT NULL,
            scope_fingerprint VARCHAR(71) NOT NULL,
            canonical_hash VARCHAR(71) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_screening_identity PRIMARY KEY (screening_id),
            CONSTRAINT uq_screening_identity_intake UNIQUE (intake_id),
            CONSTRAINT uq_screening_identity_outbox_ref UNIQUE
                (screening_id, intake_id, created_at),
            CONSTRAINT uq_screening_identity_result_ref UNIQUE
                (screening_id, intake_id, tenant_id, client_id,
                 scope_fingerprint, canonical_hash, created_at),
            CONSTRAINT ck_screening_identity_kind CHECK
                (id_kind = 'SCREENING'),
            CONSTRAINT ck_screening_identity_ids CHECK (
                screening_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                intake_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                client_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_screening_identity_hashes CHECK (
                scope_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND
                canonical_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_screening_identity_time CHECK (isfinite(created_at)),
            CONSTRAINT fk_screening_identity_registry FOREIGN KEY
                (screening_id, id_kind)
                REFERENCES screening_opaque_id_registry
                (opaque_id, object_kind)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_identity_intake FOREIGN KEY
                (intake_id, tenant_id, client_id, scope_fingerprint,
                 canonical_hash)
                REFERENCES screening_accepted_intake
                (intake_id, tenant_id, client_id, scope_fingerprint,
                 canonical_hash)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE screening_accepted_outbox (
            event_id VARCHAR(128) NOT NULL,
            id_kind VARCHAR(16) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            schema_version VARCHAR(16) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            intake_id VARCHAR(128) NOT NULL,
            screening_id VARCHAR(128) NOT NULL,
            CONSTRAINT pk_screening_accepted_outbox PRIMARY KEY (event_id),
            CONSTRAINT uq_screening_outbox_intake UNIQUE (intake_id),
            CONSTRAINT uq_screening_outbox_screening UNIQUE (screening_id),
            CONSTRAINT uq_screening_outbox_result_ref UNIQUE
                (event_id, intake_id, screening_id, occurred_at),
            CONSTRAINT ck_screening_outbox_kind CHECK (id_kind = 'OUTBOX'),
            CONSTRAINT ck_screening_outbox_ids CHECK (
                event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                intake_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                screening_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_screening_outbox_shape CHECK (
                event_type = 'screening.accepted' AND
                schema_version = '1.0.0'
            ),
            CONSTRAINT ck_screening_outbox_time CHECK (isfinite(occurred_at)),
            CONSTRAINT fk_screening_outbox_registry FOREIGN KEY
                (event_id, id_kind)
                REFERENCES screening_opaque_id_registry
                (opaque_id, object_kind)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_outbox_intake FOREIGN KEY (intake_id)
                REFERENCES screening_accepted_intake(intake_id)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_outbox_screening FOREIGN KEY
                (screening_id, intake_id, occurred_at)
                REFERENCES screening_identity
                (screening_id, intake_id, created_at)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE screening_idempotency_ledger (
            tenant_id VARCHAR(128) NOT NULL,
            client_id VARCHAR(128) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            key_digest VARCHAR(71) NOT NULL,
            scope_fingerprint VARCHAR(71) NOT NULL,
            canonical_hash VARCHAR(71) NOT NULL,
            intake_id VARCHAR(128) NOT NULL,
            screening_id VARCHAR(128) NOT NULL,
            outbox_id VARCHAR(128) NOT NULL,
            committed_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT pk_screening_idempotency_ledger PRIMARY KEY
                (tenant_id, client_id, operation, key_digest),
            CONSTRAINT uq_screening_ledger_fingerprint UNIQUE
                (scope_fingerprint),
            CONSTRAINT uq_screening_ledger_intake UNIQUE (intake_id),
            CONSTRAINT uq_screening_ledger_screening UNIQUE (screening_id),
            CONSTRAINT uq_screening_ledger_outbox UNIQUE (outbox_id),
            CONSTRAINT uq_screening_ledger_scope_ref UNIQUE
                (tenant_id, client_id, operation, key_digest,
                 scope_fingerprint),
            CONSTRAINT ck_screening_ledger_ids CHECK (
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                client_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                intake_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                screening_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                outbox_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_screening_ledger_operation CHECK
                (operation = 'SCREENING_SUBMIT'),
            CONSTRAINT ck_screening_ledger_hashes CHECK (
                key_digest ~ '^sha256:[a-f0-9]{64}$' AND
                scope_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND
                canonical_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_screening_ledger_fingerprint CHECK (
                scope_fingerprint = tradesieve_screening_scope_fingerprint(
                    tenant_id, client_id, operation, key_digest
                )
            ),
            CONSTRAINT ck_screening_ledger_time CHECK (isfinite(committed_at)),
            CONSTRAINT fk_screening_ledger_intake FOREIGN KEY
                (intake_id, tenant_id, client_id, scope_fingerprint,
                 canonical_hash)
                REFERENCES screening_accepted_intake
                (intake_id, tenant_id, client_id, scope_fingerprint,
                 canonical_hash)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_ledger_screening FOREIGN KEY
                (screening_id, intake_id, tenant_id, client_id,
                 scope_fingerprint, canonical_hash, committed_at)
                REFERENCES screening_identity
                (screening_id, intake_id, tenant_id, client_id,
                 scope_fingerprint, canonical_hash, created_at)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_ledger_outbox FOREIGN KEY
                (outbox_id, intake_id, screening_id, committed_at)
                REFERENCES screening_accepted_outbox
                (event_id, intake_id, screening_id, occurred_at)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE screening_attempt_audit (
            tenant_id VARCHAR(128) NOT NULL,
            client_id VARCHAR(128) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            key_digest VARCHAR(71) NOT NULL,
            scope_fingerprint VARCHAR(71) NOT NULL,
            sequence INTEGER NOT NULL,
            attempt_id VARCHAR(128) NOT NULL,
            id_kind VARCHAR(16) NOT NULL,
            outcome VARCHAR(16) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            actor_subject VARCHAR(128) NOT NULL,
            correlation_id VARCHAR(128) NOT NULL,
            authorization_event_id VARCHAR(128) NOT NULL,
            byte_hash VARCHAR(71) NOT NULL,
            canonical_hash VARCHAR(71) NOT NULL,
            CONSTRAINT pk_screening_attempt PRIMARY KEY
                (tenant_id, client_id, operation, key_digest, sequence),
            CONSTRAINT uq_screening_attempt_id UNIQUE (attempt_id),
            CONSTRAINT uq_screening_attempt_authorization UNIQUE
                (authorization_event_id),
            CONSTRAINT ck_screening_attempt_kind CHECK (id_kind = 'ATTEMPT'),
            CONSTRAINT ck_screening_attempt_ids CHECK (
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                client_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                attempt_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                actor_subject ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                authorization_event_id ~
                    '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_screening_attempt_actor CHECK (
                actor_type IN ('HUMAN', 'SERVICE', 'AGENT')
            ),
            CONSTRAINT ck_screening_attempt_hashes CHECK (
                key_digest ~ '^sha256:[a-f0-9]{64}$' AND
                scope_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND
                byte_hash ~ '^sha256:[a-f0-9]{64}$' AND
                canonical_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_screening_attempt_sequence CHECK
                (sequence BETWEEN 1 AND 4096),
            CONSTRAINT ck_screening_attempt_outcome_sequence CHECK (
                (sequence = 1 AND outcome = 'APPLIED') OR
                (sequence > 1 AND outcome IN ('REPLAY', 'CONFLICT'))
            ),
            CONSTRAINT ck_screening_attempt_time CHECK (isfinite(occurred_at)),
            CONSTRAINT fk_screening_attempt_registry FOREIGN KEY
                (attempt_id, id_kind)
                REFERENCES screening_opaque_id_registry
                (opaque_id, object_kind)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_attempt_ledger FOREIGN KEY
                (tenant_id, client_id, operation, key_digest,
                 scope_fingerprint)
                REFERENCES screening_idempotency_ledger
                (tenant_id, client_id, operation, key_digest,
                 scope_fingerprint)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT fk_screening_attempt_authorization FOREIGN KEY
                (authorization_event_id)
                REFERENCES authorization_audit_event(event_id)
                DEFERRABLE INITIALLY DEFERRED
        );

        CREATE FUNCTION tradesieve_validate_screening_attempt()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            linked_ledger screening_idempotency_ledger%ROWTYPE;
            linked_intake screening_accepted_intake%ROWTYPE;
            linked_authorization authorization_audit_event%ROWTYPE;
            expected_sequence BIGINT;
            expected_target TEXT;
        BEGIN
            SELECT * INTO linked_ledger
            FROM screening_idempotency_ledger
            WHERE tenant_id = NEW.tenant_id
              AND client_id = NEW.client_id
              AND operation = NEW.operation
              AND key_digest = NEW.key_digest
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'screening attempt ledger does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF NEW.scope_fingerprint <> linked_ledger.scope_fingerprint THEN
                RAISE EXCEPTION 'screening attempt scope does not match ledger'
                    USING ERRCODE = '23514';
            END IF;

            -- The 4097th row is the read-side overflow sentinel. The schema
            -- itself permits at most 4096 committed attempts per scope.
            SELECT COALESCE(max(sequence), 0) + 1 INTO expected_sequence
            FROM (
                SELECT sequence
                FROM screening_attempt_audit
                WHERE tenant_id = NEW.tenant_id
                  AND client_id = NEW.client_id
                  AND operation = NEW.operation
                  AND key_digest = NEW.key_digest
                ORDER BY sequence DESC
                LIMIT 4097
            ) AS bounded_attempts;
            IF expected_sequence > 4096 THEN
                RAISE EXCEPTION 'screening attempt history limit reached'
                    USING ERRCODE = '54000';
            END IF;
            IF NEW.sequence <> expected_sequence THEN
                RAISE EXCEPTION 'screening attempt sequence is not contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF (NEW.sequence = 1 AND NEW.outcome <> 'APPLIED') OR
               (NEW.sequence > 1 AND NEW.outcome = 'APPLIED') OR
               (NEW.sequence > 1 AND
                NEW.canonical_hash = linked_ledger.canonical_hash AND
                NEW.outcome <> 'REPLAY') OR
               (NEW.sequence > 1 AND
                NEW.canonical_hash <> linked_ledger.canonical_hash AND
                NEW.outcome <> 'CONFLICT') THEN
                RAISE EXCEPTION 'screening attempt outcome is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT * INTO linked_authorization
            FROM authorization_audit_event
            WHERE event_id = NEW.authorization_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'screening authorization event does not exist'
                    USING ERRCODE = '23503';
            END IF;
            expected_target := 'SCREENING_IDEMPOTENCY:idem-' ||
                substr(NEW.scope_fingerprint, 8);
            IF linked_authorization.outcome <> 'ALLOW' OR
               linked_authorization.reason <> 'ALLOWED' OR
               linked_authorization.occurred_at <> NEW.occurred_at OR
               linked_authorization.actor_subject <> NEW.actor_subject OR
               linked_authorization.actor_type <> NEW.actor_type OR
               linked_authorization.client_id <> NEW.client_id OR
               linked_authorization.actor_tenant_id <> NEW.tenant_id OR
               linked_authorization.request_tenant_id <> NEW.tenant_id OR
               linked_authorization.target_tenant_id <> NEW.tenant_id OR
               linked_authorization.operation <> NEW.operation OR
               linked_authorization.target_ref <> expected_target OR
               linked_authorization.correlation_id <> NEW.correlation_id THEN
                RAISE EXCEPTION 'screening attempt authorization does not match'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.sequence = 1 THEN
                SELECT * INTO linked_intake
                FROM screening_accepted_intake
                WHERE intake_id = linked_ledger.intake_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'screening applied intake does not exist'
                        USING ERRCODE = '23503';
                END IF;
                IF NEW.outcome <> 'APPLIED' OR
                   NEW.occurred_at <> linked_ledger.committed_at OR
                   linked_ledger.committed_at < linked_intake.received_at OR
                   NEW.actor_type <> linked_intake.actor_type OR
                   NEW.actor_subject <> linked_intake.actor_subject OR
                   NEW.correlation_id <> linked_intake.correlation_id OR
                   NEW.byte_hash <> linked_intake.byte_hash OR
                   NEW.canonical_hash <> linked_intake.canonical_hash OR
                   NEW.canonical_hash <> linked_ledger.canonical_hash THEN
                    RAISE EXCEPTION 'screening applied attempt does not match intake'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE FUNCTION tradesieve_require_screening_complete_graph()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            graph_count BIGINT;
            applied_count BIGINT;
        BEGIN
            SELECT count(*) INTO graph_count
            FROM screening_accepted_intake AS intake
            JOIN screening_identity AS screening
              ON screening.screening_id = NEW.screening_id
             AND screening.intake_id = intake.intake_id
             AND screening.tenant_id = intake.tenant_id
             AND screening.client_id = intake.client_id
             AND screening.scope_fingerprint = intake.scope_fingerprint
             AND screening.canonical_hash = intake.canonical_hash
             AND screening.created_at = NEW.committed_at
            JOIN screening_accepted_outbox AS outbox
              ON outbox.event_id = NEW.outbox_id
             AND outbox.intake_id = intake.intake_id
             AND outbox.screening_id = screening.screening_id
             AND outbox.occurred_at = NEW.committed_at
            WHERE intake.intake_id = NEW.intake_id
              AND intake.tenant_id = NEW.tenant_id
              AND intake.client_id = NEW.client_id
              AND intake.scope_fingerprint = NEW.scope_fingerprint
              AND intake.canonical_hash = NEW.canonical_hash
              AND NEW.committed_at >= intake.received_at;
            IF graph_count <> 1 THEN
                RAISE EXCEPTION 'screening ledger requires one complete graph'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO applied_count
            FROM screening_attempt_audit
            WHERE tenant_id = NEW.tenant_id
              AND client_id = NEW.client_id
              AND operation = NEW.operation
              AND key_digest = NEW.key_digest
              AND scope_fingerprint = NEW.scope_fingerprint
              AND sequence = 1
              AND outcome = 'APPLIED'
              AND occurred_at = NEW.committed_at
              AND canonical_hash = NEW.canonical_hash;
            IF applied_count <> 1 THEN
                RAISE EXCEPTION 'screening ledger requires one applied attempt'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION tradesieve_require_screening_registry_child()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            linked_count BIGINT;
        BEGIN
            CASE NEW.object_kind
                WHEN 'INTAKE' THEN
                    SELECT count(*) INTO linked_count
                    FROM screening_accepted_intake AS intake
                    JOIN screening_idempotency_ledger AS ledger
                      ON ledger.intake_id = intake.intake_id
                    WHERE intake.intake_id = NEW.opaque_id;
                WHEN 'SCREENING' THEN
                    SELECT count(*) INTO linked_count
                    FROM screening_identity AS screening
                    JOIN screening_idempotency_ledger AS ledger
                      ON ledger.screening_id = screening.screening_id
                    WHERE screening.screening_id = NEW.opaque_id;
                WHEN 'ATTEMPT' THEN
                    SELECT count(*) INTO linked_count
                    FROM screening_attempt_audit
                    WHERE attempt_id = NEW.opaque_id;
                WHEN 'OUTBOX' THEN
                    SELECT count(*) INTO linked_count
                    FROM screening_accepted_outbox AS outbox
                    JOIN screening_idempotency_ledger AS ledger
                      ON ledger.outbox_id = outbox.event_id
                    WHERE outbox.event_id = NEW.opaque_id;
            END CASE;
            IF linked_count <> 1 THEN
                RAISE EXCEPTION 'screening registry requires one typed child'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE INDEX ix_screening_intake_tenant_received
            ON screening_accepted_intake (tenant_id, received_at, intake_id);
        CREATE INDEX ix_screening_identity_tenant_created
            ON screening_identity (tenant_id, created_at, screening_id);
        CREATE INDEX ix_screening_attempt_scope_time
            ON screening_attempt_audit
            (tenant_id, client_id, occurred_at, sequence);
        CREATE INDEX ix_screening_outbox_occurred
            ON screening_accepted_outbox (occurred_at, event_id);

        CREATE TRIGGER trg_screening_registry_immutable
            BEFORE UPDATE OR DELETE ON screening_opaque_id_registry
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_screening_intake_immutable
            BEFORE UPDATE OR DELETE ON screening_accepted_intake
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_screening_identity_immutable
            BEFORE UPDATE OR DELETE ON screening_identity
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_screening_ledger_immutable
            BEFORE UPDATE OR DELETE ON screening_idempotency_ledger
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_screening_attempt_immutable
            BEFORE UPDATE OR DELETE ON screening_attempt_audit
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_screening_outbox_immutable
            BEFORE UPDATE OR DELETE ON screening_accepted_outbox
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_screening_attempt_validate
            BEFORE INSERT ON screening_attempt_audit
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_validate_screening_attempt();
        CREATE CONSTRAINT TRIGGER trg_screening_ledger_complete
            AFTER INSERT ON screening_idempotency_ledger
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_require_screening_complete_graph();
        CREATE CONSTRAINT TRIGGER trg_screening_registry_child
            AFTER INSERT ON screening_opaque_id_registry
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_require_screening_registry_child();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE screening_attempt_audit;
        DROP FUNCTION tradesieve_validate_screening_attempt();
        DROP TABLE screening_idempotency_ledger;
        DROP FUNCTION tradesieve_require_screening_complete_graph();
        DROP TABLE screening_accepted_outbox;
        DROP TABLE screening_identity;
        DROP TABLE screening_accepted_intake;
        DROP TABLE screening_opaque_id_registry;
        DROP FUNCTION tradesieve_require_screening_registry_child();
        DROP FUNCTION tradesieve_screening_scope_fingerprint(
            TEXT, TEXT, TEXT, TEXT
        );
        """
    )
