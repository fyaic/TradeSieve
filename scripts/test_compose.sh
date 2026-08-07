#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

compose_project="${TS202_D2B_COMPOSE_PROJECT:-tradesieve-ts202-d2b-main-$$}"
fresh_project="${TS202_D2B_FRESH_PROJECT:-tradesieve-ts202-d2b-fresh-$$}"
host_port="${TS102_HOST_PORT:-18080}"
fresh_host_port="${TS202_D2B_FRESH_HOST_PORT:-18081}"
pull_timeout_seconds="${TS102_PULL_TIMEOUT_SECONDS:-600}"
build_timeout_seconds="${TS102_BUILD_TIMEOUT_SECONDS:-600}"
production_probe_database_url="postgresql://service:strong_password@postgres:5432/tradesieve" # pragma: allowlist secret
compose=(docker compose --env-file .env.example -p "$compose_project")
fresh_compose=(docker compose --env-file .env.example -p "$fresh_project")

if [[ "$compose_project" == "$fresh_project" ]]; then
  echo "fresh and legacy/main Compose projects must be unique" >&2
  exit 1
fi

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

assert_snapshot_listing() {
  local project="$1"
  local expected_exit="$2"
  local output=""
  local actual_exit
  local -a stack=(docker compose --env-file .env.example -p "$project")
  if output="$("${stack[@]}" run --rm --no-deps app \
    python -m tradesieve.manage list-source-snapshots)"; then
    actual_exit=0
  else
    actual_exit=$?
  fi
  if [[ "$actual_exit" != "$expected_exit" ]]; then
    echo "$project list-source-snapshots exit $actual_exit; expected $expected_exit" >&2
    exit 1
  fi
  python -c '
import json
import sys

expected_exit = int(sys.argv[1])
payload = json.load(sys.stdin)
assert set(payload) == {"snapshots"}, payload
snapshots = payload["snapshots"]
if expected_exit == 0:
    assert len(snapshots) == 2, payload
    assert sum(item["active"] for item in snapshots) == 1, payload
    assert all(item["validation_passed"] is True for item in snapshots), payload
    assert all(item["snapshot_id"].startswith("snapshot-") for item in snapshots), payload
    assert all(item["raw_object_id"].startswith("raw-") for item in snapshots), payload
else:
    assert snapshots == [], payload
serialized = json.dumps(payload).lower()
for forbidden_key in (
    "private_payload", "native_value", "authorization_event_id",
    "command_event_id", "lifecycle_event_id", "actor_id", "actor_type",
    "reason", "original_name", "credential_secret_ref",
    "contractual_constraints", "raw_bytes", "records", "assertions",
    "native_locator", "normalized_value", "source_locator", "legal_scope",
    "licence_summary", "license", "data_scope", "access_method", "owner",
    "responsible_operator", "jurisdiction", "credentials", "contract",
    "path", "exception", "traceback", "error", "message",
):
    quoted_key = chr(34) + forbidden_key + chr(34)
    assert quoted_key not in serialized, (forbidden_key, payload)
for forbidden_value in (
    "synthetic alpha components", "synthetic alpha components updated",
    "synthetic beta logistics", "synthetic-only-0001",
    "synthetic-only-0002", "synthetic-entity-alpha",
    "synthetic-entity-beta", "synthetic-demo-source-v1.json",
    "synthetic-demo-source-v2.json", "tradesieve demo",
    "demo-source-operator", "synthetic screening behavior only",
    "synthetic entities with no production data",
    "synthetic demo fixture; no production use", "/var/lib/tradesieve",
    "permission denied", "traceback", "exception",
):
    assert forbidden_value not in serialized, (forbidden_value, payload)
' "$expected_exit" <<<"$output"
}

source_graph_counts() {
  local project="$1"
  docker compose --env-file .env.example -p "$project" exec -T postgres \
    psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
    "SELECT (SELECT count(*) FROM source_raw_object_metadata),
            (SELECT count(*) FROM source_parsed_snapshot),
            (SELECT count(*) FROM source_snapshot_validation_evidence),
            (SELECT count(*) FROM source_snapshot_lifecycle_event),
            (SELECT count(*) FROM source_snapshot_command_audit)"
}

assert_source_graph_counts() {
  local project="$1"
  local expected="$2"
  local actual
  actual="$(source_graph_counts "$project")"
  if [[ "$actual" != "$expected" ]]; then
    echo "$project source graph counts $actual; expected $expected" >&2
    exit 1
  fi
}

