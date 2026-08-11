"""Read-only MCP adapter over the canonical active-source screening service."""

from __future__ import annotations

from collections.abc import Callable

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from tradesieve.adapters.postgres_official_sources import (
    OfficialSourcePersistenceError,
    PostgresOfficialSourceRepository,
)
from tradesieve.application.official_screening import (
    OfficialScreeningRequest,
    OfficialScreeningResult,
    PersistedOfficialScreeningService,
)
from tradesieve.config import Settings, get_settings
from tradesieve.runtime import connect

SERVER_NAME = "tradesieve"
SERVER_TITLE = "TradeSieve official-source screening"
SERVER_VERSION = "0.1.0a3"
SERVER_INSTRUCTIONS = (
    "Read-only trade-compliance decision support. Results are not legal clearance. "
    "GREEN_CANDIDATE still requires policy controls; HOLD and REQUEST_EVIDENCE must "
    "not be bypassed. If a tool is unavailable, keep the business action on hold."
)
UNAVAILABLE_MESSAGE = (
    "Active official source is unavailable; keep the business action on hold."
)

ScreeningFunction = Callable[[OfficialScreeningRequest], OfficialScreeningResult]


def _screen_active(
    settings: Settings, request: OfficialScreeningRequest
) -> OfficialScreeningResult:
    with connect(settings) as connection:
        repository = PostgresOfficialSourceRepository(connection)
        return PersistedOfficialScreeningService(repository).screen(request)


def build_server(
    settings: Settings | None = None,
    *,
    screening_function: ScreeningFunction | None = None,
) -> MCPServer[None]:
    """Build one stdio-safe MCP server with an injectable application boundary."""

    runtime_settings = settings or get_settings()
    screen = screening_function or (
        lambda request: _screen_active(runtime_settings, request)
    )
    server: MCPServer[None] = MCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        description=(
            "Screens one bounded transaction candidate against TradeSieve's active "
            "verified official-source bundle."
        ),
        instructions=SERVER_INSTRUCTIONS,
        version=SERVER_VERSION,
        log_level="ERROR",
    )

    @server.tool(
        name="screen_transaction",
        title="Screen one transaction candidate",
        description=(
            "Evaluate supplied party identifiers/names and goods facts against the "
            "active EU FSF, EU Annex I, OFAC and CHPL evidence set. Returns a "
            "structured signal, business action, source versions and caveats; never "
            "returns legal clearance."
        ),
        annotations=ToolAnnotations(
            title="Screen one transaction candidate",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    def screen_transaction(
        request: OfficialScreeningRequest,
    ) -> OfficialScreeningResult:
        try:
            return screen(request)
        except (
            OfficialSourcePersistenceError,
            psycopg.Error,
            RuntimeError,
            ValueError,
        ):
            raise RuntimeError(UNAVAILABLE_MESSAGE) from None

    return server


def main() -> None:
    """Run the local trusted-host MCP adapter over standard input/output."""

    build_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
