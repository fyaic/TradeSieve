"""Create immutable rule-bundle, lifecycle, state, and command-audit storage."""

from __future__ import annotations

from alembic import op

revision = "20260806_0003"
down_revision = "20260806_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION tradesieve_reject_immutable_change()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END
        $$;

        CREATE FUNCTION tradesieve_valid_rule_bundle_ref(value JSONB)
        RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT CASE
                WHEN value = 'null'::jsonb THEN TRUE
                WHEN jsonb_typeof(value) <> 'object' THEN FALSE
                ELSE
                    (SELECT count(*) FROM jsonb_object_keys(value)) = 3 AND
                    value ?& ARRAY['bundle_id', 'version', 'content_hash'] AND
                    jsonb_typeof(value -> 'bundle_id') = 'string' AND
                    jsonb_typeof(value -> 'version') = 'string' AND
                    jsonb_typeof(value -> 'content_hash') = 'string' AND
                    value ->> 'bundle_id' ~
                        '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                    value ->> 'version' ~
                        '^(0|[1-9][0-9]{0,8})[.](0|[1-9][0-9]{0,8})[.](0|[1-9][0-9]{0,8})$' AND
                    value ->> 'content_hash' ~ '^sha256:[a-f0-9]{64}$'
            END
        $$;

        CREATE FUNCTION tradesieve_valid_rule_id_array(value JSONB)
        RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT CASE WHEN jsonb_typeof(value) <> 'array' THEN FALSE ELSE (
                SELECT count(*) <= 256 AND
                    COALESCE(bool_and(
                        jsonb_typeof(item) = 'string' AND
                        (item #>> '{}') ~
                            '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                    ), TRUE) AND
                    count(*) = count(DISTINCT (item #>> '{}')) AND
                    COALESCE(
                        array_agg((item #>> '{}') ORDER BY ordinal) =
                        array_agg((item #>> '{}')
                                  ORDER BY (item #>> '{}') COLLATE "C"),
                        TRUE
                    )
                FROM jsonb_array_elements(value)
                    WITH ORDINALITY AS members(item, ordinal)
            ) END
        $$;

        CREATE FUNCTION tradesieve_valid_rule_fact_array(value JSONB)
        RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT CASE WHEN jsonb_typeof(value) <> 'array' THEN FALSE ELSE (
                SELECT count(*) <= 32 AND
                    COALESCE(bool_and(
                        jsonb_typeof(item) = 'string' AND
                        (item #>> '{}') IN (
                            'proposed_action', 'legal_nexus', 'activities',
                            'parties', 'goods', 'route', 'payment'
                        )
                    ), TRUE) AND
                    count(*) = count(DISTINCT (item #>> '{}')) AND
                    COALESCE(
                        array_agg((item #>> '{}') ORDER BY ordinal) =
                        array_agg((item #>> '{}')
                                  ORDER BY (item #>> '{}') COLLATE "C"),
                        TRUE
                    )
                FROM jsonb_array_elements(value)
                    WITH ORDINALITY AS members(item, ordinal)
            ) END
        $$;

        CREATE FUNCTION tradesieve_valid_rescreen_impact(value JSONB)
        RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT CASE WHEN jsonb_typeof(value) <> 'object' THEN FALSE ELSE
                (SELECT count(*) FROM jsonb_object_keys(value)) = 8 AND
                value ?& ARRAY[
                    'schema_version', 'previous_bundle', 'new_bundle',
                    'added_rule_ids', 'removed_rule_ids', 'changed_rule_ids',
                    'affected_fact_paths', 'rescreen_required'
                ] AND
                value ->> 'schema_version' = '1.0.0' AND
                tradesieve_valid_rule_bundle_ref(value -> 'previous_bundle') AND
                tradesieve_valid_rule_bundle_ref(value -> 'new_bundle') AND
                tradesieve_valid_rule_id_array(value -> 'added_rule_ids') AND
                tradesieve_valid_rule_id_array(value -> 'removed_rule_ids') AND
                tradesieve_valid_rule_id_array(value -> 'changed_rule_ids') AND
                tradesieve_valid_rule_fact_array(value -> 'affected_fact_paths') AND
                jsonb_typeof(value -> 'rescreen_required') = 'boolean' AND
                octet_length(value::text) <= 1048576 END
        $$;

        CREATE TABLE rule_bundle_version (
            tenant_id VARCHAR(128) NOT NULL,
            deployment_id VARCHAR(128) NOT NULL,
            rule_set_id VARCHAR(128) NOT NULL,
            bundle_id VARCHAR(128) NOT NULL,
            version VARCHAR(32) NOT NULL,
            content_hash VARCHAR(71) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_rule_bundle_version PRIMARY KEY
                (tenant_id, deployment_id, rule_set_id, bundle_id, version),
            CONSTRAINT uq_rule_bundle_version_full_ref UNIQUE
                (tenant_id, deployment_id, rule_set_id, bundle_id, version,
                 content_hash),
            CONSTRAINT ck_rule_bundle_version_ids CHECK (
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                rule_set_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                bundle_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_rule_bundle_version_semver CHECK (
                version ~ '^(0|[1-9][0-9]{0,8})[.](0|[1-9][0-9]{0,8})[.](0|[1-9][0-9]{0,8})$'
            ),
            CONSTRAINT ck_rule_bundle_version_hash CHECK (
                content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_rule_bundle_version_payload CHECK (
                jsonb_typeof(payload) = 'object' AND
                payload ->> 'schema_version' = '1.0.0' AND
                octet_length(payload::text) <= 1048576
            )
        );

        CREATE TABLE rule_bundle_lifecycle_state (
            tenant_id VARCHAR(128) NOT NULL,
            deployment_id VARCHAR(128) NOT NULL,
            rule_set_id VARCHAR(128) NOT NULL,
            last_sequence BIGINT NOT NULL DEFAULT 0,
            active_tenant_id VARCHAR(128),
            active_deployment_id VARCHAR(128),
            active_rule_set_id VARCHAR(128),
            active_bundle_id VARCHAR(128),
            active_version VARCHAR(32),
            active_content_hash VARCHAR(71),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_rule_bundle_lifecycle_state PRIMARY KEY
                (tenant_id, deployment_id, rule_set_id),
            CONSTRAINT ck_rule_bundle_state_ids CHECK (
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                rule_set_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_rule_bundle_state_sequence CHECK (last_sequence >= 0),
            CONSTRAINT ck_rule_bundle_state_active_scope CHECK (
                num_nonnulls(
                    active_tenant_id, active_deployment_id, active_rule_set_id,
                    active_bundle_id, active_version, active_content_hash
                ) IN (0, 6) AND
                (active_tenant_id IS NULL OR (
                    active_tenant_id = tenant_id AND
                    active_deployment_id = deployment_id AND
                    active_rule_set_id = rule_set_id
                ))
            ),
            CONSTRAINT fk_rule_bundle_state_active FOREIGN KEY
                (active_tenant_id, active_deployment_id, active_rule_set_id,
                 active_bundle_id, active_version, active_content_hash)
                REFERENCES rule_bundle_version
                (tenant_id, deployment_id, rule_set_id, bundle_id, version,
                 content_hash) MATCH FULL
        );

        CREATE TABLE rule_bundle_lifecycle_event (
            tenant_id VARCHAR(128) NOT NULL,
            deployment_id VARCHAR(128) NOT NULL,
            rule_set_id VARCHAR(128) NOT NULL,
            sequence BIGINT NOT NULL,
            event_id VARCHAR(128) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            previous_tenant_id VARCHAR(128),
            previous_deployment_id VARCHAR(128),
            previous_rule_set_id VARCHAR(128),
            previous_bundle_id VARCHAR(128),
            previous_version VARCHAR(32),
            previous_content_hash VARCHAR(71),
            new_tenant_id VARCHAR(128),
            new_deployment_id VARCHAR(128),
            new_rule_set_id VARCHAR(128),
            new_bundle_id VARCHAR(128),
            new_version VARCHAR(32),
            new_content_hash VARCHAR(71),
            reason TEXT NOT NULL,
            actor_id VARCHAR(128) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            rescreen_impact JSONB,
            CONSTRAINT pk_rule_bundle_lifecycle_event PRIMARY KEY
                (tenant_id, deployment_id, rule_set_id, sequence),
            CONSTRAINT uq_rule_bundle_lifecycle_event_id UNIQUE (event_id),
            CONSTRAINT ck_rule_bundle_event_ids CHECK (
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                rule_set_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_rule_bundle_event_sequence CHECK (sequence > 0),
            CONSTRAINT ck_rule_bundle_event_type CHECK (
                event_type IN
                    ('DRAFTED', 'APPROVED', 'ACTIVATED', 'RETIRED', 'ROLLED_BACK')
            ),
            CONSTRAINT ck_rule_bundle_event_actor CHECK (
                actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                ((event_type = 'DRAFTED' AND
                  actor_type IN ('HUMAN', 'SERVICE')) OR
                 (event_type <> 'DRAFTED' AND actor_type = 'HUMAN'))
            ),
            CONSTRAINT ck_rule_bundle_event_reason CHECK (
                length(reason) BETWEEN 1 AND 5000 AND reason = btrim(reason) AND
                reason !~ '[[:cntrl:]]'
            ),
            CONSTRAINT ck_rule_bundle_event_previous_ref CHECK (
                num_nonnulls(
                    previous_tenant_id, previous_deployment_id,
                    previous_rule_set_id, previous_bundle_id, previous_version,
                    previous_content_hash
                ) IN (0, 6) AND
                (previous_tenant_id IS NULL OR (
                    previous_tenant_id = tenant_id AND
                    previous_deployment_id = deployment_id AND
                    previous_rule_set_id = rule_set_id
                ))
            ),
            CONSTRAINT ck_rule_bundle_event_new_ref CHECK (
                num_nonnulls(
                    new_tenant_id, new_deployment_id, new_rule_set_id,
                    new_bundle_id, new_version, new_content_hash
                ) IN (0, 6) AND
                (new_tenant_id IS NULL OR (
                    new_tenant_id = tenant_id AND
                    new_deployment_id = deployment_id AND
                    new_rule_set_id = rule_set_id
                ))
            ),
            CONSTRAINT ck_rule_bundle_event_shape CHECK (
                (event_type IN ('DRAFTED', 'APPROVED') AND
                 previous_tenant_id IS NULL AND new_tenant_id IS NOT NULL AND
                 rescreen_impact IS NULL) OR
                (event_type = 'ACTIVATED' AND new_tenant_id IS NOT NULL AND
                 rescreen_impact IS NOT NULL) OR
                (event_type = 'RETIRED' AND previous_tenant_id IS NOT NULL AND
                 new_tenant_id IS NULL AND rescreen_impact IS NOT NULL) OR
                (event_type = 'ROLLED_BACK' AND previous_tenant_id IS NOT NULL AND
                 new_tenant_id IS NOT NULL AND rescreen_impact IS NOT NULL)
            ),
            CONSTRAINT ck_rule_bundle_event_impact_document CHECK (
                rescreen_impact IS NULL OR (
                    tradesieve_valid_rescreen_impact(rescreen_impact) AND
                    ((previous_tenant_id IS NULL AND
                      rescreen_impact -> 'previous_bundle' = 'null'::jsonb) OR
                     (previous_tenant_id IS NOT NULL AND
                      rescreen_impact -> 'previous_bundle' ->> 'bundle_id' =
                          previous_bundle_id AND
                      rescreen_impact -> 'previous_bundle' ->> 'version' =
                          previous_version AND
                      rescreen_impact -> 'previous_bundle' ->> 'content_hash' =
                          previous_content_hash)) AND
                    ((new_tenant_id IS NULL AND
                      rescreen_impact -> 'new_bundle' = 'null'::jsonb) OR
                     (new_tenant_id IS NOT NULL AND
                      rescreen_impact -> 'new_bundle' ->> 'bundle_id' =
                          new_bundle_id AND
                      rescreen_impact -> 'new_bundle' ->> 'version' = new_version AND
                      rescreen_impact -> 'new_bundle' ->> 'content_hash' =
                          new_content_hash))
                )
            ),
            CONSTRAINT fk_rule_bundle_event_previous FOREIGN KEY
                (previous_tenant_id, previous_deployment_id,
                 previous_rule_set_id, previous_bundle_id, previous_version,
                 previous_content_hash)
                REFERENCES rule_bundle_version
                (tenant_id, deployment_id, rule_set_id, bundle_id, version,
                 content_hash) MATCH FULL,
            CONSTRAINT fk_rule_bundle_event_new FOREIGN KEY
                (new_tenant_id, new_deployment_id, new_rule_set_id,
                 new_bundle_id, new_version, new_content_hash)
                REFERENCES rule_bundle_version
                (tenant_id, deployment_id, rule_set_id, bundle_id, version,
                content_hash) MATCH FULL
        );

        CREATE TABLE authorization_audit_event (
            event_id VARCHAR(128) PRIMARY KEY,
            occurred_at TIMESTAMPTZ NOT NULL,
            actor_subject VARCHAR(128) NOT NULL,
            client_id VARCHAR(256) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            actor_tenant_id VARCHAR(128) NOT NULL,
            request_tenant_id VARCHAR(128) NOT NULL,
            target_tenant_id VARCHAR(128) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            target_ref VARCHAR(257) NOT NULL,
            correlation_id VARCHAR(128) NOT NULL,
            outcome VARCHAR(16) NOT NULL,
            reason VARCHAR(64) NOT NULL,
            CONSTRAINT ck_authorization_audit_ids CHECK (
                event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                actor_subject ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                actor_tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                request_tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                target_tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_authorization_audit_text CHECK (
                length(client_id) BETWEEN 1 AND 256 AND
                client_id = btrim(client_id) AND client_id !~ '[[:space:]]' AND
                client_id !~ '[[:cntrl:]]' AND
                target_ref ~
                    '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_authorization_audit_actor CHECK (
                actor_type IN ('HUMAN', 'SERVICE', 'AGENT')
            ),
            CONSTRAINT ck_authorization_audit_operation CHECK (
                operation ~ '^[A-Z][A-Z0-9_]{0,127}$'
            ),
            CONSTRAINT ck_authorization_audit_outcome CHECK (
                outcome IN ('ALLOW', 'DENY')
            ),
            CONSTRAINT ck_authorization_audit_reason CHECK (
                reason IN (
                    'ALLOWED', 'CROSS_TENANT', 'OBJECT_NOT_VISIBLE',
                    'ENTITLEMENT_CHECK_FAILED', 'POLICY_MISSING',
                    'MISSING_SCOPE', 'ACTOR_TYPE_DENIED', 'ROLE_DENIED',
                    'CASE_STATE_DENIED', 'CLEARANCE_NOT_ELIGIBLE',
                    'DECISION_CONTEXT_MISMATCH', 'FOUR_EYES_DENIED',
                    'AUTHOR_APPROVER_SEPARATION_DENIED'
                )
            ),
            CONSTRAINT ck_authorization_audit_outcome_reason CHECK (
                (outcome = 'ALLOW') = (reason = 'ALLOWED')
            )
        );

        CREATE TABLE rule_bundle_command_audit (
            command_event_id VARCHAR(128) PRIMARY KEY,
            authorization_event_id VARCHAR(128) NOT NULL,
            lifecycle_event_id VARCHAR(128),
            tenant_id VARCHAR(128) NOT NULL,
            deployment_id VARCHAR(128) NOT NULL,
            rule_set_id VARCHAR(128) NOT NULL,
            bundle_id VARCHAR(128) NOT NULL,
            bundle_version VARCHAR(32) NOT NULL,
            bundle_content_hash VARCHAR(71) NOT NULL,
            target_type VARCHAR(128) NOT NULL,
            target_id VARCHAR(128) NOT NULL,
            actor_id VARCHAR(128) NOT NULL,
            actor_type VARCHAR(16) NOT NULL,
            operation VARCHAR(128) NOT NULL,
            outcome VARCHAR(16) NOT NULL,
            reason VARCHAR(64) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_rule_bundle_audit_lifecycle UNIQUE (lifecycle_event_id),
            CONSTRAINT fk_rule_bundle_audit_authorization FOREIGN KEY
                (authorization_event_id)
                REFERENCES authorization_audit_event(event_id),
            CONSTRAINT fk_rule_bundle_audit_lifecycle FOREIGN KEY
                (lifecycle_event_id) REFERENCES rule_bundle_lifecycle_event(event_id),
            CONSTRAINT ck_rule_bundle_audit_ids CHECK (
                command_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                authorization_event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                deployment_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                rule_set_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                bundle_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                target_type ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                target_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' AND
                actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
            CONSTRAINT ck_rule_bundle_audit_version_hash CHECK (
                bundle_version ~ '^(0|[1-9][0-9]{0,8})[.](0|[1-9][0-9]{0,8})[.](0|[1-9][0-9]{0,8})$' AND
                bundle_content_hash ~ '^sha256:[a-f0-9]{64}$'
            ),
            CONSTRAINT ck_rule_bundle_audit_actor CHECK (
                actor_type IN ('HUMAN', 'SERVICE', 'AGENT')
            ),
            CONSTRAINT ck_rule_bundle_audit_operation CHECK (
                operation ~ '^[A-Z][A-Z0-9_]{0,127}$'
            ),
            CONSTRAINT ck_rule_bundle_audit_outcome CHECK (
                outcome IN ('SUCCESS', 'FAILURE')
            ),
            CONSTRAINT ck_rule_bundle_audit_reason CHECK (
                reason IN (
                    'DRAFT_APPLIED', 'DRAFT_IDEMPOTENT', 'APPROVED',
                    'APPROVE_IDEMPOTENT', 'ACTIVATED', 'ACTIVATE_IDEMPOTENT',
                    'RETIRED', 'RETIRE_IDEMPOTENT', 'ROLLED_BACK',
                    'ROLLBACK_IDEMPOTENT', 'DRAFT_CONFLICT', 'NOT_FOUND',
                    'AUTHORIZATION_BINDING_DENIED', 'AUTHOR_SEPARATION_DENIED',
                    'GOVERNANCE_BLOCKED', 'OFFICIAL_CITATION_UNVERIFIED',
                    'INVALID_LIFECYCLE', 'PERSISTENCE_FAILURE'
                )
            ),
            CONSTRAINT ck_rule_bundle_audit_outcome_reason CHECK (
                (outcome = 'SUCCESS' AND reason IN (
                    'DRAFT_APPLIED', 'DRAFT_IDEMPOTENT', 'APPROVED',
                    'APPROVE_IDEMPOTENT', 'ACTIVATED', 'ACTIVATE_IDEMPOTENT',
                    'RETIRED', 'RETIRE_IDEMPOTENT', 'ROLLED_BACK',
                    'ROLLBACK_IDEMPOTENT'
                )) OR
                (outcome = 'FAILURE' AND reason NOT IN (
                    'DRAFT_APPLIED', 'DRAFT_IDEMPOTENT', 'APPROVED',
                    'APPROVE_IDEMPOTENT', 'ACTIVATED', 'ACTIVATE_IDEMPOTENT',
                    'RETIRED', 'RETIRE_IDEMPOTENT', 'ROLLED_BACK',
                    'ROLLBACK_IDEMPOTENT'
                ))
            ),
            CONSTRAINT ck_rule_bundle_audit_lifecycle_link CHECK (
                (reason IN ('DRAFT_APPLIED', 'APPROVED', 'ACTIVATED', 'RETIRED',
                            'ROLLED_BACK')) = (lifecycle_event_id IS NOT NULL)
            )
        );

        CREATE FUNCTION tradesieve_validate_rule_bundle_audit_link()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            linked_authorization authorization_audit_event%ROWTYPE;
            linked rule_bundle_lifecycle_event%ROWTYPE;
            expected_reason TEXT;
            linked_bundle_id TEXT;
            linked_version TEXT;
            linked_hash TEXT;
        BEGIN
            SELECT * INTO linked_authorization FROM authorization_audit_event
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
            SELECT * INTO linked FROM rule_bundle_lifecycle_event
                WHERE event_id = NEW.lifecycle_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'linked lifecycle event does not exist'
                    USING ERRCODE = '23503';
            END IF;
            expected_reason := CASE linked.event_type
                WHEN 'DRAFTED' THEN 'DRAFT_APPLIED'
                WHEN 'APPROVED' THEN 'APPROVED'
                WHEN 'ACTIVATED' THEN 'ACTIVATED'
                WHEN 'RETIRED' THEN 'RETIRED'
                WHEN 'ROLLED_BACK' THEN 'ROLLED_BACK'
            END;
            linked_bundle_id := COALESCE(linked.new_bundle_id,
                                         linked.previous_bundle_id);
            linked_version := COALESCE(linked.new_version, linked.previous_version);
            linked_hash := COALESCE(linked.new_content_hash,
                                    linked.previous_content_hash);
            IF NEW.outcome <> 'SUCCESS' OR NEW.reason <> expected_reason OR
               NEW.tenant_id <> linked.tenant_id OR
               NEW.deployment_id <> linked.deployment_id OR
               NEW.rule_set_id <> linked.rule_set_id OR
               NEW.actor_id <> linked.actor_id OR
               NEW.actor_type <> linked.actor_type OR
               NEW.occurred_at <> linked.occurred_at OR
               NEW.bundle_id <> linked_bundle_id OR
               NEW.bundle_version <> linked_version OR
               NEW.bundle_content_hash <> linked_hash THEN
                RAISE EXCEPTION 'command audit does not match lifecycle event'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE FUNCTION tradesieve_require_rule_bundle_event_audit()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM rule_bundle_command_audit
                WHERE lifecycle_event_id = NEW.event_id
            ) THEN
                RAISE EXCEPTION 'lifecycle event requires one command audit'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;

        CREATE INDEX ix_rule_bundle_event_scope_time
            ON rule_bundle_lifecycle_event
            (tenant_id, deployment_id, rule_set_id, occurred_at, sequence);
        CREATE INDEX ix_rule_bundle_audit_scope_time
            ON rule_bundle_command_audit
            (tenant_id, deployment_id, rule_set_id, occurred_at, command_event_id);
        CREATE INDEX ix_authorization_audit_actor_time
            ON authorization_audit_event
            (actor_tenant_id, actor_subject, occurred_at, event_id);

        CREATE TRIGGER trg_rule_bundle_version_immutable
            BEFORE UPDATE OR DELETE ON rule_bundle_version
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_rule_bundle_event_immutable
            BEFORE UPDATE OR DELETE ON rule_bundle_lifecycle_event
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_rule_bundle_audit_immutable
            BEFORE UPDATE OR DELETE ON rule_bundle_command_audit
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_authorization_audit_immutable
            BEFORE UPDATE OR DELETE ON authorization_audit_event
            FOR EACH ROW EXECUTE FUNCTION tradesieve_reject_immutable_change();
        CREATE TRIGGER trg_rule_bundle_audit_link
            BEFORE INSERT ON rule_bundle_command_audit
            FOR EACH ROW EXECUTE FUNCTION tradesieve_validate_rule_bundle_audit_link();
        CREATE CONSTRAINT TRIGGER trg_rule_bundle_event_requires_audit
            AFTER INSERT ON rule_bundle_lifecycle_event
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tradesieve_require_rule_bundle_event_audit();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE rule_bundle_command_audit;
        DROP FUNCTION tradesieve_validate_rule_bundle_audit_link();
        DROP TABLE authorization_audit_event;
        DROP TRIGGER trg_rule_bundle_event_requires_audit
            ON rule_bundle_lifecycle_event;
        DROP FUNCTION tradesieve_require_rule_bundle_event_audit();
        DROP TABLE rule_bundle_lifecycle_event;
        DROP TABLE rule_bundle_lifecycle_state;
        DROP TABLE rule_bundle_version;
        DROP FUNCTION tradesieve_valid_rescreen_impact(JSONB);
        DROP FUNCTION tradesieve_valid_rule_fact_array(JSONB);
        DROP FUNCTION tradesieve_valid_rule_id_array(JSONB);
        DROP FUNCTION tradesieve_valid_rule_bundle_ref(JSONB);
        DROP FUNCTION tradesieve_reject_immutable_change();
        """
    )