application_rollback() {
  local reason="$1"
  "${compose[@]}" run --rm --no-deps app python -c '
from datetime import UTC, datetime
import sys
from tradesieve.application.auth import Operation
from tradesieve.config import get_settings
from tradesieve.demo_source_snapshot import (
    DEMO_SOURCE_ID, DemoSourceActor, authorize_demo_source_request,
    demo_snapshot_identity,
)
from tradesieve.runtime import build_demo_source_services, connect
settings = get_settings()
with connect(settings) as connection:
    graph = build_demo_source_services(settings, connection)
    snapshots = graph.repository.list_snapshots(
        settings.deployment_id, DEMO_SOURCE_ID, limit=512
    )
    _, lifecycle = graph.repository.get_lifecycle_snapshot(
        settings.deployment_id, DEMO_SOURCE_ID
    )
    target = next(
        item for item in snapshots if item.reference() != lifecycle.active_snapshot
    )
    identity = demo_snapshot_identity(target)
    authorized = authorize_demo_source_request(
        settings, graph.authorization, actor=DemoSourceActor.APPROVER,
        operation=Operation.SOURCE_SNAPSHOT_ROLLBACK,
        now=datetime.now(UTC), identity=identity,
    )
    graph.service.rollback(
        authorized, source_id=DEMO_SOURCE_ID, identity=identity, reason=sys.argv[1]
    )
    print(target.snapshot_id)
' "$reason"
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

assert_live() {
  local scenario="$1"
  local live_code
  live_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${host_port}/health/live")"
  if [[ "$live_code" != "200" ]]; then
    echo "$scenario broke liveness: $live_code" >&2
    exit 1
  fi
}

active_raw_identity() {
  "${compose[@]}" exec -T postgres \
    psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
    "SELECT raw.object_id || '|' || substr(raw.content_hash, 8)
     FROM source_snapshot_lifecycle_state AS state
     JOIN source_parsed_snapshot AS snapshot
       ON snapshot.deployment_id = state.deployment_id
      AND snapshot.source_id = state.source_id
      AND snapshot.snapshot_id = state.active_snapshot_id
      AND snapshot.content_hash = state.active_snapshot_content_hash
     JOIN source_raw_object_metadata AS raw
       ON raw.deployment_id = snapshot.deployment_id
      AND raw.source_id = snapshot.source_id
      AND raw.object_id = snapshot.raw_object_id
     WHERE state.deployment_id = 'demo'
       AND state.source_id = 'synthetic-source-v1'"
}

raw_volume_fault() {
  local action="$1"
  local identity
  local object_id
  local digest
  identity="$(active_raw_identity)"
  IFS='|' read -r object_id digest <<<"$identity"
  if [[ -z "$object_id" || -z "$digest" ]]; then
    echo "could not resolve the active raw-object identity" >&2
    exit 1
  fi
  "${compose[@]}" run --rm --no-deps bootstrap-demo python -c '
import os
from pathlib import Path
import sys

action, object_id, digest = sys.argv[1:]
path = (
    Path("/var/lib/tradesieve/raw") / "objects" / digest[:2] / digest[2:4]
    / f"{object_id}.{digest}.raw"
)
missing = path.with_suffix(path.suffix + ".ts202-missing")
good = path.with_suffix(path.suffix + ".ts202-good")
if action == "remove":
    assert path.is_file() and not missing.exists()
    path.rename(missing)
elif action == "restore-remove":
    assert missing.is_file() and not path.exists()
    missing.rename(path)
elif action == "tamper":
    assert path.is_file() and not good.exists()
    original = path.read_bytes()
    assert original
    good.write_bytes(original)
    os.chmod(good, 0o600)
    changed = bytes([original[0] ^ 1]) + original[1:]
    assert len(changed) == len(original) and changed != original
    path.write_bytes(changed)
    os.chmod(path, 0o600)
elif action == "restore-tamper":
    assert good.is_file() and path.is_file()
    good.replace(path)
else:
    raise AssertionError(action)
' "$action" "$object_id" "$digest"
}

source_integrity_projection() {
  "${compose[@]}" exec -T postgres \
    psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
    "SELECT
       (SELECT count(*) FROM source_raw_object_metadata) || '|' ||
       (SELECT count(*) FROM source_parsed_snapshot) || '|' ||
       (SELECT count(*) FROM source_snapshot_validation_evidence) || '|' ||
       (SELECT count(*) FROM source_snapshot_lifecycle_event) || '|' ||
       (SELECT count(*) FROM source_snapshot_command_audit) || '|' ||
       (SELECT count(*) FROM authorization_audit_event) || '|' ||
       state.last_sequence || '|' || state.active_snapshot_id || '|' ||
       state.active_snapshot_content_hash || '|' ||
       observation.availability || '|' || observation.observed_at || '|' ||
       observation.active_snapshot_id || '|' || observation.retrieved_at || '|' ||
       COALESCE(observation.effective_from::text, '<null>')
     FROM source_snapshot_lifecycle_state AS state
     JOIN source_runtime_observation AS observation
       ON observation.deployment_id = state.deployment_id
      AND observation.source_id = state.source_id
     WHERE state.deployment_id = 'demo'
       AND state.source_id = 'synthetic-source-v1'"
}

assert_bootstrap_refuses_without_mutation() {
  local scenario="$1"
  local before
  local after
  before="$(source_integrity_projection)"
  if "${compose[@]}" run --rm --no-deps bootstrap-demo >/dev/null 2>&1; then
    echo "bootstrap unexpectedly repaired $scenario" >&2
    exit 1
  fi
  after="$(source_integrity_projection)"
  if [[ "$after" != "$before" ]]; then
    echo "bootstrap mutated counts, active pointer, or observation for $scenario" >&2
    exit 1
  fi
}

assert_project_zero_residue() {
  local project="$1"
  local kind
  local ids
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=${project}")"
  if [[ -n "$ids" ]]; then
    echo "$project teardown left containers behind: $ids" >&2
    return 1
  fi
  for kind in network volume; do
    ids="$(docker "$kind" ls -q \
      --filter "label=com.docker.compose.project=${project}")"
    if [[ -n "$ids" ]]; then
      echo "$project teardown left ${kind}s behind: $ids" >&2
      return 1
    fi
  done
}

cleanup() {
  local exit_code=$?
  trap - EXIT
  "${fresh_compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || exit_code=1
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || exit_code=1
  assert_project_zero_residue "$fresh_project" || exit_code=1
  assert_project_zero_residue "$compose_project" || exit_code=1
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

export TRADESIEVE_HOST_PORT="$host_port"

run_bounded "$pull_timeout_seconds" "${compose[@]}" pull postgres
run_bounded "$build_timeout_seconds" "${compose[@]}" build

image_raw_facts="$("${compose[@]}" run --rm --no-deps migrate python -c '
import os
import stat
from pathlib import Path
p = Path("/var/lib/tradesieve/raw")
s = p.stat()
print(f"{s.st_uid}|{s.st_gid}|{stat.S_IMODE(s.st_mode):04o}")
')"
if [[ "$image_raw_facts" != "10001|10001|0700" ]]; then
  echo "unexpected image raw root facts: $image_raw_facts" >&2
  exit 1
fi

"${fresh_compose[@]}" create migrate >/dev/null
migrate_probe_id="$("${fresh_compose[@]}" ps -q --all migrate)"
migrate_raw_mounts="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/var/lib/tradesieve/raw"}}{{.Destination}}{{end}}{{end}}' \
  "$migrate_probe_id")"
