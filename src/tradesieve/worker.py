"""Minimal worker runtime that proves independent process health."""

from __future__ import annotations

import time
from contextlib import suppress

import psycopg

from tradesieve.config import get_settings
from tradesieve.runtime import record_worker_heartbeat


def main() -> None:
    settings = get_settings()
    while True:
        with suppress(psycopg.Error):
            record_worker_heartbeat(settings)
        time.sleep(settings.worker_heartbeat_seconds)


if __name__ == "__main__":  # pragma: no cover
    main()
