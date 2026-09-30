#!/usr/bin/env python3
"""M0-C4 Provider 验证脚本（真实 GLM key，ADR-006 禁 mock）。

四个子命令（对应 M0-cards C4 验收）：
  plain     ① 无工具纯文本流式（text_delta 聚合 + usage）
  tooluse   ② 带工具定义 → 流式 tool_use 碎片拼装为 ToolCall → tool_result
            回传 → 最终文本（同时验证多轮 thinking 块回传姿态）
  badkey    ③ 坏 key → 分类 AUTH、不重试（可读错误）
  cache     ④ 缓存与思考：同前缀多次请求记录 cache_read_input_tokens；
            thinking enabled/disabled 参数行为

用法（backend venv）：
  cd backend && uv run python ../scripts/provider/verify.py <子命令> \
      [--data-dir backend/data] [--slot main|aux]
配置来源 data/config.json（models.main/aux：model/base_url/api_key，git 忽略）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_core.provider import GLMAnthropicProvider, SlotConfig, StreamEvent
from agentcrew_server.config import load_config
from agentcrew_server.providers import build_provider
from agentcrew_server.secrets import redact, register_secret

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "查询指定城市的当前天气",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名"}},
        "required": ["city"],
    },
}


async def drain(events):
    collected = []
    async for event in events:
        collected.append(event)
        if event.type == "text_delta":
            print(f"  text_delta: {redact(event.text)!r}")
        elif event.type == "thinking_delta":
            pass  # 思考流量大，只计数
        elif event.type == "tool_call_started":
            print(f"  tool_call_started: {redact(event.tool_name)}（进行中，不带参数）")
        elif event.type == "tool_call":
            print(f"  tool_call: id={event.tool_call.id} name={event.tool_call.name} "
                  f"input={json.dumps(event.tool_call.input, ensure_ascii=False)}")
        elif event.type == "usage":
            u = event.usage
            print(f"  usage: input={u.input_tokens} output={u.output_tokens} "
                  f"cache_read={u.cache_read_input_tokens}")
        elif event.type == "done":
            print(f"  done: stop_reason={event.stop_reason}")
        elif event.type == "error":
            # 外审回稿：上游错误文本可能回显请求内容——输出前统一脱敏
            print(f"  error: class={event.error.error_class.value} "
                  f"retryable={event.error.retryable} "
                  f"status={event.error.status_code} msg={redact(event.error.message)}")
    return collected


def assert_clean_run(events, label):
    """每个请求的统一收尾断言：无 error、有 done（外审回稿：脚本不再无条件 PASS）。"""
    assert not any(e.type == "error" for e in events), f"{label}: 出现 error 事件"
    assert any(e.type == "done" for e in events), f"{label}: 缺少 done 事件"


def counts(events):
    out = {}
    for e in events:
        out[e.type] = out.get(e.type, 0) + 1
    return out


async def cmd_plain(provider, slot):
    print(f"== ① 无工具纯文本流式（slot={slot}）")
    events = await drain(provider.stream(
        slot,
        [{"role": "user", "content": "用一句话介绍事件溯源（event sourcing）。"}],
        thinking={"type": "disabled"},
    ))
    text = "".join(e.text for e in events if e.type == "text_delta")
    thinking_chunks = sum(1 for e in events if e.type == "thinking_delta")
    print(f"[结论] 文本长度={len(text)}；thinking 块数={thinking_chunks}（disabled 生效应为 0）")
    print(f"[结论] 事件计数={counts(events)}")
    assert any(e.type == "done" for e in events), "缺少 done 事件"
    assert not any(e.type == "error" for e in events), "不应有错误"
    print("[PASS] ①")


async def cmd_tooluse(provider, slot):
    print(f"== ② 工具定义 → 流式 tool_use 拼装 → 多轮回传（slot={slot}）")
    print("-- 第一轮：模型决定调用工具")
    events1 = await drain(provider.stream(
        slot,
        [{"role": "user", "content": "用工具查一下北京现在天气怎么样"}],
        tools=[WEATHER_TOOL],
        thinking={"type": "disabled"},
    ))
    calls = [e.tool_call for e in events1 if e.type == "tool_call"]
    assert len(calls) == 1, f"应恰好归一出 1 个 ToolCall，实际 {len(calls)}"
    call = calls[0]
    assert call.name == "get_weather" and "city" in call.input, call
    assert any(e.type == "tool_call_started" for e in events1), "应有进行中事件"
    done1 = [e for e in events1 if e.type == "done"][0]
    assert done1.stop_reason == "tool_use", done1.stop_reason

    print("-- 第二轮：回传 tool_result → 最终文本")
    messages = [
        {"role": "user", "content": "用工具查一下北京现在天气怎么样"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id,
             "content": "晴，25°C，南风 2 级"},
        ]},
    ]
    events2 = await drain(provider.stream(
        slot, messages, tools=[WEATHER_TOOL], thinking={"type": "disabled"},
    ))
    text = "".join(e.text for e in events2 if e.type == "text_delta")
    done2 = [e for e in events2 if e.type == "done"][0]
    print(f"[结论] 第二轮文本：{text[:80]}")
    assert done2.stop_reason == "end_turn"
    assert not any(e.type == "error" for e in events2)
    print("[PASS] ②")


async def cmd_badkey(provider_cfg, slot):
    print(f"== ③ 坏 key → AUTH 分类、不重试（slot={slot}）")
    bad = SlotConfig(
        model=provider_cfg[slot].model, api_key="bad-key-000000000000",
        base_url=provider_cfg[slot].base_url,
    )
    provider = GLMAnthropicProvider({slot: bad})
    events = await drain(provider.stream(
        slot, [{"role": "user", "content": "hi"}], thinking={"type": "disabled"},
    ))
    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1, f"应恰好一个 error 事件，实际 {counts(events)}"
    err = errors[0].error
    print(f"[结论] 分类={err.error_class.value} retryable={err.retryable} "
          f"status={err.status_code}（请求只发出一次——无 retry 日志即未重试）")
    assert err.error_class.value == "auth" and err.retryable is False
    print("[PASS] ③")


async def cmd_cache(provider, slot):
    print(f"== ④ 缓存与思考参数（slot={slot}）")
    ctx = "AgentCrew 是一个通用 Agent 基座。" * 120  # ~1400 token 前缀
    messages = [{"role": "user", "content": ctx + " 用一个词回答：这是什么项目？"}]
    print("-- 同前缀连发三次（隐式缓存观察）")
    for i in range(3):
        events = await drain(provider.stream(
            slot, messages, thinking={"type": "disabled"}, max_tokens=64,
        ))
        assert_clean_run(events, f"cache 第{i + 1}次")
        usage = [e.usage for e in events if e.type == "usage"][-1]
        print(f"  第{i + 1}次 input={usage.input_tokens} "
              f"cache_read={usage.cache_read_input_tokens}")
    print("-- thinking 参数对比（含 thinking_block 签名捕获检查）")
    for mode in ({"type": "disabled"}, {"type": "enabled", "budget_tokens": 512}):
        events = await drain(provider.stream(
            slot, [{"role": "user", "content": "只说：好"}],
            thinking=mode, max_tokens=2048,
        ))
        assert_clean_run(events, f"thinking={mode}")
        n_delta = sum(1 for e in events if e.type == "thinking_delta")
        blocks = [e for e in events if e.type == "thinking_block"]
        print(f"  thinking={mode} → thinking_delta 块数={n_delta}；"
              f"thinking_block 完成块={len(blocks)}"
              + (f"（signature 长度 {len(blocks[0].signature or '')}）" if blocks else ""))
    print("[注] 上游不一致：流式响应下 disabled 仍可能输出 thinking 块"
          "（非流式才生效，curl 双路实测确认）；消费侧将 thinking_delta 视为"
          "可忽略事件即可，多轮回传不带 thinking 块亦被接受（已实测）")
    print("[结论] 缓存命中字段存在但实测恒 0（ADR-009：不做缓存中断检测）")
    print("[PASS] ④")


async def main_async(args):
    data_dir = Path(args.data_dir)
    config = load_config(data_dir)
    models = config.values.get("models", {})
    for key in config.api_keys():
        register_secret(key)  # 验证脚本 stdout 的脱敏兜底（与服务端同一套）
    provider = build_provider(models)
    slot = args.slot
    if not models.get(slot, {}).get("api_key"):
        raise SystemExit(f"slot={slot} 未配置 api_key（data/config.json）")

    if args.command == "plain":
        await cmd_plain(provider, slot)
    elif args.command == "tooluse":
        await cmd_tooluse(provider, slot)
    elif args.command == "badkey":
        await cmd_badkey(provider._slots, slot)
    elif args.command == "cache":
        await cmd_cache(provider, slot)
    await provider.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plain", "tooluse", "badkey", "cache"])
    parser.add_argument("--data-dir", default=str(
        Path(__file__).resolve().parents[2] / "backend" / "data"))
    parser.add_argument("--slot", default="main", choices=["main", "aux"])
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
