"""Seatbelt 最小 profile（ADR-008）与子进程管道读取。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from ..judgment import path_is_protected, runtime_readonly_paths

def _sbpl_quote(path: str) -> str:
    """SBPL 字符串字面量转义（外审回稿 S06，macOS 实测）：`\"` 生效（未转义
    的双引号会让 profile 解析失败，含规则语法的路径名还可能改变生成内容）；
    反斜杠先转。"""
    return path.replace("\\", "\\\\").replace('"', '\\"')

def seatbelt_profile(scope_realpaths: list[str],
                     deny_realpaths: list[str], *, read_realpaths=None, readonly_realpaths=(), runtime_readonly=()) -> str:
    """普通文件读写限于规范化范围，运行依赖单独只读，网络全部禁止。"""
    reads = scope_realpaths if read_realpaths is None else read_realpaths
    system = [str(path) for path in runtime_readonly_paths()] + list(runtime_readonly)
    readonly_realpaths = tuple(dict.fromkeys([*readonly_realpaths, *system]))
    rules = ['(version 1)', '(allow default)', '(deny network*)', '(deny file-write*)', '(deny file-read*)']
    rules.append('(allow file-read* (literal "/"))')
    rules += [f'(allow file-read* (subpath "{_sbpl_quote(p)}"))' for p in dict.fromkeys([*reads, *system])
        if not path_is_protected(Path(p), deny_realpaths, normalized=True)]
    ancestors = {str(parent) for path in [*reads, *scope_realpaths, *system] for parent in Path(path).parents}
    rules += [f'(allow file-read-metadata (literal "{_sbpl_quote(p)}"))' for p in sorted(ancestors)]
    rules += [f'(allow file-write* (subpath "{_sbpl_quote(p)}"))'
              for p in scope_realpaths if not path_is_protected(Path(p), deny_realpaths, normalized=True)
              and not any(Path(p) == Path(root) or Path(root) in Path(p).parents for root in readonly_realpaths)]
    rules += [f'(deny file-write* (subpath "{_sbpl_quote(p)}"))' for p in readonly_realpaths]
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
