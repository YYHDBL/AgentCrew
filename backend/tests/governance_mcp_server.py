#!/usr/bin/env python3
"""真实MCP验收服务：文件持久化、读取、环境与进程信息。"""

import json
import os
import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel


arguments = argparse.ArgumentParser()
arguments.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
arguments.add_argument("--port", type=int, default=8000)
arguments.add_argument("dataset", nargs="?")
settings = arguments.parse_args()
server = FastMCP("AgentCrew persistent acceptance", host="127.0.0.1", port=settings.port, json_response=True)


@server.tool()
def dataset_material() -> str:
    """读取配置的位置参数指向的实际数据文件。"""
    if settings.dataset is None:
        raise ValueError("未配置数据文件")
    return Path(settings.dataset).read_text()


class EnvironmentInfo(BaseModel):
    names: list[str]
    pid: int
    group: int


@server.tool()
def persist(value: str) -> dict[str, int | str]:
    """保存真实材料并返回实际SHA与操作数量。"""
    import hashlib
    target = Path.cwd() / "mcp-operations.jsonl"
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"value": value}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"operations": len(target.read_text().splitlines()), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


@server.tool()
def readback() -> dict:
    """读取实际保存的材料。"""
    target = Path.cwd() / "mcp-operations.jsonl"
    return {"rows": [json.loads(line) for line in target.read_text().splitlines()]}


@server.tool()
def environment() -> EnvironmentInfo:
    """返回实际子进程环境名称和进程身份。"""
    return EnvironmentInfo(names=sorted(os.environ), pid=os.getpid(), group=os.getpgrp())


@server.tool()
def read_target(path: str) -> str:
    """读取实际文件以核查操作系统保护范围。"""
    return Path(path).read_text()


@server.tool()
def write_target(path: str, text: str) -> str:
    """写入实际文件以核查操作系统保护范围。"""
    Path(path).write_text(text)
    return str(Path(path).stat().st_size)


@server.tool()
def network_target(url: str) -> str:
    """请求实际网络目标以核查stdio网络限制。"""
    from urllib.request import urlopen
    with urlopen(url, timeout=5) as response:
        return response.read().decode()


@server.tool(name="employee_material_source_directory_for_governance_acceptance")
def current_directory() -> str:
    return str(Path.cwd())


if __name__ == "__main__":
    server.run(transport=settings.transport)
