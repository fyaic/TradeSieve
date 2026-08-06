#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

uv lock --check
uv sync --locked --all-groups
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv build
./scripts/check_docs.sh
npx --yes @redocly/cli@2.44.2 lint api/openapi/tradesieve.v1.json
git ls-files -z | xargs -0 uv run --locked detect-secrets-hook --baseline .secrets.baseline
uv export --locked --all-groups --no-emit-project --format requirements-txt \
  | uv run --locked pip-audit --requirement /dev/stdin --strict --progress-spinner off
