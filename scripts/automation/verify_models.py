"""M3 开工时验证当前配置的真实 main/aux，公开输出仅保留脱敏字段。"""

import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from agentcrew_server.config import load_config
from agentcrew_server.providers import build_provider
from agentcrew_server.secrets import redact, register_secret


async def main():
    directory = Path(sys.argv[1]).resolve()
    configuration = load_config(directory)
    for key in configuration.api_keys():
        if key:
            register_secret(key)
    session_id = uuid.uuid4().hex
    provider = build_provider(configuration.values["models"], session_id=session_id)
    messages = [{"role": "user", "content": "请用一句中文确认你已经收到本次真实连接检查。"}]
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
              "config_sha256": hashlib.sha256(configuration.file_path.read_bytes()).hexdigest(),
              "slots": {}}
    for slot in ("main", "aux"):
        bound = provider.slots[slot]
        events = []
        async for event in provider.stream(slot, messages, tools=[], max_tokens=256):
            if event.type not in {"thinking_delta", "thinking_block"}:
                events.append(asdict(event))
        result["slots"][slot] = {
            "model": bound.model, "provider": bound.provider,
            "base_url": bound.base_url, "context_window": bound.context_window,
            "context_window_source": bound.context_window_source,
            "api_key_configured": bool(bound.api_key),
            "effective_source": "env" if any(field.startswith(f"models.{slot}.")
                                              for field in configuration.env_fields) else "file",
            "request": {"messages": messages, "tools": [], "max_tokens": 256},
            "events": events}
        assert any(event["type"] == "done" for event in events), f"{slot}模型流没有正常结束"
        assert not any(event["type"] == "error" for event in events), f"{slot}模型返回错误"
    await provider.aclose()
    print(redact(json.dumps(result, ensure_ascii=False, indent=2)))


asyncio.run(main())