if [[ -n "$migrate_raw_mounts" ]]; then
  echo "migrate unexpectedly mounts the raw-object volume" >&2
  exit 1
fi
"${fresh_compose[@]}" rm -f migrate >/dev/null

"${fresh_compose[@]}" run --rm --no-deps bootstrap-demo python -c '
import os
import stat
from pathlib import Path
root = Path("/var/lib/tradesieve/raw")
s = root.stat()
assert (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)) == (10001, 10001, 0o700)
probe = root / ".bootstrap-write-probe"
fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.close(fd)
assert stat.S_IMODE(probe.stat().st_mode) == 0o600
probe.unlink()
'
for service in app worker; do
  if "${fresh_compose[@]}" run --rm --no-deps "$service" python -c \
    'from pathlib import Path; Path("/var/lib/tradesieve/raw/.ro-probe").touch()' \
    >/dev/null 2>&1; then
    echo "$service unexpectedly wrote to its read-only raw-object mount" >&2
    exit 1
  fi
done

# Fresh-project phase: prove a pristine full stack creates exact governed source
# evidence, real private bytes, a safe listing, and is idempotent on restart.
export TRADESIEVE_HOST_PORT="$fresh_host_port"
"${fresh_compose[@]}" up -d --wait
fresh_port_mapping="$("${fresh_compose[@]}" port app 8080)"
if [[ "$fresh_port_mapping" != "127.0.0.1:${fresh_host_port}" ]]; then
  echo "unexpected fresh app port mapping: $fresh_port_mapping" >&2
  exit 1
fi
curl --fail --silent --show-error \
  "http://127.0.0.1:${fresh_host_port}/health/ready" >/dev/null
assert_source_graph_counts "$fresh_project" "2|2|2|12|12"
"${fresh_compose[@]}" run --rm --no-deps app python -c '
import stat
from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.adapters.postgres_source_snapshot import PostgresSourceSnapshotRepository
from tradesieve.config import get_settings
from tradesieve.demo_source_snapshot import DEMO_SOURCE_ID
from tradesieve.runtime import connect
s = get_settings()
root = s.raw_object_root
root_stat = root.stat()
assert (root_stat.st_uid, root_stat.st_gid, stat.S_IMODE(root_stat.st_mode)) == (10001, 10001, 0o700)
with connect(s) as connection:
    repository = PostgresSourceSnapshotRepository(connection)
    snapshots = repository.list_snapshots(s.deployment_id, DEMO_SOURCE_ID, limit=512)
    assert len(snapshots) == 2
    store = LocalImmutableRawObjectStore(root)
    for snapshot in snapshots:
        metadata = repository.get_raw_metadata(s.deployment_id, DEMO_SOURCE_ID, snapshot.raw_object.object_id)
        assert metadata is not None
        content = store.get_verified(metadata.reference())
        assert len(content) == metadata.byte_length
