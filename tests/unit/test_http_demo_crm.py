"""ASGI behavior for the explicit demo-only CRM surface."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

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
    )


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
    assert set(schema.json()["paths"]) == {"/health/live", "/health/ready"}
    assert production_page.status_code == 404
    assert production_records.status_code == 404
