"""Read-only MCP adapter contract tests."""

from __future__ import annotations

import asyncio
from typing import Any, Self, cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import tradesieve.mcp_server as mcp_server
from tests.unit.test_http_official_screening import REQUEST, result
from tradesieve.application.official_screening import OfficialScreeningRequest
from tradesieve.config import Settings


def call(server: Any, arguments: dict[str, Any]) -> Any:
    return asyncio.run(server.call_tool("screen_transaction", arguments))


def test_tool_inventory_is_read_only_and_uses_canonical_schemas() -> None:
    server = mcp_server.build_server(screening_function=lambda _request: result())

    tools = asyncio.run(server.list_tools())

    assert server.name == mcp_server.SERVER_NAME
    assert server.instructions == mcp_server.SERVER_INSTRUCTIONS
    assert len(server.instructions) < 512
    assert [tool.name for tool in tools] == ["screen_transaction"]
    tool = tools[0]
    assert tool.input_schema["properties"]["request"]["$ref"].endswith(
        "OfficialScreeningRequest"
    )
    assert tool.output_schema is not None
    assert tool.output_schema["title"] == "OfficialScreeningResult"
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False


def test_tool_returns_structured_canonical_result() -> None:
    seen: list[OfficialScreeningRequest] = []

    def screen(request: OfficialScreeningRequest) -> Any:
        seen.append(request)
        return result()

    response = call(
        mcp_server.build_server(screening_function=screen),
        {"request": REQUEST},
    )

    assert response.is_error is False
    assert response.structured_content["signal"] == "RED"
    assert response.structured_content["business_action"] == "HOLD"
    assert response.structured_content["automatic_clearance"] is False
    assert response.structured_content["source_bundle_id"].startswith(
        "official-bundle-"
    )
    assert len(seen) == 1
    assert seen[0].goods.hs_code == "854231"


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("private runtime detail"),
        ValueError("private validation detail"),
    ],
)
def test_tool_fails_closed_without_exposing_private_details(failure: Exception) -> None:
    def fail(_request: OfficialScreeningRequest) -> Any:
        raise failure

    with pytest.raises(ToolError, match=mcp_server.UNAVAILABLE_MESSAGE) as captured:
        call(
            mcp_server.build_server(screening_function=fail),
            {"request": REQUEST},
        )

    assert "private" not in str(captured.value)


class Connection:
    closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True


def test_default_application_boundary_uses_active_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    settings = Settings()
    repositories: list[object] = []

    class Repository:
        def __init__(self, candidate: object) -> None:
            assert candidate is connection
            repositories.append(self)

    class Service:
        def __init__(self, repository: object) -> None:
            assert repository is repositories[0]

        def screen(self, request: OfficialScreeningRequest) -> Any:
            assert request.goods.hs_code == "854231"
            return result()

    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "connect", lambda candidate: connection)
    monkeypatch.setattr(mcp_server, "PostgresOfficialSourceRepository", Repository)
    monkeypatch.setattr(mcp_server, "PersistedOfficialScreeningService", Service)

    response = call(mcp_server.build_server(), {"request": REQUEST})

    assert response.structured_content["signal"] == "RED"
    assert len(repositories) == 1
    assert connection.closed is True


def test_main_runs_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    transports: list[str] = []

    class Server:
        def run(self, transport: str) -> None:
            transports.append(transport)

    monkeypatch.setattr(
        mcp_server,
        "build_server",
        lambda: cast(Any, Server()),
    )

    mcp_server.main()

    assert transports == ["stdio"]
