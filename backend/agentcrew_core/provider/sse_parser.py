"""上游 SSE 流的最小确定性行解析器（ADR-009）。

只服务 Provider 消费模型 API 的流式响应：按 SSE 规范聚合 data: 行
（多行以 \\n 拼接）、忽略注释行（: 开头）与无关字段（event:/id:/retry:），
字节块可在任意位置切分（含 UTF-8 多字节字符中间——缓冲保持 bytes，仅对
完整行解码）。前后端之间的 SSE 仍由 sse-starlette 承担（v1.4 裁定范围
不变，见 ADR-009 第 3 条）。
"""

from __future__ import annotations

import json
from typing import AsyncIterator


class SSEDecodeError(RuntimeError):
    pass


async def iter_sse_json(chunks: AsyncIterator[bytes]) -> AsyncIterator[dict]:
    """字节块流 → 解析后的 JSON 事件字典（Anthropic 流每事件一个 data 载荷）。"""
    buffer = b""
    data_lines: list[str] = []

    def _dispatch() -> dict | None:
        if not data_lines:
            return None
        payload = "\n".join(data_lines)
        data_lines.clear()
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError as e:
            raise SSEDecodeError(f"上游 data 非 JSON：{payload[:200]}") from e
        if not isinstance(obj, dict):  # 合法 JSON 数组/标量不是事件对象（外审 S15）
            raise SSEDecodeError(f"上游 data 非事件对象：{payload[:200]}")
        return obj

    async for chunk in chunks:
        buffer += chunk
        while True:
            idx = buffer.find(b"\n")
            if idx < 0:
                break  # 行未完整：留在缓冲（bytes，不怕多字节被切开）
            line = buffer[:idx].decode("utf-8").rstrip("\r")
            buffer = buffer[idx + 1:]
            if line.startswith(":"):
                continue  # 注释/心跳行
            if line == "":
                event = _dispatch()
                if event is not None:
                    yield event
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
            # 其余字段（event:/id:/retry:）按规范忽略——类型信息在 data JSON 内

    # 流结束：缓冲残留的最后一行（无结尾空行）按规范也应尝试分发
    if buffer:
        line = buffer.decode("utf-8").rstrip("\r")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    event = _dispatch()
    if event is not None:
        yield event
