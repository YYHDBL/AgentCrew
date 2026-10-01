"""五个内置工具（harness-session §6.1 五件套 / ADR-008 / v1.7 语义）。

一工具一文件（pi 模式，C8 外审回稿附加提交：纯文件搬移，逻辑零改动）：
read_file（分页读）/ write_file（O_NOFOLLOW 钉父目录 + 原子写 + artifact
事件）/ bash（Seatbelt 最小 profile + env 白名单 + 超时/取消杀组）/
http_request（allowed_hosts + 外部幂等键）/ ask_user（交互原语）；
共享件 externalize（路径硬检/大输出外部化）与 seatbelt（SBPL/进程组）。

模块级汇出保持拆分前的引用面（`from agentcrew_core.tools.builtin import
…` 的既有调用方不受影响）。
"""

from __future__ import annotations

from ..metadata import Tool, ToolMetadata
from .ask_user import ASK_USER_SCHEMA, _ask_user
from .bash import BASH_SCHEMA, _bash
from .externalize import (
    HARD_OUTPUT_LIMIT,
    INLINE_OUTPUT_LIMIT,
    _check_path,
    _externalize,
    _resolve_input_path,
)
from .http_request import HTTP_SCHEMA, _http_request
from .read_file import READ_FILE_SCHEMA, _read_file
from .memory_write import MEMORY_WRITE_SCHEMA, _memory_write
from .seatbelt import seatbelt_profile
from .write_file import WRITE_FILE_SCHEMA, _write_file, _write_file_extras
from .write_file import _open_dir_nofollow  # noqa: F401 —— 测试引用面


def _meta(name, description, schema, **kwargs):
    return ToolMetadata(
        name=name, description=description, parameters=schema, **kwargs
    )


def build_default_registry():
    from ..scheduler import ToolRegistry

    registry = ToolRegistry()
    registry.register(Tool(
        _meta("read_file", "读取文件内容（分页，每页默认 200 行）", READ_FILE_SCHEMA,
              read_only=True, destructive=False, risk_level="low",
              needs_approval=False, concurrent_safe=True,
              side_effect_class="verifiable"),
        _read_file,
    ))
    registry.register(Tool(
        _meta("write_file", "把完整内容原子写入指定文件（覆盖式）", WRITE_FILE_SCHEMA,
              read_only=False, destructive=False, risk_level="medium",
              needs_approval=True, concurrent_safe=True,
              side_effect_class="verifiable"),
        _write_file, prepared_extras=_write_file_extras,
    ))
    registry.register(Tool(
        _meta("bash", "在受控沙盒中执行 shell 命令（写限任务范围、禁止联网、"
                      "受保护路径与凭据不可读写；只读白名单命令免审批）", BASH_SCHEMA,
              read_only=False, destructive=True, risk_level="medium",
              needs_approval=True, concurrent_safe=False,
              side_effect_class="outcome_unknown"),
        _bash,
    ))
    registry.register(Tool(
        _meta("http_request", "发起 HTTP 请求（受 allowed_hosts 白名单约束）",
              HTTP_SCHEMA,
              read_only=False, destructive=False, risk_level="medium",
              needs_approval=True, concurrent_safe=True,
              side_effect_class="outcome_unknown"),
        _http_request,
    ))
    registry.register(Tool(
        _meta("memory_write", "读取或修改持久化三库。用户偏好进入 user，工作区事实进入 workspace，员工经验进入 soul。先 read 获取修订和条目哈希；写入提供 expected_revision 和真实依据 basis，可用 operations 一次整合多个条目。配额失败三次后本回合跳过保存。",
              MEMORY_WRITE_SCHEMA, read_only=False, destructive=False,
              risk_level="medium", needs_approval=False, concurrent_safe=False,
              side_effect_class="verifiable"),
        _memory_write,
    ))
    registry.register(Tool(
        _meta("ask_user", "向用户提问并等待回答（用于澄清任务，答案会回到对话）",
              ASK_USER_SCHEMA,
              read_only=True, destructive=False, risk_level="low",
              needs_approval=False, concurrent_safe=True,
              side_effect_class="verifiable"),
        _ask_user,
    ))
    return registry
