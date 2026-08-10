"""Static safety contract for the bounded live four-source acceptance gate."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
GATE = ROOT / "scripts" / "test_official_screening_live.sh"
ASSERTION = ROOT / "scripts" / "official_screening_live_assert.py"
REQUEST = ROOT / "examples" / "requests" / "official-screening.json"


def test_live_gate_is_bounded_isolated_authenticated_and_zero_residue() -> None:
    text = GATE.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert "TS_OFFICIAL_LIVE_COMPOSE_PROJECT" in text
    assert "tradesieve-official-live-$$" in text
    assert "run_bounded" in text
    assert "TS_OFFICIAL_LIVE_REFRESH_TIMEOUT_SECONDS" in text
    assert "trap cleanup EXIT" in text
    assert "down --volumes --remove-orphans" in text
    assert "refresh-official-sources" in text
    assert text.count("refresh-official-sources") == 2
    assert "screen-active" in text
    assert "Authorization: Bearer local_demo_only_official_screening_token" in text
    assert "/v1/official-screenings" in text
    assert "/screen-official" in text
    assert "official_screening_live_assert.py" in text
    assert "mktemp -d" in text
    assert 'rm -r "$evidence_dir"' in text


def test_assertion_binds_all_interfaces_and_current_ofac_candidate() -> None:
    text = ASSERTION.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(ASSERTION))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert {"_read", "_ofac_by_kind", "main"}.issubset(functions)
    assert "OfficialSourceRefreshResult.model_validate_json" in text
    assert "OfficialScreeningResult.model_validate_json" in text
    assert 'set(values) != {"SDN", "CONSOLIDATED"}' in text
    assert '"RUSSIA-EO14024"' in text
    assert "cli.model_dump() != rest.model_dump()" in text
    assert '"ofac_russia_candidate_held": True' in text
    assert "whole_name" not in text
    assert "identifier.number" not in text


def test_request_is_bounded_public_candidate_input() -> None:
    payload = json.loads(REQUEST.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0.0"
    assert payload["party_names"] == [{"name": "JOINT STOCK COMPANY SOVCOMFLOT"}]
    assert payload["goods"] == {
        "annex_i_code": "3A001",
        "classification_verified": True,
        "technical_specification_available": True,
    }
