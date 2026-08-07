"""Static safety contract for the TS-302 PostgreSQL 18.4 C3 gate."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
PROBE = ROOT / "scripts" / "screening_submission_postgres_probe.py"
GATE = ROOT / "scripts" / "test_screening_submission_postgres.sh"


def test_probe_registers_every_real_postgres_acceptance_phase() -> None:
    text = PROBE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(PROBE))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {
        "legacy_upgrade_probe",
        "service_uow_probe",
        "commit_outcome_probe",
        "concurrency_probe",
        "constraint_probe",
        "attempt_capacity_probe",
        "corruption_repair_probe",
        "downgrade_reupgrade_probe",
    }.issubset(functions)
    assert "PostgresAuthorizationAuditSink" in text
    assert "PostgresScreeningSubmissionUnitOfWork" in text
    assert "ThreadPoolExecutor" in text
    assert "Barrier" in text
    assert 'next_attempt_sqlstate="54000"' in text
    assert 'migrate_down("20260806_0004")' in text
    assert 'migrate_up("head")' in text
    assert "AS auth_event" not in text
    assert "PRIVATE_KEY" not in "\n".join(
        line for line in text.splitlines() if "evidence(" in line
    )


def test_shell_gate_is_bounded_isolated_and_cleans_all_compose_resources() -> None:
    text = GATE.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "TS302_C3_COMPOSE_PROJECT" in text
    assert "tradesieve-ts302-c3-$$" in text
    assert "run_bounded" in text
    assert "TS302_C3_PULL_TIMEOUT_SECONDS" in text
    assert "TS302_C3_BUILD_TIMEOUT_SECONDS" in text
    assert "TS302_C3_PROBE_TIMEOUT_SECONDS" in text
    assert "trap cleanup EXIT" in text
    assert "trap 'exit 130' INT TERM HUP" in text
    assert "down --volumes --remove-orphans" in text
    assert text.count("com.docker.compose.project=${compose_project}") == 3
    assert 'server_version" != "18.4"' in text
    assert 'psql_version" != "psql (PostgreSQL) 18.4"' in text
    assert "screening_submission_postgres_probe.py" in text
    assert ':/acceptance:ro"' in text
    assert "--network host" not in text
