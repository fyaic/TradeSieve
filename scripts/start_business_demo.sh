#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

demo_project="${TRADESIEVE_DEMO_PROJECT:-tradesieve-demo}"
demo_env_file="${TRADESIEVE_DEMO_ENV_FILE:-.env.example}"
demo_port="${TRADESIEVE_HOST_PORT:-8080}"

if [[ ! -f "$demo_env_file" ]]; then
  echo "TradeSieve demo environment file not found: $demo_env_file" >&2
  exit 2
fi

if [[ ! "$demo_project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "TRADESIEVE_DEMO_PROJECT must contain only lowercase letters, numbers, _ or -." >&2
  exit 2
fi

if [[ ! "$demo_port" =~ ^[0-9]+$ ]] || ((demo_port < 1 || demo_port > 65535)); then
  echo "TRADESIEVE_HOST_PORT must be an integer from 1 to 65535." >&2
  exit 2
fi

for required_command in docker curl; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Required command not found: $required_command" >&2
    exit 2
  fi
done

export TRADESIEVE_HOST_PORT="$demo_port"
compose=(docker compose --env-file "$demo_env_file" -p "$demo_project")

echo "[1/4] Building and starting the isolated TradeSieve demo stack..."
"${compose[@]}" up -d --build --wait

echo "[2/4] Refreshing and atomically activating the four official sources..."
refresh_succeeded=false
for attempt in 1 2; do
  if "${compose[@]}" run --rm --no-deps app \
    python -m tradesieve.manage refresh-official-sources; then
    refresh_succeeded=true
    break
  fi
  if [[ "$attempt" == "1" ]]; then
    echo "The official-source endpoint did not complete; retrying once..." >&2
    sleep 2
  fi
done

if [[ "$refresh_succeeded" != "true" ]]; then
  echo "Official-source refresh failed closed. The demo was not declared ready." >&2
  echo "Check outbound dependencies in docs/operations/external-dependencies.md." >&2
  exit 2
fi

echo "[3/4] Verifying the active screening path with repository-owned synthetic data..."
"${compose[@]}" exec -T app \
  tradesieve-manage screen-active --request - \
  < examples/requests/official-screening.json >/dev/null

echo "[4/4] Verifying service readiness and the synthetic CRM page..."
curl --fail --silent --show-error \
  "http://127.0.0.1:${demo_port}/health/ready" >/dev/null
curl --fail --silent --show-error \
  "http://127.0.0.1:${demo_port}/demo/crm" >/dev/null

cat <<EOF

TradeSieve business demo is ready.

CRM demo:   http://127.0.0.1:${demo_port}/demo/crm
Readiness:  http://127.0.0.1:${demo_port}/health/ready
Compose:    ${demo_project}

The page contains synthetic transactions only. Every result requires human clearance.
EOF
