#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

compose_project="${TS102_COMPOSE_PROJECT:-tradesieve-ts102-$$}"
host_port="${TS102_HOST_PORT:-18080}"
pull_timeout_seconds="${TS102_PULL_TIMEOUT_SECONDS:-600}"
build_timeout_seconds="${TS102_BUILD_TIMEOUT_SECONDS:-600}"
production_probe_database_url="postgresql://service:strong_password@postgres:5432/tradesieve" # pragma: allowlist secret
compose=(docker compose --env-file .env.example -p "$compose_project")

run_bounded() {
  local timeout_seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout --foreground "${timeout_seconds}s" "$@"
  else
    perl -e 'alarm shift @ARGV; exec @ARGV or die "exec failed: $!\n"' \
      "$timeout_seconds" "$@"
  fi
}

assert_source_listing() {
  local expected_status="$1"
  local expected_ready="$2"
  local expected_exit="$3"
  local output
  local actual_exit
  if output="$("${compose[@]}" run --rm --no-deps app \
    python -m tradesieve.manage list-sources)"; then
    actual_exit=0
  else
    actual_exit=$?
  fi
  if [[ "$actual_exit" != "$expected_exit" ]]; then
    echo "list-sources exit $actual_exit; expected $expected_exit" >&2
    exit 1
  fi
  python -c '
import json
import sys

expected_status, expected_ready = sys.argv[1], sys.argv[2] == "true"
payload = json.load(sys.stdin)
expected_keys = {
    "deployment_id", "source_set_id", "ready", "status", "issues", "sources"
}
assert set(payload) == expected_keys, payload
assert payload["ready"] is expected_ready, payload
assert payload["status"] == expected_status, payload
assert payload["sources"], payload
assert payload["sources"][0]["status"] == expected_status, payload
assert payload["sources"][0]["required"] is True, payload
assert [item["source_id"] for item in payload["issues"]] == sorted(
    item["source_id"] for item in payload["issues"]
), payload
if expected_ready:
    assert payload["issues"] == [], payload
else:
    assert payload["issues"] == [
        {"source_id": "synthetic-source-v1", "status": expected_status}
    ], payload
serialized = json.dumps(payload)
assert "credential_secret_ref" not in serialized, serialized
assert "contractual_constraints" not in serialized, serialized
' "$expected_status" "$expected_ready" <<<"$output"
}

assert_rule_listing() {
  local output
  output="$("${compose[@]}" run --rm --no-deps app \
    python -m tradesieve.manage list-rules)"
  python -c '
import json
import sys

payload = json.load(sys.stdin)
assert set(payload) == {"active_bundle", "status"}, payload
assert payload["status"] == "ACTIVE", payload
active = payload["active_bundle"]
assert set(active) == {"summary", "rules"}, active
summary = active["summary"]
assert set(summary) == {
    "tenant_id", "deployment_id", "rule_set_id", "bundle_id", "version",
    "content_hash", "owner", "authored_by", "effective_from",
    "effective_until", "state", "active", "rule_count",
}, summary
assert summary["tenant_id"] == "demo-tenant", summary
assert summary["deployment_id"] == "demo", summary
assert summary["rule_set_id"] == "synthetic-demo-rules-v1", summary
assert summary["bundle_id"] == "synthetic-demo-bundle-v1", summary
assert summary["version"] == "1.0.0", summary
assert summary["state"] == "ACTIVE" and summary["active"] is True, summary
assert summary["effective_until"] is None, summary
assert summary["rule_count"] == 2, summary
rules = active["rules"]
assert [rule["rule_id"] for rule in rules] == [
    "synthetic-data-completeness", "synthetic-legal-nexus"
], rules
assert all(rule["fixture_count"] == 2 for rule in rules), rules
assert all(rule["effective_until"] is None for rule in rules), rules
assert all(len(rule["citations"]) == 1 for rule in rules), rules
assert all(
    citation["kind"] == "INTERNAL_POLICY"
    and citation["policy_content_hash"].startswith("sha256:")
    and citation["source_id"] is None
    and citation["snapshot_id"] is None
    for rule in rules
    for citation in rule["citations"]
), rules
serialized = json.dumps(payload).lower()
for forbidden in (
    "private_policy_text", "internal_notes", "synthetic fixture text",
    "human_cleared", "not legal advice",
):
    assert forbidden not in serialized, forbidden
' <<<"$output"
}

