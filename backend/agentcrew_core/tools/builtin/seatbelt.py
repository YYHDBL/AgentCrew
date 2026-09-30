"""Seatbelt 最小 profile（ADR-008）与流式限额泵。纯搬移自 builtin.py。"""

from __future__ import annotations

import asyncio

def _sbpl_quote(path: str) -> str:
    """SBPL 字符串字面量转义（外审回稿 S06，macOS 实测）：`\"` 生效（未转义
    的双引号会让 profile 解析失败，含规则语法的路径名还可能改变生成内容）；
    反斜杠先转。"""
    return path.replace("\\", "\\\\").replace('"', '\\"')

def seatbelt_profile(scope_realpaths: list[str],
                     deny_realpaths: list[str]) -> str:
    """最小 profile（ADR-008）：读默认放开（读取强制边界 M2），写限 scope，
    网络全禁，受保护路径**读写双向**禁（嵌套 subpath 的 deny 压过外层 allow，
    实测验证；deny 对尚不存在的路径同样生效——创建即被拒，实测验证）。SBPL
    规则：特定 subpath 覆盖泛化规则。"""
    rules = ['(version 1)', '(allow default)', '(deny network*)', '(deny file-write*)']
    rules += [f'(allow file-write* (subpath "{_sbpl_quote(p)}"))'
              for p in scope_realpaths]
    # 双向禁：先禁写（含 scope 内受保护路径），再禁读
    rules += [f'(deny file-write* (subpath "{_sbpl_quote(p)}"))'
              for p in deny_realpaths]
    rules += [f'(deny file-read* (subpath "{_sbpl_quote(p)}"))'
              for p in deny_realpaths]
    return "\n".join(rules)

def _sandboxed_argv(profile: str, command: str) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command]

async def _pump(stream, buf: bytearray, cap: int) -> bool:
    """流式读取子进程输出到 cap 字节为止（S04）：不再先整读后截断——
    `yes | head -c 4G` 类命令不会把内存吃满。返回是否触顶。"""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return False
        room = cap - len(buf)
        if room <= 0:
            return True
        buf.extend(chunk[:room])
