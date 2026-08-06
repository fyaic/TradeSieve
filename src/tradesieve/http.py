"""Minimal HTTP runtime for liveness and fail-closed readiness."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from tradesieve.application.contracts import Health, HealthStatus
from tradesieve.config import Settings, get_settings
from tradesieve.runtime import check_readiness


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

    return application


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":  # pragma: no cover
    main()
