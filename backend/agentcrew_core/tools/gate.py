"""权限闸门判定纯函数（governance §2.3 三级闸门 / harness-design §5）。

员工deny规则优先检查。元数据的破坏风险硬拒后，合法只读调用免审批；
其余调用匹配员工allow规则，没有当前规则授权时由服务层等待人工审批。
文件scope和protected以及角色、Grant由服务层在审批和派发前检查。

pattern 语义（v1.1；bash 于外审回稿 F05 收紧）：write_file → 路径前缀
（realpath 规范化后匹配）；bash → 完整命令等值（前缀会连带放行追加命令，
实测 `echo` 规则直接放行 `echo ok; rm -rf …`）；http_request → 域名
（含 *. 通配）。无人值守交集语义是 M2/cron 域，M0 不实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from .judgment import host_allowed
from .metadata import ToolMetadata


@dataclass(frozen=True)
class PermissionRule:
    agent_id: str
    tool_name: str
    pattern: str
    effect: Literal["allow", "deny"]


@dataclass(frozen=True)
class GateResult:
    action: Literal["allow", "deny", "ask"]
    reason: str
    matched_pattern: str | None = None  # 第 2 闸命中时的规则 pattern


def _write_target(call_input: dict[str, Any], cwd) -> Path:
    """write_file 的判定基准路径：相对路径以任务 cwd 解析（S09 同源；
    C8 修复：否则审批 target/规则 pattern 会落在进程 cwd 上，与实际写入
    落点错位——allow_always 落的目录规则永远盖不住后续同类写入）。"""
    path = Path(call_input.get("path", "")).expanduser()
    if not path.is_absolute() and cwd is not None:
        path = Path(cwd) / path
    return path.resolve()


def rule_matches(tool_name: str, call_input: dict[str, Any], pattern: str,
                 cwd=None) -> bool:
    if tool_name in {"write_file", "read_file"}:
        target = _write_target(call_input, cwd)
        root = Path(pattern).expanduser().resolve()
        try:
            target.relative_to(root)
            return True
        except ValueError:
            return False
    if tool_name == "bash":
        # 精确匹配完整命令（外审回稿 F05 收紧，超越 governance G1 的前缀
        # 语义）：`echo` 前缀会连带放行 `echo x; rm -rf …` 与 `echoSomething`；
        # 完整命令等值则任何追加/变形都不可能命中
        return call_input.get("command", "") == pattern
    if tool_name == "http_request":
        ok, _ = host_allowed(call_input.get("url", ""), [pattern])
        return ok
    if tool_name.startswith("mcp_"):
        return pattern == tool_name
    return False


def evaluate_gate(
    meta: ToolMetadata,
    call_input: dict[str, Any],
    bash_readonly_verdict: bool,
    rules: list[PermissionRule],
    agent_id: str = "",
    cwd=None,
) -> GateResult:
    mine = [r for r in rules
            if r.agent_id == agent_id and r.tool_name == meta.name]
    for rule in mine:
        if rule.effect == "deny" and rule_matches(
                meta.name, call_input, rule.pattern, cwd):
            return GateResult("deny", f"deny 规则命中：{rule.pattern}", rule.pattern)
    # deny规则检查完成后判断元数据。
    if meta.risk_level == "high" and meta.destructive:
        return GateResult("deny", "risk=high 且 destructive，硬拒")
    if meta.name == "bash" and bash_readonly_verdict:
        return GateResult("allow", "白名单只读命令（动态判定）")
    if meta.read_only:
        return GateResult("allow", "只读工具")
    if not meta.needs_approval:
        return GateResult("allow", "元数据免审批")
    # 第 2 闸：员工规则表（deny > allow）
    for rule in mine:
        if rule.effect == "allow" and rule_matches(
                meta.name, call_input, rule.pattern, cwd):
            return GateResult("allow", f"allow 规则命中：{rule.pattern}", rule.pattern)
    # 第 3 闸：人工审批
    return GateResult("ask", "无规则命中，需人工审批")


def approval_target(tool_name: str, call_input: dict[str, Any],
                    cwd=None) -> str:
    """审批卡上的目标资源展示（v1.4 target 字段；相对路径按任务 cwd）。"""
    if tool_name == "write_file":
        return str(_write_target(call_input, cwd))
    if tool_name == "bash":
        return call_input.get("command", "")[:120]
    if tool_name == "http_request":
        return call_input.get("url", "")
    return tool_name


def always_scope_pattern(tool_name: str, call_input: dict[str, Any],
                         cwd=None) -> str:
    """allow_always / reject_always 落规则的 pattern（G1：工具+资源范围）——
    也就是审批卡上 always_scope_preview 展示给用户看的内容。"""
    if tool_name == "write_file":
        return str(_write_target(call_input, cwd).parent)  # 同目录内后续写放行
    if tool_name == "bash":
        return call_input.get("command", "")  # 完整命令（等值匹配，F05 收紧）
    if tool_name == "http_request":
        return urlparse(call_input.get("url", "")).hostname or ""
    return tool_name
