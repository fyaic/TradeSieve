"""Static safety contract for the bounded OFAC SLS live probe."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
PROBE = ROOT / "scripts" / "ofac_sls_live_probe.py"


def test_live_probe_uses_both_comprehensive_lists_and_replay_diff() -> None:
    text = PROBE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(PROBE))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert {"_snapshot_evidence", "run_probe", "main"}.issubset(functions)
    assert "OfacSlsListKind.SDN" in text
    assert "OfacSlsListKind.CONSOLIDATED" in text
    assert "diff_ofac_snapshots" in text
    assert '"replay_stable": True' in text
    assert '"history_boundary": "locally_preserved_snapshots_only"' in text


def test_live_probe_output_is_bounded_and_failure_is_redacted() -> None:
    text = PROBE.read_text(encoding="utf-8")

    assert "entry.whole_name" not in text
    assert "entry.uid" not in text
    assert "identifier.number" not in text
    assert "except Exception:" in text
    assert "str(error)" not in text
    assert "INTERNAL_INVARIANT" in text
    assert "ensure_ascii=True" in text
    assert "sort_keys=True" in text
