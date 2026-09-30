"""闸门判定纯函数矩阵（governance §2.3 三级闸门 × 规则 pattern 语义）。"""

from pathlib import Path

import pytest

from agentcrew_core.tools import (
    PermissionRule,
    ToolMetadata,
    always_scope_pattern,
    approval_target,
    evaluate_gate,
    rule_matches,
)


def _meta(name, **kw):
    base = dict(name=name, description="", parameters={},
                read_only=False, destructive=False, risk_level="medium",
                needs_approval=True, concurrent_safe=True,
                side_effect_class="verifiable", timeout_ms=5_000)
    base.update(kw)
    return ToolMetadata(**base)


WRITE = _meta("write_file")
BASH = _meta("bash", destructive=True, concurrent_safe=False,
             side_effect_class="outcome_unknown")
READ = _meta("read_file", read_only=True, needs_approval=False)
HTTP = _meta("http_request", side_effect_class="outcome_unknown")


@pytest.mark.parametrize("meta,inp,readonly,expected,reason_part", [
    # 第 1 闸：元数据
    (READ, {"path": "/x"}, False, "allow", "只读"),
    (BASH, {"command": "ls /x"}, True, "allow", "白名单只读"),
    (_meta("nuke", risk_level="high", destructive=True, side_effect_class="outcome_unknown"),
     {}, False, "deny", "硬拒"),
    (_meta("auto", needs_approval=False), {}, False, "allow", "免审批"),
    # 无规则 → ask
    (WRITE, {"path": "/tmp/a/b.txt"}, False, "ask", "人工"),
])
def test_gate_level1_and_default(meta, inp, readonly, expected, reason_part):
    result = evaluate_gate(meta, inp, readonly, [], agent_id="ag1")
    assert result.action == expected
    assert reason_part in result.reason


def test_gate_level2_rules(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    target = {"path": str(ws / "out.txt")}
    outside = {"path": str(tmp_path / "evil.txt")}
    allow_rule = [PermissionRule("ag1", "write_file", str(ws), "allow")]
    deny_rule = [PermissionRule("ag1", "write_file", str(ws), "deny")]

    r1 = evaluate_gate(WRITE, target, False, allow_rule, "ag1")
    assert r1.action == "allow" and "allow 规则命中" in r1.reason
    # 范围外不命中（区外仍弹审批——G1 语义）
    r2 = evaluate_gate(WRITE, outside, False, allow_rule, "ag1")
    assert r2.action == "ask"
    # deny 优先于 allow（同范围两条规则）
    r3 = evaluate_gate(WRITE, target, False,
                       allow_rule + deny_rule, "ag1")
    assert r3.action == "deny" and "deny 规则命中" in r3.reason
    # 别的员工的规则不生效
    r4 = evaluate_gate(WRITE, target, False,
                       [PermissionRule("other", "write_file", str(ws), "allow")], "ag1")
    assert r4.action == "ask"


def test_rule_matching_bash_exact():
    """bash 规则 = 完整命令等值（外审回稿 F05 收紧）：追加/变形命令不命中。"""
    assert rule_matches("bash", {"command": "npm test --watch"}, "npm test --watch")
    assert not rule_matches("bash", {"command": "npm test --watch --json"},
                            "npm test --watch")
    assert not rule_matches("bash", {"command": "npm test --watch; rm -rf x"},
                            "npm test --watch")
    assert not rule_matches("bash", {"command": "npmtest --watch"}, "npm test")
    assert not rule_matches("bash", {"command": "echo okay; rm -rf ./important"},
                            "echo")
    assert not rule_matches("bash", {"command": "echoSomething"}, "echo")


def test_rule_matching_http_wildcard():
    assert rule_matches("http_request", {"url": "https://api.example.com/v1"},
                        "*.example.com")
    assert not rule_matches("http_request", {"url": "https://evil.com"},
                            "*.example.com")


def test_rule_matching_write_realpath(tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(tmp_path / "real")
    assert rule_matches("write_file", {"path": str(link / "f.txt")}, str(tmp_path / "real"))


def test_always_scope_pattern_and_target(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert always_scope_pattern("write_file", {"path": str(ws / "a.txt")}) == str(ws)
    assert always_scope_pattern(
        "bash", {"command": "npm test --watch"}) == "npm test --watch"  # F05
    assert always_scope_pattern("http_request",
                                {"url": "https://api.example.com/v1"}) == "api.example.com"
    assert approval_target("write_file", {"path": str(ws / "a.txt")}) == str(ws / "a.txt")
    assert approval_target("bash", {"command": "echo hi"}) == "echo hi"
