"""Static safety contract for the official-source PostgreSQL 18.4 gate."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
PROBE = ROOT / "scripts" / "official_source_postgres_probe.py"
UPGRADE_PROBE = ROOT / "scripts" / "ofac_official_source_upgrade_probe.py"
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
    assert "build_ofac_snapshot" in functions
    assert '"immutable_guards": 5' in text
    assert '"migration": "20260810_0007"' in text
    assert '"ofac_snapshots": 2' in text


def test_upgrade_probe_preserves_0006_evidence_but_fails_closed() -> None:
    text = UPGRADE_PROBE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(UPGRADE_PROBE))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert {"legacy_bundle_identity", "main"}.issubset(functions)
    assert 'command.upgrade(config, "20260810_0006")' in text
    assert 'command.upgrade(config, "head")' in text
    assert "PostgresOfficialSourceRepository(connection).get_active()" in text
    assert '"legacy_active_fails_closed": True' in text
    assert '"legacy_dual_entries_preserved": 300' in text


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
    assert "ofac_official_source_upgrade_probe.py" in text
    assert ':/acceptance:ro"' in text
    assert "--network host" not in text
