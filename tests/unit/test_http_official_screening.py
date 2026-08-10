"""Authenticated HTTP boundary over the active official-source screening path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import tradesieve.http as http_runtime
from tradesieve.application.official_screening import (
    OfficialScreeningRequest,
    OfficialScreeningResult,
)
from tradesieve.config import Settings

TOKEN = "local_demo_only_official_screening_token"  # pragma: allowlist secret
PATH = "/v1/official-screenings"
REQUEST = {
    "schema_version": "1.0.0",
    "party_names": [{"name": "Benevolence International Foundation"}],
    "goods": {
        "annex_i_code": "3A001",
        "classification_verified": True,
        "technical_specification_available": True,
        "product_family": "ELECTROCHEMICAL_CELL",
        "technical_facts": [
            {
                "fact_id": "is_battery",
                "unit": "BOOLEAN",
                "boolean_value": False,
                "evidence_ref": "datasheet-1",
                "verified": True,
            },
            {
                "fact_id": "cell_type",
                "unit": "CELL_TYPE",
                "text_value": "SECONDARY",
                "evidence_ref": "datasheet-1",
                "verified": True,
            },
            {
                "fact_id": "energy_density_wh_per_kg",
                "unit": "WH_PER_KG",
                "numeric_value": "380",
                "evidence_ref": "datasheet-1",
                "verified": True,
            },
            {
                "fact_id": "measurement_temperature_celsius",
                "unit": "CELSIUS",
                "numeric_value": "20",
                "evidence_ref": "datasheet-1",
                "verified": True,
            },
        ],
    },
}


def result() -> OfficialScreeningResult:
    return OfficialScreeningResult.model_validate(
        {
            "schema_version": "1.0.0",
            "signal": "RED",
            "business_action": "HOLD",
            "automatic_clearance": False,
            "source_bundle_id": "official-bundle-" + "a" * 64,
            "source_bundle_content_hash": "sha256:" + "a" * 64,
            "source_bundle_activated_at": "2026-08-10T06:25:19+00:00",
            "sanctions": {
                "source_generation_date": "2026-08-05T16:47:04+02:00",
                "source_global_file_id": "184961",
                "source_snapshot_id": "eu-fsf-" + "b" * 64,
                "source_snapshot_content_hash": "sha256:" + "b" * 64,
                "source_raw_content_hash": "sha256:" + "c" * 64,
                "source_retrieved_at": "2026-08-10T06:25:16+00:00",
                "identifier_query_count": 0,
                "identifier_statuses": [],
                "identifier_evidence": [],
                "name_query_count": 1,
                "name_statuses": ["CANDIDATE"],
                "name_evidence": [],
            },
            "dual_use": {
                "source_effective_from": "2025-11-15",
                "source_snapshot_id": "eu-dual-use-" + "d" * 64,
                "source_snapshot_content_hash": "sha256:" + "d" * 64,
                "source_archive_hash": "sha256:" + "e" * 64,
                "source_retrieved_at": "2026-08-10T06:25:19+00:00",
                "status": "CONTROL_ENTRY_FOUND",
                "requested_code": "3A001",
                "entry_content_hash": "sha256:" + "f" * 64,
                "source_native_locator": "/ANNEX/NP[NO.P='3A001']",
                "missing_facts": [],
            },
            "caveats": ["Only an authorised human may clear the transaction."],
        }
    )


def send(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: object | None = None,
    content: bytes | AsyncIterator[bytes] | None = None,
) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                content=content,
            )

    return asyncio.run(request())


class Connection:
    closed = False

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True


class Repository:
    def __init__(self, connection: object) -> None:
        self.connection = connection


class Service:
    requests: list[OfficialScreeningRequest] = []
    failure: Exception | None = None

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def screen(self, request: OfficialScreeningRequest) -> OfficialScreeningResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return result()


@pytest.fixture(autouse=True)
def reset_service() -> None:
    Service.requests = []
    Service.failure = None


def install_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, Connection]:
    connection = Connection()
    monkeypatch.setattr(http_runtime, "connect", lambda _settings: connection)
    monkeypatch.setattr(
        http_runtime,
        "PostgresOfficialSourceRepository",
        Repository,
    )
    monkeypatch.setattr(
        http_runtime,
        "PersistedOfficialScreeningService",
        Service,
    )
    return http_runtime.create_app(Settings()), connection


def test_authenticated_route_uses_one_active_repository_and_documents_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, connection = install_runtime(monkeypatch)

    response = send(
        app,
        "POST",
        PATH,
        headers={"Authorization": f"Bearer {TOKEN}"},
        json_body=REQUEST,
    )

    assert response.status_code == 200
    assert response.json()["signal"] == "RED"
    assert response.json()["business_action"] == "HOLD"
    assert response.json()["source_bundle_id"].startswith("official-bundle-")
    assert len(Service.requests) == 1
    assert Service.requests[0].goods.annex_i_code == "3A001"
    assert Service.requests[0].goods.product_family == "ELECTROCHEMICAL_CELL"
    assert len(Service.requests[0].goods.technical_facts) == 4
    assert connection.closed is True

    schema = send(app, "GET", "/openapi.json").json()
    operation = schema["paths"][PATH]["post"]
    assert operation["security"] == [{"OfficialScreeningBearer": []}]
    assert operation["requestBody"]["content"]["application/json"]


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic anything",
        "Bearer short",
        "Bearer contains whitespace",
        "Bearer " + "x" * 40,
    ],
)
def test_authentication_fails_before_body_or_database(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str | None,
) -> None:
    app, connection = install_runtime(monkeypatch)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = send(
        app,
        "POST",
        PATH,
        headers=headers,
        content=b'not-json-containing-private-name="Example Subject"',
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Authentication required"}
    assert "Example Subject" not in response.text
    assert Service.requests == []
    assert connection.closed is False


@pytest.mark.parametrize(
    ("headers", "content", "status"),
    [
        ({"Content-Type": "text/plain"}, b"{}", 415),
        ({"Content-Type": "application/json", "Content-Length": "bad"}, b"{}", 400),
        ({"Content-Type": "application/json", "Content-Length": "0"}, b"", 413),
        (
            {
                "Content-Type": "application/json",
                "Content-Length": str(1024 * 1024 + 1),
            },
            b"{}",
            413,
        ),
    ],
)
def test_request_envelope_is_rejected_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    content: bytes,
    status: int,
) -> None:
    app, _ = install_runtime(monkeypatch)
    response = send(
        app,
        "POST",
        PATH,
        headers={"Authorization": f"Bearer {TOKEN}", **headers},
        content=content,
    )
    assert response.status_code == status
    assert Service.requests == []


def test_chunked_request_without_length_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = install_runtime(monkeypatch)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"{}"

    response = send(
        app,
        "POST",
        PATH,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        content=chunks(),
    )
    assert response.status_code == 411
    assert Service.requests == []


def test_invalid_contract_is_generic_and_source_failure_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, connection = install_runtime(monkeypatch)
    invalid = {
        "party_names": [{"name": "Private Invalid Subject"}],
        "goods": {"annex_i_code": "not-a-code"},
    }

    invalid_response = send(
        app,
        "POST",
        PATH,
        headers={"Authorization": f"Bearer {TOKEN}"},
        json_body=invalid,
    )
    assert invalid_response.status_code == 422
    assert invalid_response.json() == {"detail": "Request validation failed"}
    assert "Private Invalid Subject" not in invalid_response.text
    assert connection.closed is False

    Service.failure = RuntimeError("database detail must remain private")
    unavailable = send(
        app,
        "POST",
        PATH,
        headers={"Authorization": f"Bearer {TOKEN}"},
        json_body=REQUEST,
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Active official source is unavailable"}
    assert "database detail" not in unavailable.text
    assert connection.closed is True


def test_direct_auth_validator_rejects_missing_content_length() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": PATH,
        "headers": [
            (b"authorization", f"Bearer {TOKEN}".encode()),
            (b"content-type", b"application/json"),
        ],
    }
    failure = http_runtime._authenticate_official_request(Request(scope), Settings())
    assert failure is not None
    assert failure.status_code == 411
