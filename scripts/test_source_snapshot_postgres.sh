#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

compose_project="${TS202_C3C_COMPOSE_PROJECT:-tradesieve-ts202-c3c-$$}"
pull_timeout_seconds="${TS202_C3C_PULL_TIMEOUT_SECONDS:-600}"
build_timeout_seconds="${TS202_C3C_BUILD_TIMEOUT_SECONDS:-600}"
probe_timeout_seconds="${TS202_C3C_PROBE_TIMEOUT_SECONDS:-300}"
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

assert_zero_residue() {
  local residue=""
  local resource_ids
  resource_ids="$(docker ps -aq \
    --filter "label=com.docker.compose.project=${compose_project}")"
  if [[ -n "$resource_ids" ]]; then
    echo "C3c acceptance left Compose containers: $resource_ids" >&2
    residue="containers"
  fi
  resource_ids="$(docker network ls -q \
    --filter "label=com.docker.compose.project=${compose_project}")"
  if [[ -n "$resource_ids" ]]; then
    echo "C3c acceptance left Compose networks: $resource_ids" >&2
    residue="${residue:+$residue,}networks"
  fi
  resource_ids="$(docker volume ls -q \
    --filter "label=com.docker.compose.project=${compose_project}")"
  if [[ -n "$resource_ids" ]]; then
    echo "C3c acceptance left Compose volumes: $resource_ids" >&2
    residue="${residue:+$residue,}volumes"
  fi
  [[ -z "$residue" ]]
}

cleanup() {
  local exit_code=$?
  trap - EXIT
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || exit_code=1
  assert_zero_residue || exit_code=1
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

run_bounded "$pull_timeout_seconds" "${compose[@]}" pull postgres
run_bounded "$build_timeout_seconds" "${compose[@]}" build app
"${compose[@]}" up -d --wait postgres

server_version="$("${compose[@]}" exec -T postgres \
  psql -X -U tradesieve -d tradesieve --tuples-only --no-align \
  -c 'SHOW server_version')"
if [[ "$server_version" != "18.4" ]]; then
  echo "unexpected PostgreSQL server version: $server_version" >&2
  exit 1
fi
psql_version="$("${compose[@]}" exec -T postgres psql --version)"
if [[ "$psql_version" != "psql (PostgreSQL) 18.4" ]]; then
  echo "unexpected psql version: $psql_version" >&2
  exit 1
fi

run_bounded "$probe_timeout_seconds" \
  "${compose[@]}" run --rm --no-deps \
  --volume "$repo_root/scripts:/acceptance:ro" \
  app python /acceptance/source_snapshot_postgres_probe.py

"${compose[@]}" down --volumes --remove-orphans
assert_zero_residue
trap - EXIT INT TERM HUP
echo '{"phase":"compose-zero-residue","status":"PASS","postgres":"18.4"}'
