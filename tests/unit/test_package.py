"""Package metadata smoke tests."""

from tradesieve import __version__


def test_package_exposes_development_version() -> None:
    assert __version__ == "0.1.0.dev0"
