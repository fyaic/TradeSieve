"""ASGI behavior for the explicit demo-only CRM surface."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

import tradesieve.http as http_runtime
from tradesieve.application.official_screening import (
    OfficialScreeningRequest,
    OfficialScreeningResult,
)
from tradesieve.config import Settings
from tradesieve.http import create_app


def send(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(request())


def production_settings() -> Settings:
    return Settings(
        mode="production",
        database_url="postgresql://service@database/tradesieve",
        demo_bootstrap_enabled=False,
        rule_bundle_tenant_id="tenant-production",
        deployment_id="deployment-production",
        required_source_set="approved-source-set",
        required_rule_set="approved-rule-set",
        official_api_token_sha256="sha256:" + "a" * 64,
    )


def official_result() -> OfficialScreeningResult:
    return OfficialScreeningResult.model_validate(
        {
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
                "name_query_count": 3,
                "name_statuses": ["NO_CANDIDATE", "CANDIDATE", "NO_CANDIDATE"],
                "name_evidence": [
                    {
                        "eu_reference_number": "EU.620.38",
                        "entity_logical_id": "983",
                        "subject_type": "enterprise",
                        "alias_assertion_hash": "sha256:" + "1" * 64,
                        "source_native_locator": "/export/entity/alias",
                        "strong_alias": True,
                    }
                ],
            },
            "dual_use": {
                "source_effective_from": "2025-11-15",
                "source_snapshot_id": "eu-dual-use-" + "d" * 64,
                "source_snapshot_content_hash": "sha256:" + "d" * 64,
                "source_archive_hash": "sha256:" + "e" * 64,
                "source_retrieved_at": "2026-08-10T06:25:19+00:00",
                "status": "TECHNICAL_REVIEW_REQUIRED",
                "requested_code": "3A001",
                "entry_content_hash": "sha256:" + "f" * 64,
                "source_native_locator": "/ANNEX/NP[NO.P='3A001']",
                "missing_facts": [
                    "qualified_classifier_review",
                    "technical_specification",
                ],
            },
            "caveats": ["Only an authorised human may clear the transaction."],
        }
    )


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
        return official_result()


def test_demo_crm_page_and_assets_are_local_no_store_resources() -> None:
    app = create_app(Settings())

    page = send(app, "GET", "/demo/crm")
    stylesheet = send(app, "GET", "/demo/crm/assets/app.css")
    javascript = send(app, "GET", "/demo/crm/assets/app.js")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "华舟国际货运" in page.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert "--accent:" in stylesheet.text
    assert javascript.status_code == 200
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert javascript.headers["cache-control"] == "no-store"
    assert "/demo/api/crm/records" in javascript.text
    assert "/screen-official" in javascript.text


def test_demo_crm_api_lists_details_and_returns_canonical_screening() -> None:
    app = create_app(Settings())

    listing = send(app, "GET", "/demo/api/crm/records")
    assert listing.status_code == 200
    records = listing.json()["records"]
    assert len(records) == 5
    record_id = records[0]["record_id"]

    detail = send(app, "GET", f"/demo/api/crm/records/{record_id}")
    screening = send(app, "POST", f"/demo/api/crm/records/{record_id}/screen")

    assert detail.status_code == 200
    assert detail.json()["record"]["record_id"] == record_id
    assert detail.json()["mapping"]["canonical_input_hash"].startswith("sha256:")
    assert screening.status_code == 200
    assert screening.json()["precomputed_fixture"] is True
    assert screening.json()["result"]["signal"] == "RED"
    assert screening.json()["result"]["business_action"] == "ESCALATE"
    assert screening.json()["result"]["state"] == "ESCALATE"


def test_demo_crm_live_button_uses_active_official_screening_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    Service.requests = []
    Service.failure = None
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
    app = create_app(Settings())

    response = send(
        app,
        "POST",
        "/demo/api/crm/records/crm-quote-260810-0047/screen-official",
    )

    assert response.status_code == 200
    assert response.json()["live_official_sources"] is True
    assert response.json()["result"]["signal"] == "RED"
    assert response.json()["result"]["business_action"] == "HOLD"
    assert response.json()["result"]["dual_use"]["requested_code"] == "3A001"
    assert len(Service.requests) == 1
    assert any(
        item.name == "Benevolence International Foundation"
        for item in Service.requests[0].party_names
    )
    assert connection.closed is True


def test_demo_crm_live_route_fails_closed_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    Service.requests = []
    Service.failure = RuntimeError("private database detail")
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
    app = create_app(Settings())

    unavailable = send(
        app,
        "POST",
        "/demo/api/crm/records/crm-quote-260810-0047/screen-official",
    )
    missing = send(
        app,
        "POST",
        "/demo/api/crm/records/unknown/screen-official",
    )

    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": "Active official source is unavailable; CRM remains held"
    }
    assert "private database detail" not in unavailable.text
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Synthetic CRM record not found"}


def test_demo_crm_unknown_record_is_a_safe_404_for_get_and_post() -> None:
    app = create_app(Settings())

    detail = send(app, "GET", "/demo/api/crm/records/unknown")
    screening = send(app, "POST", "/demo/api/crm/records/unknown/screen")

    expected = {"detail": "Synthetic CRM record not found"}
    assert detail.status_code == 404
    assert detail.json() == expected
    assert screening.status_code == 404
    assert screening.json() == expected
    assert "traceback" not in detail.text.lower() + screening.text.lower()


def test_demo_routes_are_absent_in_production_and_hidden_from_openapi() -> None:
    demo_app = create_app(Settings())
    production_app = create_app(production_settings())

    schema = send(demo_app, "GET", "/openapi.json")
    production_page = send(production_app, "GET", "/demo/crm")
    production_records = send(production_app, "GET", "/demo/api/crm/records")

    assert schema.status_code == 200
    assert set(schema.json()["paths"]) == {
        "/health/live",
        "/health/ready",
        "/v1/official-screenings",
    }
    assert production_page.status_code == 404
    assert production_records.status_code == 404
