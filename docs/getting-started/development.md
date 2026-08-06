# Development setup

**Status:** TS-201 source-registry and TS-205 rule-bundle runtime foundations. This workflow validates the development package and contracts; it does not start a screening service or imply production readiness.

## Prerequisites

- Git;
- `uv` 0.12.1;
- Node.js/npm for the pinned Redocly contract lint command.

The repository pins CPython 3.13.14 in `.python-version`. `uv` downloads that interpreter when it is not already available.

## Clean-checkout verification

From a clean checkout:

```bash
./scripts/check.sh
```

The script verifies the lockfile, installs all locked dependency groups, checks formatting/lint/types, runs unit and architecture-boundary tests with coverage, builds the wheel/source distribution, validates documentation/OpenAPI, scans tracked files for secrets, and audits Python dependencies. It covers non-container gates; run `./scripts/test_compose.sh` for the complete reference-deployment lifecycle.

For a faster edit/test loop:

```bash
uv sync --locked --all-groups
uv run --locked pytest
```

## Package boundaries

- `tradesieve.domain` is framework independent and cannot import application or adapters.
- `tradesieve.application` can orchestrate domain/port abstractions but cannot import adapters.
- `tradesieve.ports` contains replaceable interfaces when their first implementing story lands.
- Adapter directories are added only with a concrete capability; empty infrastructure scaffolding is not completion evidence.

`tests/architecture/test_dependency_boundaries.py` enforces these rules and is part of the required unit-test job.

## CI gates

Every pull request runs:

- pinned Python/`uv` lock verification, format, lint, strict type checks, unit/architecture tests, coverage, and package build;
- the existing documentation/OpenAPI contract workflow;
- tracked-file secret detection and dependency vulnerability audit.
- the Docker Compose reference deployment test when implementation changes are proposed.

GitHub Actions are pinned to immutable commit SHAs, run on the explicit `ubuntu-24.04` image, use read-only repository permissions, and do not persist checkout credentials. A failed required check must be diagnosed; repeated reruns are not acceptance evidence.

## Data boundary

Use only synthetic fixtures. Never add customer, shipment, payment, identity, evidence, credential, or other production extracts. Local `.env` files and private data/evidence/export directories remain ignored by Git.
