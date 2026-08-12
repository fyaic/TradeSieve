from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_business_demo_script_is_bounded_and_fail_closed() -> None:
    script = (ROOT / "scripts/start_business_demo.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "TRADESIEVE_DEMO_PROJECT:-tradesieve-demo" in script
    assert "TRADESIEVE_DEMO_ENV_FILE:-.env.example" in script
    assert "for attempt in 1 2" in script
    assert "refresh-official-sources" in script
    assert "screen-active --request -" in script
    assert "/health/ready" in script
    assert "/demo/crm" in script
    assert "down --volumes" not in script
    assert "automatic_clearance" not in script


def test_business_demo_script_is_executable() -> None:
    mode = (ROOT / "scripts/start_business_demo.sh").stat().st_mode
    assert mode & 0o111
