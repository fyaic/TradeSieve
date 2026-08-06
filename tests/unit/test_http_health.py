"""Health endpoint behavior tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from tradesieve.config import Settings
from tradesieve.http import create_app
from tradesieve.runtime import RuntimeStatus


def request(app: FastAPI, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get(path)

    return asyncio.run(send())


def test_liveness_does_not_probe_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_probe(settings: Settings) -> RuntimeStatus:
        raise AssertionError(f"liveness probed dependencies: {settings.mode}")

    monkeypatch.setattr("tradesieve.http.check_readiness", unexpected_probe)
    response = request(create_app(Settings()), "/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "checks": {"process": "UP"}}


def test_readiness_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable = RuntimeStatus(
        ready=False,
        checks={
            "database": "OK",
            "migration": "20260806_0001",
            "required_source_coverage": "UNAVAILABLE",
            "required_rule_coverage": "synthetic-demo-rules-v1",
        },
    )
    monkeypatch.setattr("tradesieve.http.check_readiness", lambda settings: unavailable)
    response = request(create_app(Settings()), "/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["required_source_coverage"] == "UNAVAILABLE"
    assert "synthetic-demo-rules-v1" not in response.text


def test_readiness_returns_only_public_ok_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = RuntimeStatus(
        ready=True,
        checks={
            "database": "OK",
            "migration": "OK",
            "required_source_coverage": "OK",
            "required_rule_coverage": "OK",
        },
    )
    monkeypatch.setattr("tradesieve.http.check_readiness", lambda settings: available)
    response = request(create_app(Settings()), "/health/ready")
    assert response.status_code == 200
    assert set(response.json()["checks"].values()) == {"OK"}
    assert "synthetic" not in response.text


def test_http_main_uses_configured_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(http_host="127.0.0.1", http_port=9080)
    captured: dict[str, object] = {}
    monkeypatch.setattr("tradesieve.http.get_settings", lambda: settings)
    monkeypatch.setattr(
        "tradesieve.http.uvicorn.run",
        lambda app, host, port: captured.update(app=app, host=host, port=port),
    )
    from tradesieve import http

    http.main()
    assert captured == {"app": http.app, "host": "127.0.0.1", "port": 9080}
