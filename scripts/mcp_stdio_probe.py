#!/usr/bin/env python3
"""Drive the packaged MCP server over stdio and emit bounded JSON evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def parser() -> argparse.ArgumentParser:
    subject = argparse.ArgumentParser()
    subject.add_argument("--project-name", default="tradesieve-demo")
    subject.add_argument(
        "--request",
        type=Path,
        default=Path("examples/requests/official-screening.json"),
    )
    subject.add_argument("--expect-signal", default="RED")
    subject.add_argument("--expect-action", default="HOLD")
    return subject


async def probe(arguments: argparse.Namespace) -> dict[str, Any]:
    request = json.loads(arguments.request.read_text(encoding="utf-8"))
    server = StdioServerParameters(
        command="docker",
        args=[
            "compose",
            "-p",
            arguments.project_name,
            "exec",
            "-T",
            "app",
            "tradesieve-mcp",
        ],
        cwd=Path(__file__).parents[1],
    )
    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as client,
    ):
        initialization = await client.initialize()
        if initialization.server_info.name != "tradesieve":
            raise RuntimeError("MCP server identity is unexpected")
        inventory = await client.list_tools()
        tool_names = [tool.name for tool in inventory.tools]
        if tool_names != ["screen_transaction"]:
            raise RuntimeError("MCP tool inventory is outside the approved boundary")
        annotations = inventory.tools[0].annotations
        if (
            annotations is None
            or annotations.read_only_hint is not True
            or annotations.destructive_hint is not False
            or annotations.idempotent_hint is not True
            or annotations.open_world_hint is not False
        ):
            raise RuntimeError("MCP tool annotations are outside the approved boundary")
        invalid = await client.call_tool(
            "screen_transaction",
            {"request": {"party_names": [], "goods": {}}},
        )
        if not invalid.is_error:
            raise RuntimeError("MCP rejected-input gate was bypassed")
        response = await client.call_tool(
            "screen_transaction",
            {"request": request},
        )
    if response.is_error or response.structured_content is None:
        raise RuntimeError("MCP screening returned an error")
    result = response.structured_content
    if (
        result.get("signal") != arguments.expect_signal
        or result.get("business_action") != arguments.expect_action
        or result.get("automatic_clearance") is not False
    ):
        raise RuntimeError("MCP screening result did not enforce the expected gate")
    bundle_id = result.get("source_bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id.startswith("official-bundle-"):
        raise RuntimeError("MCP screening result is not bound to an official bundle")
    return {
        "protocol": "stdio",
        "protocol_version": initialization.protocol_version,
        "tools": tool_names,
        "invalid_request_rejected": True,
        "signal": result["signal"],
        "business_action": result["business_action"],
        "automatic_clearance": result["automatic_clearance"],
        "source_bundle_id": bundle_id,
        "eu_fsf_statuses": result["sanctions"]["name_statuses"],
        "ofac_statuses": [
            {
                "list_kind": item["list_kind"],
                "name_statuses": item["name_statuses"],
            }
            for item in result["ofac"]["lists"]
        ],
        "dual_use_status": result["dual_use"]["status"],
        "sensitive_goods_status": result["sensitive_goods"]["status"],
    }


def main() -> None:
    print(
        json.dumps(
            asyncio.run(probe(parser().parse_args())),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