files = tuple(path for path in root.rglob("*") if path.is_file())
assert len(files) == 2
assert all((p.stat().st_uid, p.stat().st_gid, stat.S_IMODE(p.stat().st_mode)) == (10001, 10001, 0o600) for p in files)
'
assert_snapshot_listing "$fresh_project" "0"
fresh_identity_before="$("${fresh_compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT string_agg(object_id || ':' || content_hash || ':' || byte_length, ',' ORDER BY object_id)
   FROM source_raw_object_metadata;
   SELECT string_agg(snapshot_id || ':' || content_hash, ',' ORDER BY snapshot_id)
   FROM source_parsed_snapshot;
   SELECT last_sequence || ':' || active_snapshot_id
   FROM source_snapshot_lifecycle_state WHERE deployment_id='demo';")"
fresh_counts_before="$(source_graph_counts "$fresh_project")"
"${fresh_compose[@]}" run --rm --no-deps bootstrap-demo
fresh_counts_after="$(source_graph_counts "$fresh_project")"
fresh_identity_after="$("${fresh_compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT string_agg(object_id || ':' || content_hash || ':' || byte_length, ',' ORDER BY object_id)
   FROM source_raw_object_metadata;
   SELECT string_agg(snapshot_id || ':' || content_hash, ',' ORDER BY snapshot_id)
   FROM source_parsed_snapshot;
   SELECT last_sequence || ':' || active_snapshot_id
   FROM source_snapshot_lifecycle_state WHERE deployment_id='demo';")"
if [[ "$fresh_counts_before" != "$fresh_counts_after" || \
      "$fresh_identity_before" != "$fresh_identity_after" ]]; then
  echo "fresh bootstrap repeat mutated governed source evidence" >&2
  exit 1
fi
"${fresh_compose[@]}" down --volumes --remove-orphans
assert_project_zero_residue "$fresh_project"

export TRADESIEVE_HOST_PORT="$host_port"

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

# Legacy/main phase: start at 0003 with the exact TS-201 registry row and prove
# unknown observations fail closed without writes. Only the exact old marker may
# be removed and replaced by the real 0004 source graph.
"${compose[@]}" run --rm --no-deps migrate python -c '
from alembic import command
from tradesieve.config import get_settings
from tradesieve.manage import migration_config
command.upgrade(migration_config(get_settings()), "20260806_0003")
'
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "INSERT INTO source_set_manifest
     (deployment_id, source_set_id, required_source_ids)
   VALUES ('demo', 'synthetic-demo-sources-v1',
           '[\"synthetic-source-v1\"]'::jsonb);
   INSERT INTO source_registry (
     deployment_id, source_set_id, source_id, name, owner,
     responsible_operator, jurisdiction, legal_scope, data_scope,
     access_method, credential_secret_ref, licence_summary,
     contractual_constraints, refresh_expectation_seconds,
     stale_after_seconds, active
   ) VALUES (
     'demo', 'synthetic-demo-sources-v1', 'synthetic-source-v1',
     'Synthetic source fixture', 'TradeSieve demo', 'demo-source-operator',
     'SYNTHETIC', 'Synthetic screening behavior only',
     'Synthetic entities with no production data', 'INTERNAL', NULL,
     'Synthetic demo fixture; no production use', NULL, 3600, 7200, TRUE
   );
   INSERT INTO source_runtime_observation (
     deployment_id, source_id, availability, observed_at,
     active_snapshot_id, retrieved_at, effective_from
   ) VALUES (
     'demo', 'synthetic-source-v1', 'AVAILABLE', CURRENT_TIMESTAMP,
     'unknown-marker', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
   );" >/dev/null
registration_projection="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT deployment_id, source_set_id, source_id, name, owner,
          responsible_operator, jurisdiction, legal_scope, data_scope,
          access_method, COALESCE(credential_secret_ref, '<null>'),
          licence_summary, COALESCE(contractual_constraints, '<null>'),
          refresh_expectation_seconds, stale_after_seconds, active
   FROM source_registry WHERE deployment_id='demo'")"
expected_registration_projection="demo|synthetic-demo-sources-v1|synthetic-source-v1|Synthetic source fixture|TradeSieve demo|demo-source-operator|SYNTHETIC|Synthetic screening behavior only|Synthetic entities with no production data|INTERNAL|<null>|Synthetic demo fixture; no production use|<null>|3600|7200|t"
if [[ "$registration_projection" != "$expected_registration_projection" ]]; then
  echo "legacy seeded registration is not exactly demo_source_registration" >&2
  exit 1
fi
unknown_marker_before="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT availability || '|' || active_snapshot_id || '|' ||
          observed_at::text || '|' || retrieved_at::text || '|' || effective_from::text
   FROM source_runtime_observation WHERE deployment_id='demo'")"
"${compose[@]}" run --rm --no-deps migrate
if "${compose[@]}" run --rm --no-deps bootstrap-demo >/dev/null 2>&1; then
  echo "unknown legacy marker unexpectedly bootstrapped" >&2
  exit 1
fi
unknown_marker_after="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT availability || '|' || active_snapshot_id || '|' ||
          observed_at::text || '|' || retrieved_at::text || '|' || effective_from::text
   FROM source_runtime_observation WHERE deployment_id='demo'")"
if [[ "$unknown_marker_after" != "$unknown_marker_before" ]]; then
  echo "unknown marker changed during fail-closed bootstrap" >&2
  exit 1
fi
legacy_failure_counts="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT (SELECT count(*) FROM source_raw_object_metadata),
          (SELECT count(*) FROM source_parsed_snapshot),
          (SELECT count(*) FROM source_snapshot_validation_evidence),
          (SELECT count(*) FROM source_snapshot_lifecycle_event),
          (SELECT count(*) FROM source_snapshot_command_audit),
          (SELECT count(*) FROM rule_bundle_version),
          (SELECT count(*) FROM rule_bundle_lifecycle_event),
          (SELECT count(*) FROM rule_bundle_command_audit),
          (SELECT count(*) FROM authorization_audit_event)")"
if [[ "$legacy_failure_counts" != "0|0|0|0|0|0|0|0|0" ]]; then
  echo "unknown marker bootstrap wrote governed state: $legacy_failure_counts" >&2
  exit 1
fi
"${compose[@]}" run --rm --no-deps migrate python -c '
from alembic import command
from tradesieve.config import get_settings
from tradesieve.manage import migration_config
command.downgrade(migration_config(get_settings()), "20260806_0003")
'
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "UPDATE source_runtime_observation
   SET active_snapshot_id='synthetic-snapshot-v1'
   WHERE deployment_id='demo' AND source_id='synthetic-source-v1';" >/dev/null
exact_marker="$("${compose[@]}" exec -T postgres psql -X -U tradesieve \
  -d tradesieve --tuples-only --no-align -c \
  "SELECT availability || '|' || active_snapshot_id || '|' ||
          (observed_at = retrieved_at AND retrieved_at = effective_from)::text
   FROM source_runtime_observation WHERE deployment_id='demo'")"
if [[ "$exact_marker" != "AVAILABLE|synthetic-snapshot-v1|true" ]]; then
  echo "failed to establish exact TS-201 legacy marker: $exact_marker" >&2
  exit 1
fi
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps bootstrap-demo
assert_source_graph_counts "$compose_project" "2|2|2|12|12"

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
assert_rule_counts "17"
assert_rule_listing
assert_rule_counts "18"

"${compose[@]}" run --rm --no-deps bootstrap-demo
assert_rule_counts "20"
assert_rule_listing
assert_rule_counts "21"

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

source_audit_projection="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT count(*) || '|' || bool_and(
      command.lifecycle_event_id = event.event_id AND
      command.outcome = 'SUCCESS' AND auth_event.outcome = 'ALLOW' AND
      command.actor_id = event.actor_id AND
      command.actor_type = event.actor_type AND
      command.occurred_at = event.occurred_at AND
      command.raw_object_id = event.raw_object_id AND
      command.snapshot_id IS NOT DISTINCT FROM event.snapshot_id AND
      auth_event.event_id = command.authorization_event_id AND
      auth_event.actor_subject = command.actor_id AND
      auth_event.actor_type = command.actor_type AND
      auth_event.operation = command.operation AND
      auth_event.target_ref = command.target_type || ':' || command.target_id AND
      command.reason = CASE event.event_type
        WHEN 'RETRIEVED' THEN 'RETRIEVED'
        WHEN 'QUARANTINED' THEN 'QUARANTINED'
        WHEN 'PARSED' THEN 'PARSED'
        WHEN 'VALIDATED' THEN 'VALIDATED'
        WHEN 'APPROVED' THEN 'APPROVED'
        WHEN 'ACTIVATED' THEN 'ACTIVATED'
        WHEN 'ROLLED_BACK' THEN 'ROLLED_BACK' END AND
      command.operation = CASE event.event_type
        WHEN 'RETRIEVED' THEN 'SOURCE_SNAPSHOT_INGEST'
        WHEN 'QUARANTINED' THEN 'SOURCE_SNAPSHOT_INGEST'
        WHEN 'PARSED' THEN 'SOURCE_SNAPSHOT_PARSE'
        WHEN 'VALIDATED' THEN 'SOURCE_SNAPSHOT_VALIDATE'
        WHEN 'APPROVED' THEN 'SOURCE_SNAPSHOT_APPROVE'
        WHEN 'ACTIVATED' THEN 'SOURCE_SNAPSHOT_ACTIVATE'
        WHEN 'ROLLED_BACK' THEN 'SOURCE_SNAPSHOT_ROLLBACK' END)
   FROM source_snapshot_lifecycle_event AS event
   JOIN source_snapshot_command_audit AS command
     ON command.lifecycle_event_id = event.event_id
   JOIN authorization_audit_event AS auth_event
     ON auth_event.event_id = command.authorization_event_id")"
if [[ "$source_audit_projection" != "12|true" ]]; then
  echo "unexpected source event-command-ALLOW projection: $source_audit_projection" >&2
  exit 1
fi

immutable_source_counts_before="$(source_graph_counts "$compose_project")"
for immutable_probe in \
  source_raw_object_metadata:original_name \
  source_parsed_snapshot:private_payload \
  source_snapshot_validation_evidence:private_payload \
  source_snapshot_lifecycle_event:reason \
  source_snapshot_command_audit:reason; do
  immutable_table="${immutable_probe%%:*}"
  immutable_column="${immutable_probe##*:}"
  if "${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
    -U tradesieve -d tradesieve -c \
    "UPDATE ${immutable_table} SET ${immutable_column} = ${immutable_column}" \
    >/dev/null 2>&1; then
    echo "$immutable_table accepted an update" >&2
    exit 1
  fi
  if "${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
    -U tradesieve -d tradesieve -c "DELETE FROM ${immutable_table}" \
    >/dev/null 2>&1; then
    echo "$immutable_table accepted a delete" >&2
    exit 1
  fi
done
immutable_source_counts_after="$(source_graph_counts "$compose_project")"
if [[ "$immutable_source_counts_before" != "$immutable_source_counts_after" ]]; then
  echo "immutable source negative probes left residual changes" >&2
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

negative_probe_residue="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT
     (SELECT count(*) FROM authorization_audit_event
      WHERE event_id LIKE 'constraint-%'
         OR correlation_id LIKE 'constraint-%') || '|' ||
     (SELECT count(*) FROM rule_bundle_command_audit
      WHERE command_event_id LIKE 'constraint-%'
         OR authorization_event_id LIKE 'constraint-%') || '|' ||
     (SELECT count(*) FROM rule_bundle_lifecycle_event
      WHERE event_id LIKE 'constraint-%')")"
if [[ "$negative_probe_residue" != "0|0|0" ]]; then
  echo "failed constraint probes left SQL residue: $negative_probe_residue" >&2
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

production_snapshot_output=""
if production_snapshot_output="$(
  TRADESIEVE_MODE=production \
  TRADESIEVE_DATABASE_URL="$production_probe_database_url" \
  TRADESIEVE_DEMO_BOOTSTRAP_ENABLED=false \
  TRADESIEVE_RULE_BUNDLE_TENANT_ID=tenant-1 \
  TRADESIEVE_DEPLOYMENT_ID=production-1 \
  TRADESIEVE_REQUIRED_SOURCE_SET=approved-sources-v1 \
  TRADESIEVE_REQUIRED_RULE_SET=approved-rules-v1 \
  "${compose[@]}" run --rm --no-deps app \
  python -m tradesieve.manage list-source-snapshots
)"; then
  echo "production list-source-snapshots unexpectedly succeeded" >&2
  exit 1