assert_rule_counts() {
  local expected_authorizations="$1"
  local counts
  counts="$("${compose[@]}" exec -T postgres \
    psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
    "SELECT (SELECT count(*) FROM rule_bundle_version),
            (SELECT count(*) FROM rule_bundle_lifecycle_event),
            (SELECT count(*) FROM rule_bundle_command_audit),
            (SELECT count(*) FROM authorization_audit_event),
            (SELECT count(*) FROM runtime_coverage
             WHERE coverage_kind = 'RULE'
                OR coverage_id = 'synthetic-demo-rules-v1')")"
  if [[ "$counts" != "1|3|3|${expected_authorizations}|0" ]]; then
    echo "unexpected rule persistence counts: $counts" >&2
    exit 1
  fi
}

assert_readiness_unavailable() {
  local scenario="$1"
  local readiness_code
  readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${host_port}/health/ready")"
  if [[ "$readiness_code" != "503" ]]; then
    echo "$scenario did not make readiness fail closed: $readiness_code" >&2
    exit 1
  fi
}

assert_readiness_ready() {
  local scenario="$1"
  local readiness_code
  readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${host_port}/health/ready")"
  if [[ "$readiness_code" != "200" ]]; then
    echo "$scenario did not restore readiness: $readiness_code" >&2
    exit 1
  fi
}

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

export TRADESIEVE_HOST_PORT="$host_port"

run_bounded "$pull_timeout_seconds" "${compose[@]}" pull postgres
run_bounded "$build_timeout_seconds" "${compose[@]}" build

python_version="$("${compose[@]}" run --rm --no-deps app python --version)"
if [[ "$python_version" != "Python 3.13.14" ]]; then
  echo "unexpected container Python version: $python_version" >&2
  exit 1
fi
uv_version="$("${compose[@]}" run --rm --no-deps app uv --version)"
read -r uv_name uv_semver _ <<<"$uv_version"
if [[ "$uv_name" != "uv" || "$uv_semver" != "0.12.1" ]]; then
  echo "unexpected container uv version: $uv_version" >&2
  exit 1
fi
container_uid="$("${compose[@]}" run --rm --no-deps app python -c \
  'import os; print(os.getuid())')"
if [[ "$container_uid" != "10001" ]]; then
  echo "unexpected application UID: $container_uid" >&2
  exit 1
fi
if "${compose[@]}" run --rm --no-deps app python -c \
  'from pathlib import Path; Path("/app/.write-probe").touch()' \
  >/dev/null 2>&1; then
  echo "read-only application container unexpectedly wrote to /app" >&2
  exit 1
fi

"${compose[@]}" up -d --wait postgres

server_version="$("${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  --tuples-only --no-align -c 'SHOW server_version')"
if [[ "$server_version" != "18.4" ]]; then
  echo "unexpected PostgreSQL server version: $server_version" >&2
  exit 1
fi
psql_version="$("${compose[@]}" exec -T postgres psql --version)"
if [[ "$psql_version" != "psql (PostgreSQL) 18.4" ]]; then
  echo "unexpected psql version: $psql_version" >&2
  exit 1
fi

if "${compose[@]}" run --rm --no-deps app python -m tradesieve.manage inspect; then
  echo "inspect unexpectedly passed before migrations" >&2
  exit 1
fi

if TRADESIEVE_MODE=production "${compose[@]}" run --rm --no-deps app \
  python -m tradesieve.manage inspect; then
  echo "unsafe production defaults unexpectedly passed validation" >&2
  exit 1
fi

"${compose[@]}" up -d --wait

published_port="$("${compose[@]}" port app 8080)"
if [[ "$published_port" != "127.0.0.1:${host_port}" ]]; then
  echo "unexpected app port mapping: $published_port" >&2
  exit 1
fi

for service in postgres app worker; do
  container_id="$("${compose[@]}" ps -q "$service")"
  health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id")"
  if [[ "$health" != "healthy" ]]; then
    echo "$service is not healthy: $health" >&2
    exit 1
  fi
done

curl --fail --silent --show-error "http://127.0.0.1:${host_port}/health/live" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:${host_port}/health/ready" >/dev/null
"${compose[@]}" run --rm --no-deps app python -m tradesieve.manage inspect
assert_source_listing "CURRENT" "true" "0"
assert_rule_counts "4"
assert_rule_listing
assert_rule_counts "5"

"${compose[@]}" run --rm --no-deps bootstrap-demo
assert_rule_counts "6"
assert_rule_listing
assert_rule_counts "7"

