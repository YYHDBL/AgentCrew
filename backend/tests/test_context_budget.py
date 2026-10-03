"""M1-07：官方计数、真实文件和进程、工件校验及请求阻断。"""

import asyncio
import hashlib
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import pytest
import httpx

from agentcrew_core.memory.budget import ContextBudgetError
from agentcrew_core.tools import ToolInvocation, WorkContext, build_default_registry
from agentcrew_core.tools.scheduler import ToolScheduler
from agentcrew_server.memory.tokenizer import DEEPSEEK_V41, load_counter
from agentcrew_server.providers import bind_slot
from agentcrew_server.providers import ConfiguredProvider
from agentcrew_server.tool_outputs import ToolOutputStore


def slot():
    return bind_slot({"provider": "openai-compatible", "model": "deepseek-v4.1-flash",
                      "base_url": "https://opencode.ai/zen/go/v1", **DEEPSEEK_V41})


def test_request_counts_full_tool_round_and_reserves_output():
    cfg = slot()
    counter = load_counter(cfg)
    tools = build_default_registry().schemas()
    messages = [
        {"role": "user", "content": "核对中文资料"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "先读取真实文件"},
            {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "资料.md"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1",
                                      "content": "文件实际内容：中文段落"}]},
    ]
    budget = counter.measure(cfg, messages, tools, system="真实三库快照与技能索引")
    assert budget.system_tokens > 0 and budget.history_tokens > 0
    assert budget.tool_result_tokens > 0 and budget.output_reserved == cfg.max_tokens
    assert budget.total_input_tokens == (budget.system_tokens + budget.history_tokens
                                         + budget.tool_result_tokens)
    assert budget.context_window == 1_000_000
    budget.validate()
    without_tools = counter.measure(cfg, messages, [], system="真实三库快照与技能索引")
    assert budget.system_tokens > without_tools.system_tokens
    with pytest.raises(ContextBudgetError, match="system"):
        replace(budget, system_tokens=150_001).validate()
    with pytest.raises(ContextBudgetError, match="历史"):
        replace(budget, history_tokens=600_001).validate()
    with pytest.raises(ContextBudgetError, match="输出"):
        replace(budget, output_reserved=1_000_000).validate()
    assert not budget.compression_required
    assert replace(budget, history_tokens=480_000).compression_required


def test_unknown_profile_rejected_before_request():
    cfg = slot()
    recognized = bind_slot({"provider": "openai-compatible", "model": "deepseek-v4.1-flash",
                            "base_url": "https://opencode.ai/zen/go/v1"})
    assert recognized.context_window == cfg.context_window
    assert recognized.tokenizer_sha256 == cfg.tokenizer_sha256
    with pytest.raises(ContextBudgetError, match="MODEL_WINDOW_UNKNOWN"):
        load_counter(replace(cfg, context_window=0))
    with pytest.raises(ContextBudgetError, match="TOKENIZER_UNAVAILABLE"):
        load_counter(replace(cfg, model="unknown-model"))


def test_budget_errors_precede_any_http_request():
    observed = []

    async def audit(request):
        observed.append(str(request.url))

    async def scenario():
        client = httpx.AsyncClient(event_hooks={"request": [audit]})
        try:
            for cfg, expected in (
                    (replace(slot(), context_window=0), "MODEL_WINDOW_UNKNOWN"),
                    (replace(slot(), model="unknown-model"), "TOKENIZER_UNAVAILABLE"),
                    (replace(slot(), max_tokens=1_000_000), "CONTEXT_BUDGET_EXCEEDED")):
                provider = ConfiguredProvider({"main": cfg}, client=client)
                with pytest.raises(ContextBudgetError, match=expected):
                    async for _ in provider.stream("main", [{"role": "user", "content": "核验"}],
                                                   [], system="系统提示"):
                        pass
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert observed == []


