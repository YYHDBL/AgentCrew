"""工具层类型：ToolMetadata / 工具与调用 / 结果 / 执行上下文（harness-session §6）。

ToolMetadata 字段逐一对齐设计；副作用三分类按 ADR-008/v1.7 语义声明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

SideEffectClass = Literal["verifiable", "external_idempotency", "outcome_unknown"]


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    parameters: dict[str, Any]           # JSON Schema（给模型的入参定义）
    read_only: bool
    destructive: bool
    risk_level: Literal["low", "medium", "high"]
    needs_approval: bool                 # 默认 = not read_only（审批闸门在 C6）
    concurrent_safe: bool
    side_effect_class: SideEffectClass
    timeout_ms: int = 60_000
    max_output_bytes: int = 100_000


@dataclass(frozen=True)
class ToolInvocation:
    """一次工具调用（call_id 即幂等/审批/账本绑定的唯一身份）。"""

    call_id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    output: str = ""                      # 内联输出/摘要（≤32KB，超出外部化）
    artifact_path: str | None = None      # 外部化工件指针
    error: str | None = None              # OUT_OF_SCOPE / PROTECTED_PATH / TIMEOUT…
    details: dict[str, Any] = field(default_factory=dict)


ToolExecute = Callable[[ToolInvocation, "WorkContext"], Awaitable[ToolResult]]
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]
AskResolver = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class Tool:
    metadata: ToolMetadata
    execute: ToolExecute
    # 可选：随 tool.prepared 载荷附加的工具特有字段（如 write_file 的内容
    # sha256——v1.7 verifiable 类核验依据）；签名为 (input) -> dict
    prepared_extras: Callable[[dict], dict] | None = None


@dataclass
class WorkContext:
    """一次任务的工具执行上下文（scope/cwd/保护路径由会话装配统一提供，S09）。"""

    scope: list[Any] = field(default_factory=list)          # Path 列表（realpath 已规范）
    protected: list[Any] = field(default_factory=list)      # 受保护路径（realpath）
    artifacts_dir: Any = None                               # Path：外部化工件落盘处
    allowed_hosts: list[str] = field(default_factory=list)
    task_run_id: str = ""
    emit: EventSink | None = None        # 事件出口（tool.*/artifact.*/question.*）
    ask_resolver: AskResolver | None = None  # ask_user 的回答通道（C8 接真实 HTTP）
    # 任务工作目录（S09，C7 会话装配提供）：read_file/write_file 的相对路径
    # 统一对此解析；bash 子进程以它为 cwd。缺省 None = 维持进程 cwd（旧语义）
    cwd: Any = None
    # 经三级闸门授权的 http_request 调用（call_id 级）：M0 allowed_hosts 空 =
    # 全部需审批——人工/规则放行后执行器的域名硬检查须认这笔授权（外审 S02）
    approved_calls: set[str] = field(default_factory=set)