else
  production_snapshot_exit=$?
fi
if [[ "$production_snapshot_exit" != "3" || \
      "$production_snapshot_output" != '{"snapshots":[]}' ]]; then
  echo "unexpected production snapshot CLI boundary: exit=$production_snapshot_exit output=$production_snapshot_output" >&2
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
main_identity_before="$("${compose[@]}" exec -T postgres psql -X -U tradesieve \
  -d tradesieve --tuples-only --no-align -c \
  "SELECT string_agg(object_id || ':' || content_hash || ':' || byte_length,
                     ',' ORDER BY object_id) FROM source_raw_object_metadata;
   SELECT string_agg(snapshot_id || ':' || content_hash,
                     ',' ORDER BY snapshot_id) FROM source_parsed_snapshot;")"
main_raw_files_before="$("${compose[@]}" run --rm --no-deps app python -c '
import hashlib
from tradesieve.config import get_settings
root = get_settings().raw_object_root
print(",".join(
    f"{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    for path in sorted(root.rglob("*.raw"))
))
')"
"${compose[@]}" up -d --force-recreate --wait postgres app worker
persistence_marker="$("${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  --tuples-only --no-align -c "SELECT marker FROM ts102_persistence_probe")"
if [[ "$persistence_marker" != "survives-recreate" ]]; then
  echo "PostgreSQL data did not survive forced container recreation" >&2
  exit 1
