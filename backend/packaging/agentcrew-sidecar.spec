# -*- mode: python ; coding: utf-8 -*-
# AgentCrew sidecar 打包定义（macOS arm64 onefile，桌面架构 §1）。
# 入口 scripts/release/build_sidecar.py（--specpath backend/packaging）。
# hiddenimports 只补静态分析发现不了的动态导入；原生模块（tokenizers、
# pydantic-core、anyio）由 PyInstaller hooks 覆盖，最终以 verify_sidecar.py
# 的真实运行检查为准。runtime_tmpdir 保持默认：onefile 解包到 macOS 每用户
# $TMPDIR（700 权限），不写 /tmp，也不写应用资源或用户数据目录。

from pathlib import Path

_backend = Path(SPECPATH).parent  # spec 位于 backend/packaging/，源码根在上一级

a = Analysis(
    ["sidecar_entry.py"],
    pathex=[str(_backend)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "anyio._backends._asyncio",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "mcp.client.stdio",
        "mcp.server.fastmcp",
    ],
    hookspath={},
    hooksconfig={},
    excludes=["pytest", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agentcrew-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