def test_real_large_file_and_subprocess_preserve_exact_bytes(tmp_path):
    source = tmp_path / "资料.md"
    raw = (("真实中文输出😀" * 20 + "\n") * 8_000).encode("utf-8")
    source.write_bytes(raw)
    events = []

    async def emit(event_type, payload):
        events.append((event_type, payload))

    context = WorkContext(scope=[tmp_path], protected=[], cwd=tmp_path,
                          artifacts_dir=tmp_path / "artifacts", task_run_id="task-07",
                          output_store=ToolOutputStore(), emit=emit)

    async def scenario():
        scheduler = ToolScheduler(build_default_registry())
        file_result = await scheduler.run(ToolInvocation("read-large", "read_file",
            {"path": str(source), "limit": 10_000}), context)
        bash_result = await scheduler.run(ToolInvocation("bash-large", "bash",
            {"command": f"cat '{source}'"}), context)
        return file_result, bash_result, scheduler

    file_result, bash_result, scheduler = asyncio.run(scenario())
    for result in (file_result, bash_result):
        assert result.ok
        assert Path(result.artifact_path).read_bytes() == raw
        assert result.details["sha256"] == hashlib.sha256(raw).hexdigest()
        assert result.details["size_bytes"] == len(raw)
        assert 0 < result.details["prefix_bytes"] <= 2048
        assert result.output.startswith("真实中文输出😀")
        assert result.details["sha256"] in result.output
        assert "truncated" not in result.details
    externalized = [(kind, payload) for kind, payload in events
                    if kind == "tool.result_externalized"]
    assert len(externalized) == 2
    assert all(payload["sha256"] == hashlib.sha256(raw).hexdigest()
               for _, payload in externalized)

    async def read_again():
        return await scheduler.run(ToolInvocation("read-artifact", "read_file",
            {"path": file_result.artifact_path, "limit": 10_000}), context)

    reread = asyncio.run(read_again())
    assert reread.ok and Path(reread.artifact_path).read_bytes() == raw
    Path(file_result.artifact_path).write_bytes("外部修改".encode("utf-8"))
    with pytest.raises(ValueError, match="ARTIFACT_HASH_MISMATCH"):
        asyncio.run(read_again())


def test_artifact_write_failure_blocks_tool_result(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("真实大文件\n" * 8_000, encoding="utf-8")
    blocked = tmp_path / "artifacts"
    blocked.write_text("目录位置已经被占用", encoding="utf-8")
    events = []

    async def emit(kind, payload):
        events.append(kind)

    context = WorkContext(scope=[tmp_path], protected=[], cwd=tmp_path,
                          artifacts_dir=blocked, task_run_id="task-07",
                          output_store=ToolOutputStore(), emit=emit)
    with pytest.raises(OSError):
        asyncio.run(ToolScheduler(build_default_registry()).run(
            ToolInvocation("read-blocked", "read_file", {"path": str(source)}), context))
    assert events == ["tool.prepared", "tool.dispatched"]


def test_real_http_and_stderr_preserve_complete_output(tmp_path):
    body = ("HTTP 与标准错误的完整原文😀\n" * 8_000).encode("utf-8")
    source = tmp_path / "large.txt"
    source.write_bytes(body)
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(
        *args, directory=str(tmp_path), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = WorkContext(scope=[tmp_path], protected=[], cwd=tmp_path,
                          artifacts_dir=tmp_path / "artifacts", task_run_id="task-07",
                          allowed_hosts=["127.0.0.1"], output_store=ToolOutputStore())

    async def scenario():
        scheduler = ToolScheduler(build_default_registry())
        http_result = await scheduler.run(ToolInvocation("http-large", "http_request",
            {"url": f"http://127.0.0.1:{server.server_port}/{quote(source.name)}"}), context)
        stderr_result = await scheduler.run(ToolInvocation("stderr-large", "bash",
            {"command": f"cat '{source}' 1>&2"}), context)
        return http_result, stderr_result

    try:
        http_result, stderr_result = asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert http_result.ok and Path(http_result.artifact_path).read_bytes() == body
    assert http_result.details["sha256"] == hashlib.sha256(body).hexdigest()
    stderr_artifact = stderr_result.details["stderr_artifact"]
    assert stderr_result.ok and Path(stderr_artifact["artifact_path"]).read_bytes() == body
    assert stderr_artifact["sha256"] == hashlib.sha256(body).hexdigest()
    assert stderr_artifact["prefix_bytes"] <= 2048
    assert stderr_artifact["sha256"] in stderr_result.output
