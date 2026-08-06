"""Worker loop behavior tests."""

from __future__ import annotations

import psycopg
import pytest

from tradesieve import worker


@pytest.mark.parametrize("database_available", [False, True])
def test_worker_retries_after_each_heartbeat_interval(
    monkeypatch: pytest.MonkeyPatch, database_available: bool
) -> None:
    attempts: list[str] = []

    def heartbeat(settings: object) -> None:
        attempts.append("heartbeat")
        if not database_available:
            raise psycopg.OperationalError("synthetic outage")

    def stop_after_interval(seconds: int) -> None:
        assert seconds == 2
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "record_worker_heartbeat", heartbeat)
    monkeypatch.setattr("tradesieve.worker.time.sleep", stop_after_interval)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert attempts == ["heartbeat"]
