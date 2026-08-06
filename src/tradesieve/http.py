"""Minimal HTTP runtime for liveness and fail-closed readiness."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tradesieve.config import Settings, get_settings
from tradesieve.runtime import check_readiness


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    application = FastAPI(
        title="TradeSieve runtime foundation",
        version="0.1.0.dev0",
        debug=runtime_settings.debug,
    )

    @application.get("/health/live", response_model=HealthResponse)
    def liveness() -> HealthResponse:
        return HealthResponse(status="OK", checks={"process": "UP"})

    @application.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse}},
    )
    def readiness() -> HealthResponse | JSONResponse:
        status = check_readiness(runtime_settings)
        payload = HealthResponse(
            status="OK" if status.ready else "UNAVAILABLE",
            checks=status.public_checks(),
        )
        if status.ready:
            return payload
        return JSONResponse(status_code=503, content=payload.model_dump())

    return application


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":  # pragma: no cover
    main()
