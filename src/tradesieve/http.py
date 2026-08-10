"""HTTP runtime for health checks and the explicit demo-only CRM surface."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.resources import files

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response

from tradesieve.application.contracts import Health, HealthStatus
from tradesieve.config import Settings, get_settings
from tradesieve.demo_crm import (
    DemoCrmDetailResponse,
    DemoCrmListResponse,
    DemoCrmScreeningResponse,
    get_demo_crm_record,
    list_demo_crm_records,
    screen_demo_crm_record,
)
from tradesieve.runtime import check_readiness


def _demo_asset(name: str) -> str:
    return (
        files("tradesieve")
        .joinpath("static", "demo_crm", name)
        .read_text(encoding="utf-8")
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    application = FastAPI(
        title="TradeSieve runtime foundation",
        version="0.1.0.dev0",
        debug=runtime_settings.debug,
    )

    @application.get("/health/live", response_model=Health)
    def liveness() -> Health:
        return Health(status=HealthStatus.OK, checks={"process": "UP"})

    @application.get(
        "/health/ready",
        response_model=Health,
        responses={503: {"model": Health}},
    )
    def readiness() -> Health | JSONResponse:
        status = check_readiness(runtime_settings)
        payload = Health(
            status=HealthStatus.OK if status.ready else HealthStatus.UNAVAILABLE,
            checks=status.public_checks(),
        )
        if status.ready:
            return payload
        return JSONResponse(status_code=503, content=payload.model_dump(mode="json"))

    if runtime_settings.mode == "demo":

        @application.get(
            "/demo/crm",
            response_class=HTMLResponse,
            include_in_schema=False,
        )
        def demo_crm_page() -> HTMLResponse:
            return HTMLResponse(
                _demo_asset("index.html"),
                headers={"Cache-Control": "no-store"},
            )

        @application.get(
            "/demo/crm/assets/app.css",
            response_class=Response,
            include_in_schema=False,
        )
        def demo_crm_css() -> Response:
            return Response(
                _demo_asset("app.css"),
                media_type="text/css; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        @application.get(
            "/demo/crm/assets/app.js",
            response_class=Response,
            include_in_schema=False,
        )
        def demo_crm_javascript() -> Response:
            return Response(
                _demo_asset("app.js"),
                media_type="text/javascript; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        @application.get(
            "/demo/api/crm/records",
            response_model=DemoCrmListResponse,
            include_in_schema=False,
        )
        def demo_crm_records() -> DemoCrmListResponse:
            return list_demo_crm_records(runtime_settings)

        @application.get(
            "/demo/api/crm/records/{record_id}",
            response_model=DemoCrmDetailResponse,
            include_in_schema=False,
        )
        def demo_crm_record(record_id: str) -> DemoCrmDetailResponse:
            try:
                return get_demo_crm_record(runtime_settings, record_id)
            except KeyError as error:
                raise HTTPException(
                    status_code=404,
                    detail="Synthetic CRM record not found",
                ) from error

        @application.post(
            "/demo/api/crm/records/{record_id}/screen",
            response_model=DemoCrmScreeningResponse,
            include_in_schema=False,
        )
        def demo_crm_screen(record_id: str) -> DemoCrmScreeningResponse:
            try:
                return screen_demo_crm_record(
                    runtime_settings,
                    record_id,
                    now=datetime.now(UTC),
                )
            except KeyError as error:
                raise HTTPException(
                    status_code=404,
                    detail="Synthetic CRM record not found",
                ) from error

    return application


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":  # pragma: no cover
    main()
