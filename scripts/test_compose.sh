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
if [[ "$uv_version" != "uv 0.12.1" ]]; then
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
  -c "UPDATE runtime_coverage SET active = FALSE WHERE coverage_kind = 'SOURCE'" >/dev/null
readiness_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${host_port}/health/ready")"
if [[ "$readiness_code" != "503" ]]; then
  echo "missing required source coverage did not fail closed: $readiness_code" >&2
  exit 1
fi

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

trap - EXIT
echo "Compose reference deployment checks passed for project $compose_project."