audit_projection="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT string_agg(
      event.event_type || ':' || event.actor_id || ':' || command.reason || ':' ||
      command.operation || ':' || auth_event.outcome,
      ',' ORDER BY event.sequence
    ) || '|' || bool_and(
      auth_event.event_id = command.authorization_event_id AND
      auth_event.actor_subject = command.actor_id AND
      auth_event.actor_type = command.actor_type AND
      auth_event.actor_tenant_id = command.tenant_id AND
      auth_event.request_tenant_id = command.tenant_id AND
      auth_event.target_tenant_id = command.tenant_id AND
      auth_event.operation = command.operation AND
      auth_event.target_ref = command.target_type || ':' || command.target_id
    )
   FROM rule_bundle_lifecycle_event AS event
   JOIN rule_bundle_command_audit AS command
     ON command.lifecycle_event_id = event.event_id
   JOIN authorization_audit_event AS auth_event
     ON auth_event.event_id = command.authorization_event_id")"
expected_audit_projection="DRAFTED:demo-policy-author:DRAFT_APPLIED:POLICY_DRAFT:ALLOW,APPROVED:demo-policy-approver:APPROVED:POLICY_APPROVE:ALLOW,ACTIVATED:demo-policy-approver:ACTIVATED:POLICY_ACTIVATE:ALLOW|true"
if [[ "$audit_projection" != "$expected_audit_projection" ]]; then
  echo "unexpected linked rule audit projection: $audit_projection" >&2
  exit 1
fi

if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "UPDATE rule_bundle_version SET payload = payload" >/dev/null 2>&1; then
  echo "immutable rule bundle accepted an update" >&2
  exit 1
fi
if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "UPDATE rule_bundle_lifecycle_event SET reason = reason" >/dev/null 2>&1; then
  echo "immutable lifecycle event accepted an update" >&2
  exit 1
fi
if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "UPDATE rule_bundle_command_audit SET reason = reason" >/dev/null 2>&1; then
  echo "immutable command audit accepted an update" >&2
  exit 1
fi
if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "UPDATE authorization_audit_event SET reason = reason" >/dev/null 2>&1; then
  echo "immutable authorization audit accepted an update" >&2
  exit 1
fi

if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "INSERT INTO rule_bundle_command_audit (
      command_event_id, authorization_event_id, lifecycle_event_id, tenant_id,
      deployment_id, rule_set_id, bundle_id, bundle_version,
      bundle_content_hash, target_type, target_id, actor_id, actor_type,
      operation, outcome, reason, occurred_at
    )
    SELECT 'constraint-missing-auth', 'constraint-missing-auth', NULL, tenant_id,
      deployment_id, rule_set_id, bundle_id, bundle_version,
      bundle_content_hash, target_type, target_id, actor_id, actor_type,
      operation, 'SUCCESS', 'DRAFT_IDEMPOTENT', CURRENT_TIMESTAMP
    FROM rule_bundle_command_audit ORDER BY occurred_at LIMIT 1" \
  >/dev/null 2>&1; then
  echo "command audit accepted a missing authorization event" >&2
  exit 1
fi

if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "BEGIN;
   INSERT INTO authorization_audit_event (
      event_id, occurred_at, actor_subject, client_id, actor_type,
      actor_tenant_id, request_tenant_id, target_tenant_id, operation,
      target_ref, correlation_id, outcome, reason
    )
    SELECT 'constraint-auth-mismatch', CURRENT_TIMESTAMP, 'wrong-demo-actor',
      auth_event.client_id, auth_event.actor_type,
      auth_event.actor_tenant_id, auth_event.request_tenant_id,
      auth_event.target_tenant_id, auth_event.operation,
      auth_event.target_ref, 'constraint-auth-mismatch', 'ALLOW', 'ALLOWED'
    FROM authorization_audit_event AS auth_event
    JOIN rule_bundle_command_audit AS command
      ON command.authorization_event_id = auth_event.event_id
    ORDER BY command.occurred_at LIMIT 1;
   INSERT INTO rule_bundle_command_audit (
      command_event_id, authorization_event_id, lifecycle_event_id, tenant_id,
      deployment_id, rule_set_id, bundle_id, bundle_version,
      bundle_content_hash, target_type, target_id, actor_id, actor_type,
      operation, outcome, reason, occurred_at
    )
    SELECT 'constraint-command-mismatch', 'constraint-auth-mismatch', NULL,
      tenant_id, deployment_id, rule_set_id, bundle_id, bundle_version,
      bundle_content_hash, target_type, target_id, actor_id, actor_type,
      operation, 'SUCCESS', 'DRAFT_IDEMPOTENT', CURRENT_TIMESTAMP
    FROM rule_bundle_command_audit ORDER BY occurred_at LIMIT 1;
   COMMIT;" >/dev/null 2>&1; then
  echo "command audit accepted mismatched authorization semantics" >&2
  exit 1
fi