fi
main_identity_after="$("${compose[@]}" exec -T postgres psql -X -U tradesieve \
  -d tradesieve --tuples-only --no-align -c \
  "SELECT string_agg(object_id || ':' || content_hash || ':' || byte_length,
                     ',' ORDER BY object_id) FROM source_raw_object_metadata;
   SELECT string_agg(snapshot_id || ':' || content_hash,
                     ',' ORDER BY snapshot_id) FROM source_parsed_snapshot;")"
main_raw_files_after="$("${compose[@]}" run --rm --no-deps app python -c '
import hashlib
from tradesieve.config import get_settings
root = get_settings().raw_object_root
print(",".join(
    f"{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    for path in sorted(root.rglob("*.raw"))
))
')"
if [[ "$main_identity_before" != "$main_identity_after" || \
      "$main_raw_files_before" != "$main_raw_files_after" ]]; then
  echo "database/raw identities changed across forced service recreation" >&2
  exit 1
fi

first_rollback_snapshot="$(application_rollback \
  'Synthetic acceptance rollback to historical v1.')"
assert_source_graph_counts "$compose_project" "2|2|2|13|13"
first_active="$("${compose[@]}" exec -T postgres psql -X -U tradesieve \
  -d tradesieve --tuples-only --no-align -c \
  "SELECT active_snapshot_id FROM source_snapshot_lifecycle_state
   WHERE deployment_id='demo' AND source_id='synthetic-source-v1'")"
if [[ "$first_active" != "$first_rollback_snapshot" ]]; then
  echo "first application rollback did not select its exact target" >&2
  exit 1
fi
first_restart_counts="$(source_graph_counts "$compose_project")"
"${compose[@]}" run --rm --no-deps bootstrap-demo
if [[ "$(source_graph_counts "$compose_project")" != "$first_restart_counts" || \
      "$("${compose[@]}" exec -T postgres psql -X -U tradesieve -d tradesieve \
        --tuples-only --no-align -c \
        "SELECT active_snapshot_id FROM source_snapshot_lifecycle_state
         WHERE deployment_id='demo' AND source_id='synthetic-source-v1'")" \
      != "$first_active" ]]; then
  echo "bootstrap changed the valid first rollback pointer or evidence" >&2
  exit 1
fi

second_rollback_snapshot="$(application_rollback \
  'Synthetic acceptance rollback to historical v2.')"
assert_source_graph_counts "$compose_project" "2|2|2|14|14"
second_active="$("${compose[@]}" exec -T postgres psql -X -U tradesieve \
  -d tradesieve --tuples-only --no-align -c \
  "SELECT active_snapshot_id FROM source_snapshot_lifecycle_state
   WHERE deployment_id='demo' AND source_id='synthetic-source-v1'")"
if [[ "$second_active" != "$second_rollback_snapshot" || \
      "$second_active" == "$first_active" ]]; then
  echo "second application rollback did not restore the other activated snapshot" >&2
  exit 1
fi
second_restart_counts="$(source_graph_counts "$compose_project")"
"${compose[@]}" run --rm --no-deps bootstrap-demo
if [[ "$(source_graph_counts "$compose_project")" != "$second_restart_counts" || \
      "$("${compose[@]}" exec -T postgres psql -X -U tradesieve -d tradesieve \
        --tuples-only --no-align -c \
        "SELECT active_snapshot_id FROM source_snapshot_lifecycle_state
         WHERE deployment_id='demo' AND source_id='synthetic-source-v1'")" \
      != "$second_active" ]]; then
  echo "bootstrap changed the valid second rollback pointer or evidence" >&2
  exit 1
fi
assert_readiness_ready "two application rollbacks and bootstrap restarts"
assert_snapshot_listing "$compose_project" "0"
authorization_operation_counts="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT count(*) FILTER (WHERE operation='SOURCE_READ'),
          count(*) FILTER (WHERE operation='SOURCE_SNAPSHOT_ROLLBACK'),
          count(*) FILTER (WHERE operation LIKE 'SOURCE_SNAPSHOT_%'
                           AND operation <> 'SOURCE_SNAPSHOT_ROLLBACK'),
          count(*) FILTER (WHERE operation='POLICY_READ'),
          count(*) FILTER (WHERE operation LIKE 'POLICY_%'
                           AND operation <> 'POLICY_READ')
   FROM authorization_audit_event")"
if [[ "$authorization_operation_counts" != "7|2|10|8|3" ]]; then
  echo "unexpected source/rule/read/rollback auth counts: $authorization_operation_counts" >&2
  exit 1
fi

