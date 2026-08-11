"""Package metadata smoke tests."""

import tomllib
from pathlib import Path

from tradesieve import __version__


def test_package_exposes_alpha_version() -> None:
    assert __version__ == "0.1.0a2"


def test_package_registers_the_management_console_script() -> None:
    root = Path(__file__).parents[2]
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["project"]["scripts"] == {
        "tradesieve-manage": "tradesieve.manage:main",
        "tradesieve-mcp": "tradesieve.mcp_server:main",
    }
