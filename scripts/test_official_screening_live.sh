#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

compose_project="${TS_OFFICIAL_LIVE_COMPOSE_PROJECT:-tradesieve-official-live-$$}"
host_port="${TS_OFFICIAL_LIVE_HOST_PORT:-$((20000 + $$ % 20000))}"
pull_timeout_seconds="${TS_OFFICIAL_LIVE_PULL_TIMEOUT_SECONDS:-600}"
build_timeout_seconds="${TS_OFFICIAL_LIVE_BUILD_TIMEOUT_SECONDS:-600}"
refresh_timeout_seconds="${TS_OFFICIAL_LIVE_REFRESH_TIMEOUT_SECONDS:-900}"
http_timeout_seconds="${TS_OFFICIAL_LIVE_HTTP_TIMEOUT_SECONDS:-60}"
evidence_dir="$(mktemp -d "${TMPDIR:-/tmp}/tradesieve-official-live.XXXXXX")"
export TRADESIEVE_HOST_PORT="$host_port"
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
  local resource_ids
  resource_ids="$(docker ps -aq \
    --filter "label=com.docker.compose.project=${compose_project}")"
  [[ -z "$resource_ids" ]]
  resource_ids="$(docker network ls -q \
    --filter "label=com.docker.compose.project=${compose_project}")"
  [[ -z "$resource_ids" ]]
  resource_ids="$(docker volume ls -q \
    --filter "label=com.docker.compose.project=${compose_project}")"
  [[ -z "$resource_ids" ]]
}

cleanup() {
  local exit_code=$?
  trap - EXIT
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || exit_code=1
  assert_zero_residue || exit_code=1
  rm -r "$evidence_dir" || exit_code=1
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

if [[ ! "$host_port" =~ ^[0-9]+$ ]] || ((host_port < 1024 || host_port > 65535)); then
  echo "invalid live-gate host port" >&2
  exit 1
fi

run_bounded "$pull_timeout_seconds" "${compose[@]}" pull postgres
run_bounded "$build_timeout_seconds" "${compose[@]}" build app
"${compose[@]}" up -d --wait postgres
run_bounded "$refresh_timeout_seconds" \
  "${compose[@]}" run --rm --no-deps app \
  python -m tradesieve.manage migrate
run_bounded "$refresh_timeout_seconds" \
  "${compose[@]}" run --rm --no-deps app \
  python -m tradesieve.manage refresh-official-sources \
  >"$evidence_dir/refresh.json"
run_bounded "$refresh_timeout_seconds" \
  "${compose[@]}" run --rm --no-deps app \
  python -m tradesieve.manage refresh-official-sources \
  >"$evidence_dir/replay.json"
run_bounded "$refresh_timeout_seconds" \
  "${compose[@]}" run --rm --no-deps \
  --volume "$repo_root/examples/requests:/acceptance-requests:ro" \
  app python -m tradesieve.manage screen-active \
  --request /acceptance-requests/official-screening.json \
  >"$evidence_dir/cli.json"

"${compose[@]}" up -d --wait app
run_bounded "$http_timeout_seconds" curl --fail-with-body --silent --show-error \
  -H 'Authorization: Bearer local_demo_only_official_screening_token' \
  -H 'Content-Type: application/json' \
  --data-binary @examples/requests/official-screening.json \
  "http://127.0.0.1:${host_port}/v1/official-screenings" \
  --output "$evidence_dir/rest.json"
run_bounded "$http_timeout_seconds" curl --fail-with-body --silent --show-error \
  -X POST \
  "http://127.0.0.1:${host_port}/demo/api/crm/records/crm-quote-260810-0047/screen-official" \
  --output "$evidence_dir/crm.json"

uv run python scripts/official_screening_live_assert.py \
  "$evidence_dir/refresh.json" \
  "$evidence_dir/replay.json" \
  "$evidence_dir/cli.json" \
  "$evidence_dir/rest.json" \
  "$evidence_dir/crm.json"

"${compose[@]}" down --volumes --remove-orphans
assert_zero_residue
rm -r "$evidence_dir"
trap - EXIT INT TERM HUP
echo '{"phase":"live-four-source-zero-residue","status":"PASS"}'
