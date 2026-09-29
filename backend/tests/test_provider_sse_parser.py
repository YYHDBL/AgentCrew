"""上游 SSE 解析器单测：分片跨 chunk / CRLF / 多行 data / 注释行 / 尾包。"""

import asyncio
import json

import pytest

from agentcrew_core.provider.sse_parser import SSEDecodeError, iter_sse_json


def _run(chunks: list[bytes]):
    async def gen():
        for c in chunks:
            yield c
    return asyncio.run(_collect(gen()))


async def _collect(agen):
    return [e async for e in iter_sse_json(agen)]


def test_basic_events():
    events = _run([
        b'event: message_start\ndata: {"type": "message_start", "x": 1}\n\n',
        b'data: {"type": "ping"}\n\n',
    ])
    assert events == [{"type": "message_start", "x": 1}, {"type": "ping"}]


def test_chunks_split_mid_line_and_mid_json():
    # 字节块切在行中间、JSON 中间、事件边界上
    events = _run([
        'data: {"type": "content_block_del'.encode(),
        'ta", "delta": {"type": "text'.encode(),
        '_delta", "text": "你好"}}'.encode(),
        b'\n',
        b'\ndata: {"type": "message_stop"}\n\n',
    ])
    assert events == [
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你好"}},
        {"type": "message_stop"},
    ]


def test_crlf_line_endings():
    events = _run([b'data: {"a": 1}\r\n\r\ndata: {"b": 2}\r\n\r\n'])
    assert events == [{"a": 1}, {"b": 2}]


def test_multiline_data_joined_with_newline():
    events = _run([b'data: {\ndata: "type": "x"}\n\n'])
    assert events == [{"type": "x"}]


def test_comment_and_ignored_fields():
    events = _run([
        b': ping keepalive\n',
        b'event: whatever\nid: 7\nretry: 3000\ndata: {"ok": true}\n\n',
    ])
    assert events == [{"ok": True}]


def test_utf8_multibyte_split_across_chunks():
    text = "北京"
    body = json.dumps({"t": text}, ensure_ascii=False).encode("utf-8")
    half = len(json.dumps({"t": ""}, ensure_ascii=False).encode("utf-8")) + 1
    events = _run([b"data: " + body[:half], body[half:] + b"\n\n"])
    assert events == [{"t": text}]


def test_trailing_event_without_final_blank_line():
    events = _run([b'data: {"type": "last"}'])
    assert events == [{"type": "last"}]


def test_invalid_json_raises():
    with pytest.raises(SSEDecodeError):
        _run([b"data: not-json\n\n"])
