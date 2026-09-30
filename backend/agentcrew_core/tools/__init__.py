"""工具层（harness-session §6 / ADR-008）：元数据、判定、注册表、调度器、五件套。"""

from .builtin import build_default_registry
from .gate import (
    GateResult,
    PermissionRule,
    always_scope_pattern,
    approval_target,
    evaluate_gate,
    rule_matches,
)
from .judgment import (
    bash_readonly,
    build_protected_paths,
    host_allowed,
    path_in_scope,
    path_is_protected,
)
from .metadata import (
    SideEffectClass,
    Tool,
    ToolInvocation,
    ToolMetadata,
    ToolResult,
    WorkContext,
)
from .scheduler import ToolRegistry, ToolScheduler, input_hash, new_call_id

__all__ = [
    "build_default_registry", "bash_readonly", "build_protected_paths",
    "host_allowed", "path_in_scope", "path_is_protected",
    "GateResult", "PermissionRule", "always_scope_pattern", "approval_target",
    "evaluate_gate", "rule_matches",
    "SideEffectClass", "Tool", "ToolInvocation", "ToolMetadata", "ToolResult",
    "WorkContext", "ToolRegistry", "ToolScheduler", "input_hash", "new_call_id",
]
