#!/usr/bin/env python3
"""构建自包含 sidecar：PyInstaller onefile 可执行文件 + 官方计数资源。

在仓库根目录以 backend/.venv/bin/python 运行（任务卡 M4-02 固定命令）：
  backend/.venv/bin/python scripts/release/build_sidecar.py \
      --arch arm64 --output-dir desktop/resources/sidecar/darwin-arm64

产物：可执行文件进 --output-dir；计数资源进 desktop/resources/tokenizers/；
模块清单、依赖许可与 SHA-256 汇总进 desktop/.artifacts/m4-build/build-manifest.json。
资源声明读取 scripts/release/tokenizer_resources.json（版本控制），
按固定 revision 下载并核对 SHA-256，任何不一致直接失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"
DECLARATIONS = ROOT / "scripts" / "release" / "tokenizer_resources.json"
TOKENIZER_DIR = ROOT / "desktop" / "resources" / "tokenizers"
ARTIFACTS = ROOT / "desktop" / ".artifacts" / "m4-build"
HF_CACHE = ARTIFACTS / "hf-cache"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_resources() -> list[dict]:
    """按声明下载官方计数资源，核对 SHA 后复制进 TOKENIZER_DIR。"""
    sys.path.insert(0, str(BACKEND))  # 复用 venv 里的 huggingface_hub
    from huggingface_hub import hf_hub_download

    declarations = json.loads(DECLARATIONS.read_text(encoding="utf-8"))
    results: list[dict] = []
    for entry in declarations["entries"]:
        target = TOKENIZER_DIR / entry["key"]
        if target.exists():
            shutil.rmtree(target)
        records = []
        for file_decl in entry["files"]:
            downloaded = Path(hf_hub_download(
                repo_id=entry["repository"], filename=file_decl["name"],
                revision=entry["revision"], cache_dir=HF_CACHE, token=False))
            digest = sha256_file(downloaded)
            if digest != file_decl["sha256"]:
                raise SystemExit(
                    f"SHA 不一致：{entry['repository']}@{entry['revision']} {file_decl['name']}\n"
                    f"期望 {file_decl['sha256']}\n实际 {digest}")
            dest = target / file_decl["name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(downloaded, dest)
            records.append({**file_decl, "size_bytes": dest.stat().st_size})
        (target / "LICENSE").write_bytes(
            Path(hf_hub_download(repo_id=entry["repository"], filename="LICENSE",
                                 revision=entry["revision"], cache_dir=HF_CACHE,
                                 token=False)).read_bytes())
        results.append({k: entry[k] for k in
                        ("key", "provider", "model", "revision", "source_url",
                         "license", "context_window", "context_window_source")}
                       | {"files": records})
    manifest = {"generated_from": str(DECLARATIONS.relative_to(ROOT)),
                "entries": results}
    (TOKENIZER_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def collect_licenses() -> list[dict]:
    """复制运行时依赖的许可文件到构建工件（发布包引用同一来源）。"""
    license_dir = ARTIFACTS / "licenses"
    if license_dir.exists():
        shutil.rmtree(license_dir)
    license_dir.mkdir(parents=True)
    records = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get("Name", "")):
        name = dist.metadata.get("Name", "")
        if not name or dist.metadata.get("License") is None and not list(dist.files or []):
            continue
        candidate = None
        for file_entry in dist.files or []:
            lowered = file_entry.name.lower()
            if lowered.startswith(("license", "licence", "copying")) or lowered in ("notice",):
                candidate = file_entry
                if lowered.startswith(("license", "licence")):
                    break
        if candidate is None:
            continue
        try:
            source = dist.locate_file(candidate)
            text = Path(source).read_bytes()
        except OSError:
            continue
        dest = license_dir / f"{name}-{candidate.name}"
        dest.write_bytes(text)
        records.append({"package": name, "file": candidate.name,
                        "path": str(dest.relative_to(ROOT))})
    return records


def build_binary(arch: str, output_dir: Path) -> Path:
    if arch != "arm64":
        raise SystemExit(f"仅支持 arm64（当前目标平台），收到 {arch}")
    if not VENV_PY.is_file():
        raise SystemExit(f"缺少虚拟环境：{VENV_PY}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work = ARTIFACTS / "pyinstaller"
    if work.exists():
        shutil.rmtree(work)
    started = time.monotonic()
    subprocess.run(
        [str(VENV_PY), "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(output_dir), "--workpath", str(work),
         str(BACKEND / "packaging" / "agentcrew-sidecar.spec")],
        cwd=ROOT, check=True)
    binary = output_dir / "agentcrew-sidecar"
    if not binary.is_file():
        raise SystemExit(f"构建未产出可执行文件：{binary}")
    os.chmod(binary, 0o755)
    print(f"PyInstaller 完成，用时 {time.monotonic() - started:.1f}s")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", default="arm64", choices=["arm64"])
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "desktop" / "resources" / "sidecar" / "darwin-arm64")
    args = parser.parse_args()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    resources = collect_resources()
    print(f"计数资源就绪：{[entry['key'] for entry in resources]}")
    binary = build_binary(args.arch, args.output_dir)
    licenses = collect_licenses()

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "pyinstaller": metadata.version("pyinstaller"),
        "binary": {
            "path": str(binary.relative_to(ROOT)),
            "size_bytes": binary.stat().st_size,
            "sha256": sha256_file(binary),
        },
        "tokenizers": resources,
        "licenses": licenses,
        "arch": args.arch,
    }
    manifest_path = ARTIFACTS / "build-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"构建清单：{manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
