"""HTTP runtime for health checks and the explicit demo-only CRM surface."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.resources import files
from typing import Annotated

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException, Security
from fastapi.exceptions import RequestValidationError
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tradesieve.adapters.postgres_official_sources import (
    OfficialSourcePersistenceError,
    PostgresOfficialSourceRepository,
)
from tradesieve.application.contracts import Health, HealthStatus
from tradesieve.application.official_screening import (
    OfficialScreeningRequest,
    OfficialScreeningResult,
    PersistedOfficialScreeningService,
)
from tradesieve.config import Settings, get_settings
from tradesieve.demo_crm import (
    DemoCrmDetailResponse,
    DemoCrmIntegrationEvent,
    DemoCrmListResponse,
    DemoCrmOfficialScreeningResponse,
    DemoCrmScreeningResponse,
    get_demo_crm_record,
    list_demo_crm_records,
    official_request_for_demo_crm_record,
    screen_demo_crm_record,
)
from tradesieve.runtime import check_readiness, connect

_OFFICIAL_SCREENING_PATH = "/v1/official-screenings"
_MAX_OFFICIAL_REQUEST_BYTES = 1024 * 1024
_OFFICIAL_BEARER = HTTPBearer(
    auto_error=False,
    scheme_name="OfficialScreeningBearer",
    description="Deployment-scoped workload bearer token.",
)


def _official_auth_failure() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _authenticate_official_request(
    request: Request, settings: Settings
) -> JSONResponse | None:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return _official_auth_failure()
    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not 16 <= len(token) <= 4096
        or any(character.isspace() for character in token)
    ):
        return _official_auth_failure()
    candidate = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(candidate, settings.official_api_token_sha256):
        return _official_auth_failure()
    if request.headers.get("content-type", "").partition(";")[0].lower() != (
        "application/json"
    ):
        return JSONResponse(
            status_code=415,
            content={"detail": "application/json is required"},
        )
    declared = request.headers.get("content-length")
    if declared is None:
        return JSONResponse(
            status_code=411,
            content={"detail": "Content-Length is required"},
        )
    try:
        declared_length = int(declared)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Content-Length is invalid"},
        )
    if not 1 <= declared_length <= _MAX_OFFICIAL_REQUEST_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body is outside its byte bound"},
        )
    return None


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
        version="0.1.0a1",
        debug=runtime_settings.debug,
    )

    @application.exception_handler(RequestValidationError)
    async def validation_failure(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed"},
        )

    @application.middleware("http")
    async def protect_official_screening(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path == _OFFICIAL_SCREENING_PATH and request.method == "POST":
            failure = _authenticate_official_request(request, runtime_settings)
            if failure is not None:
                return failure
        return await call_next(request)

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

    @application.post(
        _OFFICIAL_SCREENING_PATH,
        response_model=OfficialScreeningResult,
        responses={
            401: {"description": "Missing or invalid workload bearer token"},
            411: {"description": "A bounded Content-Length is required"},
            415: {"description": "Only application/json is accepted"},
            422: {"description": "The request contract is invalid"},
            503: {"description": "No fresh verified official-source bundle"},
        },
    )
    def official_screening(
        screening_request: OfficialScreeningRequest,
        _credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_OFFICIAL_BEARER),
        ],
    ) -> OfficialScreeningResult:
        try:
            with connect(runtime_settings) as connection:
                return PersistedOfficialScreeningService(
                    PostgresOfficialSourceRepository(connection)
                ).screen(screening_request)
        except (
            OfficialSourcePersistenceError,
            psycopg.Error,
            RuntimeError,
            ValueError,
        ):
            raise HTTPException(
                status_code=503,
                detail="Active official source is unavailable",
            ) from None

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

        @application.post(
            "/demo/api/crm/records/{record_id}/screen-official",
            response_model=DemoCrmOfficialScreeningResponse,
            include_in_schema=False,
        )
        def demo_crm_official_screen(
            record_id: str,
        ) -> DemoCrmOfficialScreeningResponse:
            try:
                screening_request = official_request_for_demo_crm_record(
                    runtime_settings,
                    record_id,
                )
            except KeyError as error:
                raise HTTPException(
                    status_code=404,
                    detail="Synthetic CRM record not found",
                ) from error
            now = datetime.now(UTC)
            try:
                with connect(runtime_settings) as connection:
                    result = PersistedOfficialScreeningService(
                        PostgresOfficialSourceRepository(connection)
                    ).screen(screening_request)
            except (
                OfficialSourcePersistenceError,
                psycopg.Error,
                RuntimeError,
                ValueError,
            ):
                raise HTTPException(
                    status_code=503,
                    detail="Active official source is unavailable; CRM remains held",
                ) from None
            rendered_time = now.isoformat().replace("+00:00", "Z")
            return DemoCrmOfficialScreeningResponse(
                record_id=record_id,
                result=result,
                integration_events=[
                    DemoCrmIntegrationEvent(
                        label="CRM 提交真实官方来源门禁",
                        occurred_at=rendered_time,
                        detail="合成交易字段映射为主体名称和 Annex I 审查事实。",
                    ),
                    DemoCrmIntegrationEvent(
                        label="TradeSieve 返回保守业务动作",
                        occurred_at=rendered_time,
                        detail="CRM 执行 HOLD/MONITOR；任何结果均不自动放行。",
                    ),
                ],
            )

    return application


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":  # pragma: no cover
    main()