if "${compose[@]}" exec -T postgres \
  psql -X -v ON_ERROR_STOP=1 -U tradesieve -d tradesieve -c \
  "BEGIN;
   INSERT INTO rule_bundle_lifecycle_event (
      tenant_id, deployment_id, rule_set_id, sequence, event_id, event_type,
      new_tenant_id, new_deployment_id, new_rule_set_id, new_bundle_id,
      new_version, new_content_hash, reason, actor_id, actor_type, occurred_at
    )
    SELECT tenant_id, deployment_id, rule_set_id, 4,
      'constraint-event-without-audit', 'DRAFTED', tenant_id, deployment_id,
      rule_set_id, bundle_id, version, content_hash,
      'Synthetic deferred-audit constraint probe.', 'demo-policy-author',
      'HUMAN', CURRENT_TIMESTAMP
    FROM rule_bundle_version LIMIT 1;
   COMMIT;" >/dev/null 2>&1; then
  echo "lifecycle event committed without exactly one command audit" >&2
  exit 1
fi

"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "BEGIN;
   UPDATE rule_bundle_lifecycle_state
   SET active_tenant_id = NULL, active_deployment_id = NULL,
       active_rule_set_id = NULL, active_bundle_id = NULL,
       active_version = NULL, active_content_hash = NULL
   WHERE tenant_id = 'demo-tenant' AND deployment_id = 'demo'
     AND rule_set_id = 'synthetic-demo-rules-v1';
   COMMIT;" >/dev/null
assert_readiness_unavailable "absent active rule pointer"
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "BEGIN;
   UPDATE rule_bundle_lifecycle_state AS state
   SET active_tenant_id = bundle.tenant_id,
       active_deployment_id = bundle.deployment_id,
       active_rule_set_id = bundle.rule_set_id,
       active_bundle_id = bundle.bundle_id,
       active_version = bundle.version,
       active_content_hash = bundle.content_hash
   FROM rule_bundle_version AS bundle
   WHERE state.tenant_id = bundle.tenant_id
     AND state.deployment_id = bundle.deployment_id
     AND state.rule_set_id = bundle.rule_set_id
     AND state.tenant_id = 'demo-tenant'
     AND state.deployment_id = 'demo'
     AND state.rule_set_id = 'synthetic-demo-rules-v1';
   COMMIT;" >/dev/null
assert_readiness_ready "active rule pointer repair"

"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "BEGIN;
   UPDATE rule_bundle_lifecycle_state SET last_sequence = 2
   WHERE tenant_id = 'demo-tenant' AND deployment_id = 'demo'
     AND rule_set_id = 'synthetic-demo-rules-v1';
   COMMIT;" >/dev/null
assert_readiness_unavailable "corrupt rule lifecycle projection"
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "BEGIN;
   UPDATE rule_bundle_lifecycle_state SET last_sequence = 3
   WHERE tenant_id = 'demo-tenant' AND deployment_id = 'demo'
     AND rule_set_id = 'synthetic-demo-rules-v1';
   COMMIT;" >/dev/null
assert_readiness_ready "rule lifecycle projection repair"

"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "BEGIN;
   ALTER TABLE rule_bundle_version
     DISABLE TRIGGER trg_rule_bundle_version_immutable;
   UPDATE rule_bundle_version
     SET payload = payload || '{\"__corruption_probe\": true}'::jsonb;
   ALTER TABLE rule_bundle_version
     ENABLE TRIGGER trg_rule_bundle_version_immutable;
   COMMIT;" >/dev/null
assert_readiness_unavailable "corrupt active rule content"
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "BEGIN;
   ALTER TABLE rule_bundle_version
     DISABLE TRIGGER trg_rule_bundle_version_immutable;
   UPDATE rule_bundle_version SET payload = payload - '__corruption_probe';
   ALTER TABLE rule_bundle_version
     ENABLE TRIGGER trg_rule_bundle_version_immutable;
   COMMIT;" >/dev/null
immutable_trigger_state="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT tgenabled FROM pg_trigger
   WHERE tgrelid = 'rule_bundle_version'::regclass
     AND tgname = 'trg_rule_bundle_version_immutable'")"
if [[ "$immutable_trigger_state" != "O" ]]; then
  echo "rule bundle immutable trigger was not restored: $immutable_trigger_state" >&2
  exit 1
fi
assert_readiness_ready "active rule content repair"

