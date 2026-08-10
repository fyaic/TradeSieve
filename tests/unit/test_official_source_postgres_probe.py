"""Static safety contract for the official-source PostgreSQL 18.4 gate."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
PROBE = ROOT / "scripts" / "official_source_postgres_probe.py"
GATE = ROOT / "scripts" / "test_official_source_postgres.sh"


def test_probe_covers_activation_round_trip_idempotency_and_immutability() -> None:
    text = PROBE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(PROBE))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert {"build_bundle", "expect_check_violation", "main"}.issubset(functions)
    assert "PostgresOfficialSourceRepository" in text
    assert "OfficialSourceWriteOutcome.APPLIED" in text
    assert "OfficialSourceWriteOutcome.IDEMPOTENT" in text
    assert "repository.get_active()" in text
    assert '"immutable_guards": 3' in text
    assert '"migration": "20260810_0006"' in text


def test_shell_gate_is_bounded_versioned_isolated_and_zero_residue() -> None:
    text = GATE.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "TS_OFFICIAL_COMPOSE_PROJECT" in text
    assert "tradesieve-official-$$" in text
    assert "run_bounded" in text
    assert "TS_OFFICIAL_PULL_TIMEOUT_SECONDS" in text
    assert "TS_OFFICIAL_BUILD_TIMEOUT_SECONDS" in text
    assert "TS_OFFICIAL_PROBE_TIMEOUT_SECONDS" in text
    assert "trap cleanup EXIT" in text
    assert "trap 'exit 130' INT TERM HUP" in text
    assert "down --volumes --remove-orphans" in text
    assert text.count("com.docker.compose.project=${compose_project}") == 3
    assert 'server_version" != "18.4"' in text
    assert 'psql_version" != "psql (PostgreSQL) 18.4"' in text
    assert "python -m tradesieve.manage migrate" in text
    assert "official_source_postgres_probe.py" in text
    assert ':/acceptance:ro"' in text
    assert "--network host" not in text