final_source_audit_projection="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align -c \
  "SELECT count(*) || '|' || max(event.sequence) || '|' || bool_and(
      command.lifecycle_event_id = event.event_id AND
      command.outcome = 'SUCCESS' AND auth_event.outcome = 'ALLOW' AND
      command.actor_id = event.actor_id AND
      command.actor_type = event.actor_type AND
      command.occurred_at = event.occurred_at AND
      command.raw_object_id = event.raw_object_id AND
      command.snapshot_id IS NOT DISTINCT FROM event.snapshot_id AND
      auth_event.event_id = command.authorization_event_id AND
      auth_event.actor_subject = command.actor_id AND
      auth_event.actor_type = command.actor_type AND
      auth_event.operation = command.operation AND
      auth_event.target_ref = command.target_type || ':' || command.target_id AND
      command.operation = CASE event.event_type
        WHEN 'RETRIEVED' THEN 'SOURCE_SNAPSHOT_INGEST'
        WHEN 'QUARANTINED' THEN 'SOURCE_SNAPSHOT_INGEST'
        WHEN 'PARSED' THEN 'SOURCE_SNAPSHOT_PARSE'
        WHEN 'VALIDATED' THEN 'SOURCE_SNAPSHOT_VALIDATE'
        WHEN 'APPROVED' THEN 'SOURCE_SNAPSHOT_APPROVE'
        WHEN 'ACTIVATED' THEN 'SOURCE_SNAPSHOT_ACTIVATE'
        WHEN 'ROLLED_BACK' THEN 'SOURCE_SNAPSHOT_ROLLBACK' END
    ) || '|' || count(*) FILTER (
      WHERE event.event_type = 'ROLLED_BACK'
        AND event.actor_type = 'HUMAN'
        AND command.operation = 'SOURCE_SNAPSHOT_ROLLBACK'
        AND command.reason = 'ROLLED_BACK'
    )
   FROM source_snapshot_lifecycle_event AS event
   JOIN source_snapshot_command_audit AS command
     ON command.lifecycle_event_id = event.event_id
   JOIN authorization_audit_event AS auth_event
     ON auth_event.event_id = command.authorization_event_id")"
if [[ "$final_source_audit_projection" != "14|14|true|2" ]]; then
  echo "unexpected final source event-command-ALLOW projection: $final_source_audit_projection" >&2
  exit 1
fi

"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "UPDATE source_runtime_observation SET retrieved_at = CURRENT_TIMESTAMP - INTERVAL '3 hours', observed_at = CURRENT_TIMESTAMP WHERE deployment_id = 'demo' AND source_id = 'synthetic-source-v1'" >/dev/null
assert_live "stale required source"
assert_readiness_unavailable "stale required source"
assert_source_listing "STALE" "false" "2"

"${compose[@]}" exec -T postgres psql -U tradesieve -d tradesieve \
  -c "UPDATE source_runtime_observation SET availability = 'UNAVAILABLE' WHERE deployment_id = 'demo' AND source_id = 'synthetic-source-v1'" >/dev/null
readiness_payload="$(curl --silent --output - \
  "http://127.0.0.1:${host_port}/health/ready")"
assert_live "unavailable required source"
assert_readiness_unavailable "unavailable required source"
python -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["status"] == "UNAVAILABLE", payload
assert payload["checks"]["required_source_coverage"] == "UNAVAILABLE", payload
assert "synthetic-source-v1" not in json.dumps(payload), payload
' <<<"$readiness_payload"
assert_source_listing "UNAVAILABLE" "false" "2"
assert_bootstrap_refuses_without_mutation "corrupt source observation"

"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "WITH authoritative AS (
     SELECT state.deployment_id, state.source_id, state.active_snapshot_id,
            event.occurred_at, raw.retrieved_at, raw.effective_from
     FROM source_snapshot_lifecycle_state AS state
     JOIN source_snapshot_lifecycle_event AS event
       ON event.deployment_id = state.deployment_id
      AND event.source_id = state.source_id
      AND event.sequence = state.last_sequence
      AND event.snapshot_id = state.active_snapshot_id
      AND event.snapshot_content_hash = state.active_snapshot_content_hash
     JOIN source_parsed_snapshot AS snapshot
       ON snapshot.deployment_id = state.deployment_id
      AND snapshot.source_id = state.source_id
      AND snapshot.snapshot_id = state.active_snapshot_id
      AND snapshot.content_hash = state.active_snapshot_content_hash
     JOIN source_raw_object_metadata AS raw
       ON raw.deployment_id = snapshot.deployment_id
      AND raw.source_id = snapshot.source_id
      AND raw.object_id = snapshot.raw_object_id
      AND raw.content_hash = snapshot.raw_content_hash
      AND raw.byte_length = snapshot.raw_byte_length
     WHERE state.deployment_id = 'demo'
       AND state.source_id = 'synthetic-source-v1'
   )
   UPDATE source_runtime_observation AS observation
   SET availability = 'AVAILABLE',
       observed_at = authoritative.occurred_at,
       active_snapshot_id = authoritative.active_snapshot_id,
       retrieved_at = authoritative.retrieved_at,
       effective_from = authoritative.effective_from
   FROM authoritative
   WHERE observation.deployment_id = authoritative.deployment_id
     AND observation.source_id = authoritative.source_id" >/dev/null
assert_readiness_ready "authoritative observation repair"
assert_snapshot_listing "$compose_project" "0"

raw_volume_fault remove
assert_live "missing active raw object"
assert_readiness_unavailable "missing active raw object"
assert_snapshot_listing "$compose_project" "2"
raw_volume_fault restore-remove
assert_readiness_ready "missing active raw-object repair"

raw_volume_fault tamper
assert_live "same-length active raw-object tamper"
assert_readiness_unavailable "same-length active raw-object tamper"
assert_snapshot_listing "$compose_project" "2"
raw_volume_fault restore-tamper
assert_readiness_ready "same-length active raw-object repair"
main_raw_files_after_faults="$("${compose[@]}" run --rm --no-deps app python -c '
import hashlib
from tradesieve.config import get_settings
root = get_settings().raw_object_root
print(",".join(
    f"{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    for path in sorted(root.rglob("*.raw"))
))
')"
if [[ "$main_raw_files_after_faults" != "$main_raw_files_before" ]]; then
  echo "raw-object identity/hash changed after missing/tamper restoration" >&2
  exit 1
