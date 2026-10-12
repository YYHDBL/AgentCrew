"""内置工具注册表：基础五件套、三库、会话检索和 Skill 操作。

read_file（分页读）/ write_file（原子写）/ bash（Seatbelt）/
http_request（外部幂等键）/ ask_user（交互）及记忆、Skill 工具。
共享件 externalize 执行范围检查并调用服务层保存大型输出。

模块级汇出保持现有调用路径。
"""

from __future__ import annotations

from ..metadata import Tool, ToolMetadata
from .ask_user import ASK_USER_SCHEMA, _ask_user
from .bash import BASH_SCHEMA, _bash
from .externalize import (
    INLINE_OUTPUT_LIMIT,
    _check_path,
    _externalize,
    _resolve_input_path,
)
from .http_request import HTTP_SCHEMA, _http_request
from .read_file import READ_FILE_SCHEMA, _read_file
from .memory_write import MEMORY_WRITE_SCHEMA, _memory_write
from .session_search import SESSION_SEARCH_SCHEMA, _session_search
from .skill_view import SKILL_VIEW_SCHEMA, _skill_view
from .skill_patch import SKILL_PATCH_SCHEMA, _skill_patch
from .schedule_task import SCHEDULE_TASK_SCHEMA, _schedule_task
from .trace_read import TRACE_READ_SCHEMA, _trace_read
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
        _meta("write_file", "把完整内容原子写入指定文件，自动创建合法范围内的父目录（覆盖式）", WRITE_FILE_SCHEMA,
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
        _meta("session_search", "在当前工作区和员工允许的历史与记忆中查找中文或其他子串，返回可回查来源。支持短词、分页和显式归档查询；检索不调用模型。",
              SESSION_SEARCH_SCHEMA, read_only=True, destructive=False,
              risk_level="low", needs_approval=False, concurrent_safe=True,
              side_effect_class="verifiable"),
        _session_search,
    ))
    registry.register(Tool(
        _meta("skill_view", "按名称读取实时 Skill 正文与修订；不存在时返回创建目标状态。file 仅读取已登记支撑文件。正文读取记录使用；支撑文件读取不替代正文读取。",
              SKILL_VIEW_SCHEMA, read_only=True, destructive=False, risk_level="low", needs_approval=False,
              concurrent_safe=True, side_effect_class="verifiable"), _skill_view,
    ))
    registry.register(Tool(
        _meta("skill_patch", "创建或修改可泛化的流程和机理。先在本用户回合 skill_view 或 action=read 读取目标正文/不存在状态；写入提供读到的 expected_revision 和真实 basis。create 提供 description（最多60字符）及全文 text；edit 替换全文；patch 用唯一 old_text/new_text 替换。files 维护 references/templates/assets/scripts 内的支撑文件，null 删除。允许判断无须保存；用户明确要求保存流程时执行受控保存。",
              SKILL_PATCH_SCHEMA, read_only=False, destructive=False, risk_level="medium", needs_approval=False,
              concurrent_safe=False, side_effect_class="verifiable"), _skill_patch,
    ))
    registry.register(Tool(
        _meta("trace_read", "分页读取当前身份可见的真实任务、尝试、事件、调用与错误，携带事件引用和水位；后台审查仅允许读取作业绑定的目标任务。",
              TRACE_READ_SCHEMA, read_only=True, destructive=False, risk_level="low", needs_approval=False,
              concurrent_safe=True, side_effect_class="verifiable"), _trace_read,
    ))
    registry.register(Tool(
        _meta("schedule_task", "提出不可变定时计划，必须等待真人批准。员工与工作区绑定当前任务；existing绑定当前会话，new_conversation每次建立会话。pre_authorized只表示建议范围，真人从当前合法候选缩小选择；永久允许无法免除创建确认。",
              SCHEDULE_TASK_SCHEMA, read_only=False, destructive=False, risk_level="medium", needs_approval=True,
              concurrent_safe=False, side_effect_class="verifiable", requires_human_confirmation=True), _schedule_task,
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
