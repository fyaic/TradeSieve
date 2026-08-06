"""Container healthcheck command tests."""

from __future__ import annotations

import sys
from types import TracebackType
from typing import Self

import pytest

from tradesieve import healthcheck


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@pytest.mark.parametrize(("status", "expected"), [(200, True), (503, False)])
def test_app_healthcheck_uses_readiness_endpoint(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: bool
) -> None:
    monkeypatch.setattr(
        "tradesieve.healthcheck.urllib.request.urlopen",
        lambda url, timeout: FakeResponse(status),
    )
    assert healthcheck.app_is_ready() is expected


def test_app_healthcheck_handles_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(url: str, timeout: int) -> None:
        raise OSError(f"synthetic unavailable: {url} {timeout}")

    monkeypatch.setattr("tradesieve.healthcheck.urllib.request.urlopen", unavailable)
    assert healthcheck.app_is_ready() is False


@pytest.mark.parametrize(("component", "healthy"), [("app", True), ("worker", False)])
def test_main_maps_health_to_exit_code(
    monkeypatch: pytest.MonkeyPatch, component: str, healthy: bool
) -> None:
    monkeypatch.setattr(sys, "argv", ["tradesieve.healthcheck", component])
    monkeypatch.setattr(healthcheck, "app_is_ready", lambda: healthy)
    monkeypatch.setattr(healthcheck, "worker_is_fresh", lambda settings: healthy)
    with pytest.raises(SystemExit) as exc_info:
        healthcheck.main()
    assert exc_info.value.code == (0 if healthy else 1)