fi

"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "UPDATE source_snapshot_lifecycle_state
   SET last_sequence = last_sequence - 1
   WHERE deployment_id = 'demo' AND source_id = 'synthetic-source-v1'" >/dev/null
assert_live "corrupt source lifecycle state"
assert_readiness_unavailable "corrupt source lifecycle state"
assert_snapshot_listing "$compose_project" "2"
assert_bootstrap_refuses_without_mutation "corrupt source lifecycle state"
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "WITH authoritative AS (
     SELECT event.deployment_id, event.source_id, event.sequence,
            event.snapshot_id, event.snapshot_content_hash, event.occurred_at
     FROM source_snapshot_lifecycle_event AS event
     WHERE event.deployment_id = 'demo'
       AND event.source_id = 'synthetic-source-v1'
     ORDER BY event.sequence DESC LIMIT 1
   )
   UPDATE source_snapshot_lifecycle_state AS state
   SET last_sequence = authoritative.sequence,
       active_snapshot_id = authoritative.snapshot_id,
       active_snapshot_content_hash = authoritative.snapshot_content_hash,
       updated_at = authoritative.occurred_at
   FROM authoritative
   WHERE state.deployment_id = authoritative.deployment_id
     AND state.source_id = authoritative.source_id" >/dev/null
assert_readiness_ready "authoritative source lifecycle repair"

"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "UPDATE source_runtime_observation SET availability = 'QUARANTINED'
   WHERE deployment_id = 'demo' AND source_id = 'synthetic-source-v1'" >/dev/null
assert_live "corrupt source observation after lifecycle repair"
assert_readiness_unavailable "corrupt source observation after lifecycle repair"
assert_snapshot_listing "$compose_project" "2"
assert_bootstrap_refuses_without_mutation "corrupt source observation after lifecycle repair"
"${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 \
  -U tradesieve -d tradesieve -c \
  "WITH authoritative AS (
     SELECT state.deployment_id, state.source_id, state.active_snapshot_id,
            event.occurred_at, raw.retrieved_at, raw.effective_from
     FROM source_snapshot_lifecycle_state AS state
     JOIN source_snapshot_lifecycle_event AS event
       ON event.deployment_id = state.deployment_id
      AND event.source_id = state.source_id
      AND event.sequence = state.last_sequence
      AND event.snapshot_id = state.active_snapshot_id
      AND event.snapshot_content_hash = state.active_snapshot_content_hash
     JOIN source_parsed_snapshot AS snapshot
       ON snapshot.deployment_id = state.deployment_id
      AND snapshot.source_id = state.source_id
      AND snapshot.snapshot_id = state.active_snapshot_id
      AND snapshot.content_hash = state.active_snapshot_content_hash
     JOIN source_raw_object_metadata AS raw
       ON raw.deployment_id = snapshot.deployment_id
      AND raw.source_id = snapshot.source_id
      AND raw.object_id = snapshot.raw_object_id
      AND raw.content_hash = snapshot.raw_content_hash
      AND raw.byte_length = snapshot.raw_byte_length
     WHERE state.deployment_id = 'demo'
       AND state.source_id = 'synthetic-source-v1'
   )
   UPDATE source_runtime_observation AS observation
   SET availability = 'AVAILABLE',
       observed_at = authoritative.occurred_at,
       active_snapshot_id = authoritative.active_snapshot_id,
       retrieved_at = authoritative.retrieved_at,
       effective_from = authoritative.effective_from
   FROM authoritative
   WHERE observation.deployment_id = authoritative.deployment_id
     AND observation.source_id = authoritative.source_id" >/dev/null
assert_readiness_ready "authoritative source observation repair"
assert_source_graph_counts "$compose_project" "2|2|2|14|14"
assert_snapshot_listing "$compose_project" "0"
"${compose[@]}" run --rm --no-deps app python -c '
import stat
from tradesieve.adapters.local_raw_object_store import LocalImmutableRawObjectStore
from tradesieve.adapters.postgres_source_snapshot import PostgresSourceSnapshotRepository
from tradesieve.config import get_settings
from tradesieve.demo_source_snapshot import DEMO_SOURCE_ID
from tradesieve.runtime import connect
s = get_settings()
root = s.raw_object_root
with connect(s) as connection:
    repository = PostgresSourceSnapshotRepository(connection)
    snapshots = repository.list_snapshots(s.deployment_id, DEMO_SOURCE_ID, limit=512)
    assert len(snapshots) == 2
    store = LocalImmutableRawObjectStore(root)
    for snapshot in snapshots:
        metadata = repository.get_raw_metadata(
            s.deployment_id, DEMO_SOURCE_ID, snapshot.raw_object.object_id
        )
        assert metadata is not None
        assert len(store.get_verified(metadata.reference())) == metadata.byte_length
files = tuple(path for path in root.rglob("*") if path.is_file())
assert len(files) == 2
assert all(
    (path.stat().st_uid, path.stat().st_gid, stat.S_IMODE(path.stat().st_mode))
    == (10001, 10001, 0o600)
    for path in files
)
'

"${compose[@]}" stop postgres
assert_live "database outage"
readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${host_port}/health/ready")"
if [[ "$readiness_code" != "503" ]]; then
  echo "database outage did not make readiness fail closed: $readiness_code" >&2
  exit 1
fi

"${compose[@]}" start postgres
"${compose[@]}" up -d --wait
"${compose[@]}" down --volumes --remove-orphans
assert_project_zero_residue "$compose_project"
assert_project_zero_residue "$fresh_project"

trap - EXIT
trap - INT TERM HUP
echo "Compose reference deployment checks passed for projects $fresh_project and $compose_project."
