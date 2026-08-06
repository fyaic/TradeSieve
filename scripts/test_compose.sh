#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

compose_project="${TS102_COMPOSE_PROJECT:-tradesieve-ts102-$$}"
host_port="${TS102_HOST_PORT:-18080}"
pull_timeout_seconds="${TS102_PULL_TIMEOUT_SECONDS:-600}"
build_timeout_seconds="${TS102_BUILD_TIMEOUT_SECONDS:-600}"
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
