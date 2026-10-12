"""M1-07：向当前真实 main/aux 发送最短请求并保存官方计数与供应商 usage。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentcrew_server.memory.tokenizer import DEEPSEEK_V41
from agentcrew_server.providers import ConfiguredProvider, bind_slot
from agentcrew_core.tools import build_default_registry

BACKEND = Path(__file__).resolve().parents[2] / "backend"


async def main() -> None:
    saved = json.loads((BACKEND / "data" / "config.json").read_text(encoding="utf-8"))
    evidence = {"observed_at": datetime.now(timezone.utc).isoformat(), "calls": []}
    for name, with_tools in (("aux", False), ("main", False), ("main", True)):
        cfg = bind_slot({**saved["models"][name], **DEEPSEEK_V41})
        if not cfg.api_key:
            raise ValueError(f"{name} 的真实模型密钥未配置")
        session_id = uuid.uuid4().hex
        events = []

        async def sink(kind, payload):
            events.append({"type": kind, "payload": payload})

        provider = ConfiguredProvider({name: cfg}, session_id=session_id,
                                      budget_sink=sink)
        usage = None
        count = 0
        tools = build_default_registry().schemas() if with_tools else []
        thinking = {"type": "disabled"} if name == "aux" else None
        try:
            async for event in provider.stream(name,
                    [{"role": "user", "content": "请只回复收到指令，不调用工具。"}], tools,
                    system="你是中文助手。", thinking=thinking):
                count += 1
                if event.usage:
                    usage = {"input_tokens": event.usage.input_tokens,
                             "output_tokens": event.usage.output_tokens}
        finally:
            await provider.aclose()
        if len(events) != 1 or events[0]["type"] != "context.budget_checked" or usage is None:
            raise AssertionError("真实模型请求缺少完整预算事件或供应商 usage")
        budget = events[0]["payload"]
        evidence["calls"].append({"slot": name, "tools_declared": len(tools), "model": cfg.model,
            "session_id": session_id, "stream_events": count,
            "budget": budget, "usage": usage,
            "input_token_difference": usage["input_tokens"] - budget["total_input_tokens"],
            "context_window_source": cfg.context_window_source,
            "tokenizer_sha256": cfg.tokenizer_sha256})

    destination = BACKEND / "data" / "m1-intermediate" / "07-model-usage.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