production_rule_output=""
if production_rule_output="$(
  TRADESIEVE_MODE=production \
  TRADESIEVE_DATABASE_URL="$production_probe_database_url" \
  TRADESIEVE_DEMO_BOOTSTRAP_ENABLED=false \
  TRADESIEVE_RULE_BUNDLE_TENANT_ID=tenant-1 \
  TRADESIEVE_DEPLOYMENT_ID=production-1 \
  TRADESIEVE_REQUIRED_SOURCE_SET=approved-sources-v1 \
  TRADESIEVE_REQUIRED_RULE_SET=approved-rules-v1 \
  "${compose[@]}" run --rm --no-deps app \
  python -m tradesieve.manage list-rules
)"; then
  echo "production list-rules unexpectedly succeeded" >&2
  exit 1
else
  production_rule_exit=$?
fi
if [[ "$production_rule_exit" != "3" || \
      "$production_rule_output" != '{"active_bundle": null, "status": "DISABLED"}' ]]; then
  echo "unexpected production list-rules boundary: exit=$production_rule_exit output=$production_rule_output" >&2
  exit 1
fi

"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "INSERT INTO source_set_manifest (deployment_id, source_set_id, required_source_ids) VALUES ('constraint-probe', 'mixed-id-order', '[\"A-source\", \"a-source\", \"a.source\", \"a:source\"]'::jsonb)" >/dev/null
if "${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "INSERT INTO source_set_manifest (deployment_id, source_set_id, required_source_ids) VALUES ('constraint-probe', 'invalid-order', '[\"a-source\", \"A-source\"]'::jsonb)" \
  >/dev/null 2>&1; then
  echo "source manifest constraint accepted non-C ordering" >&2
  exit 1
fi
"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "DELETE FROM source_set_manifest WHERE deployment_id = 'constraint-probe'" >/dev/null

"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "CREATE TABLE ts102_persistence_probe (marker TEXT PRIMARY KEY); INSERT INTO ts102_persistence_probe VALUES ('survives-recreate')" >/dev/null
"${compose[@]}" up -d --force-recreate --wait postgres
persistence_marker="$("${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  --tuples-only --no-align -c "SELECT marker FROM ts102_persistence_probe")"
if [[ "$persistence_marker" != "survives-recreate" ]]; then
  echo "PostgreSQL data did not survive forced container recreation" >&2
  exit 1
fi

"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "UPDATE source_runtime_observation SET retrieved_at = CURRENT_TIMESTAMP - INTERVAL '3 hours', observed_at = CURRENT_TIMESTAMP WHERE deployment_id = 'demo' AND source_id = 'synthetic-source-v1'" >/dev/null
readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${host_port}/health/ready")"
if [[ "$readiness_code" != "503" ]]; then
  echo "stale required source did not fail closed: $readiness_code" >&2
  exit 1
fi
assert_source_listing "STALE" "false" "2"

"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "UPDATE source_runtime_observation SET availability = 'UNAVAILABLE', observed_at = CURRENT_TIMESTAMP WHERE deployment_id = 'demo' AND source_id = 'synthetic-source-v1'" >/dev/null
readiness_payload="$(curl --silent --output - \
  "http://127.0.0.1:${host_port}/health/ready")"
readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${host_port}/health/ready")"
if [[ "$readiness_code" != "503" ]]; then
  echo "unavailable required source did not fail closed" >&2
  exit 1
fi
python -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["status"] == "UNAVAILABLE", payload
assert payload["checks"]["required_source_coverage"] == "UNAVAILABLE", payload
assert "synthetic-source-v1" not in json.dumps(payload), payload
' <<<"$readiness_payload"
assert_source_listing "UNAVAILABLE" "false" "2"

"${compose[@]}" run --rm --no-deps bootstrap-demo
"${compose[@]}" stop postgres
curl --fail --silent --show-error "http://127.0.0.1:${host_port}/health/live" >/dev/null
readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${host_port}/health/ready")"
if [[ "$readiness_code" != "503" ]]; then
  echo "database outage did not make readiness fail closed: $readiness_code" >&2
  exit 1
fi

"${compose[@]}" start postgres
"${compose[@]}" up -d --wait
"${compose[@]}" down --volumes --remove-orphans

if [[ -n "$("${compose[@]}" ps -aq)" ]]; then
  echo "compose teardown left project containers behind" >&2
  exit 1
fi
if [[ -n "$(docker network ls --quiet --filter \
  "label=com.docker.compose.project=${compose_project}")" ]]; then
  echo "compose teardown left project networks behind" >&2
  exit 1
fi
if [[ -n "$(docker volume ls --quiet --filter \
  "label=com.docker.compose.project=${compose_project}")" ]]; then
  echo "compose teardown left project volumes behind" >&2
  exit 1
fi

trap - EXIT
echo "Compose reference deployment checks passed for project $compose_project."
