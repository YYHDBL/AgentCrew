"""使用当前配置验证 M1 的两个真实模型槽，输出脱敏验收记录。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentcrew_server.config import load_config
from agentcrew_server.providers import build_provider
from agentcrew_server.secrets import register_secret


async def verify(data_dir: Path, output: Path) -> None:
    config = load_config(data_dir)
    for secret in config.api_keys():
        register_secret(secret)
    session_id = "m1-model-validation-" + uuid.uuid4().hex
    provider = build_provider(config.values["models"], session_id=session_id)
    records = []
    async with provider.client:
        for slot in ("main", "aux"):
            entry = config.values["models"][slot]
            assert entry["api_key"], f"{slot} 未配置凭据"
            events = [event async for event in provider.stream(
                slot, [{"role": "user", "content": "用一句话说明如何验证文件写入后的 SHA-256。"}],
                thinking={"type": "disabled"}, max_tokens=1024)]
            assert not any(event.type == "error" for event in events), f"{slot} 返回错误"
            assert any(event.type == "done" for event in events), f"{slot} 缺少完成事件"
            text = "".join(event.text or "" for event in events if event.type == "text_delta")
            assert text, f"{slot} 返回空文本"
            usage = next(event.usage for event in reversed(events) if event.type == "usage")
            records.append({
                "slot": slot, "model": entry["model"], "provider": entry["provider"],
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "text_characters": len(text), "event_count": len(events),
            })
    record = {"session_id": session_id, "slots": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(verify(arguments.data_dir, arguments.output))
