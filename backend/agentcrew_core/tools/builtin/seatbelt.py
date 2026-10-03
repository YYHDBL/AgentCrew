"""Seatbelt 最小 profile（ADR-008）与子进程管道读取。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

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
    for path in deny_realpaths:
        if "*" in Path(path).parts:
            prefix = Path(path).parent.parent
            name = "".join(f"[{c.upper()}{c.lower()}]" if c.isalpha() else re.escape(c) for c in Path(path).name)
            pattern = "^" + re.escape(str(prefix)) + "/[^/]+/" + name + "$"
            selector = '(regex #"' + pattern.replace('"', '\\"') + '")'
        else:
            selector = f'(subpath "{_sbpl_quote(path)}")'
        rules.extend(f'(deny {operation} {selector})' for operation in ("file-write*", "file-read*"))
    return "\n".join(rules)

def _sandboxed_argv(profile: str, command: str) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command]

async def _pump(stream, output) -> None:
    """完整读取管道，服务层缓冲超过内存阈值后自动保存到文件。"""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        await asyncio.to_thread(output.write, chunk)
