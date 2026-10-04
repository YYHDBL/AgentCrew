"""官方MCP SDK连接、发现、调用及受控stdio生命周期。"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from agentcrew_core.tools.builtin.seatbelt import seatbelt_profile
from agentcrew_core.connectors import validate_schema_references
from agentcrew_core.tools.judgment import filesystem_boundary
from .http import connector_client


@asynccontextmanager
async def session(connector, context, credential, authorize):
    config = connector["config"]
    if config["transport"] == "streamable_http":
        async with connector_client(connector, credential, authorize) as client:
            async with streamable_http_client(config["url"], http_client=client) as (read, write, _session_id):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as connection:
                    await connection.initialize()
                    yield connection
    else:
        authorize()
        boundary = filesystem_boundary(context)
        runtime = [str(Path(__file__).with_name("process_host.py").resolve()), *[path for row in connector["startup_resources"] for path in (row["source_path"], row["canonical_path"])]]
        profile = seatbelt_profile([str(path) for path in boundary.write_roots], [str(path) for path in boundary.protected],
            read_realpaths=[str(path) for path in boundary.read_roots], readonly_realpaths=[str(path) for path in boundary.readonly_roots], runtime_readonly=runtime)
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TZ", "TERM") if key in os.environ}
        args = ["-p", profile, "/usr/bin/env", "-i", *[key + "=" + value for key, value in environment.items()],
            sys.executable, str(Path(__file__).with_name("process_host.py")), config["command"], *config.get("args", [])]
        parameters = StdioServerParameters(command="/usr/bin/sandbox-exec", args=args, env=environment, cwd=context.cwd)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as connection:
                await connection.initialize()
                yield connection


async def discover(connector, context, credential, authorize):
    async with session(connector, context, credential, authorize) as connection:
        items, cursor, seen = [], None, set()
        while True:
            page = await connection.list_tools(cursor=cursor)
            items.extend(item.model_dump(by_alias=True, mode="json") for item in page.tools)
            cursor = page.nextCursor
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("MCP工具目录分页游标重复")
            seen.add(cursor)
        if len(items) != len({item["name"] for item in items}):
            raise ValueError("MCP服务器返回重复工具名称")
        return items


async def call(connector, context, credential, authorize, tool_name, arguments, expected_definition, timeout_ms):
    async with session(connector, context, credential, authorize) as connection:
        cursor, seen, approved = None, set(), False
        while True:
            page = await connection.list_tools(cursor=cursor)
            for tool in page.tools:
                validate_schema_references(tool.inputSchema)
                if tool.outputSchema is not None:
                    validate_schema_references(tool.outputSchema)
                if tool.name == tool_name:
                    if tool.model_dump(by_alias=True, mode="json") != expected_definition:
                        raise ValueError("MCP工具schema与已批准目录不一致")
                    approved = True
            cursor = page.nextCursor
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("MCP工具目录分页游标重复")
            seen.add(cursor)
        if not approved:
            raise ValueError("MCP工具已从服务器目录移除")
        authorize()
        result = await connection.call_tool(tool_name, arguments, read_timeout_seconds=timedelta(milliseconds=timeout_ms))
        return result.model_dump(by_alias=True, mode="json")
