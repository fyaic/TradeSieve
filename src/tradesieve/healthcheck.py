"""Container healthcheck entry points without shell/network tooling dependencies."""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request

from tradesieve.config import get_settings
from tradesieve.runtime import worker_is_fresh


def app_is_ready() -> bool:
    settings = get_settings()
    url = f"http://127.0.0.1:{settings.http_port}/health/ready"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("app", "worker"))
    args = parser.parse_args()
    healthy = (
        app_is_ready() if args.component == "app" else worker_is_fresh(get_settings())
    )
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":  # pragma: no cover
    main()
