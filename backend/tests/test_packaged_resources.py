"""打包计数资源加载逻辑（M4-02）。

只测 tokenizer.py 的资源解析分支：AGENTCREW_TOKENIZER_DIR 显式目录、
SHA 不一致拒绝、缺文件拒绝、env 设置时不走下载路径。真实 tokenizer 的
端到端计数由 scripts/release/verify_sidecar.py 用打包产物核查。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentcrew_core.memory.budget import ContextBudgetError
from agentcrew_core.provider.glm_anthropic import SlotConfig
from agentcrew_server.memory import tokenizer
from agentcrew_server.memory.tokenizer import (
    ENCODING_SHA256,
    _local_resources,
    load_counter,
)


def make_cfg(**overrides) -> SlotConfig:
    values = {
        "model": "deepseek-v4.1-flash",
        "api_key": "test-key",
        "base_url": "https://opencode.ai/zen/go/v1",
        "provider": "openai-compatible",
        **tokenizer.DEEPSEEK_V41,
    }
    values.update(overrides)
    return SlotConfig(**values)


@pytest.fixture()
def resource_dir(tmp_path, monkeypatch):
    """与声明 SHA 无关的占位资源目录：哈希按实际内容计算，验证逻辑分支。

    encoding.py 的期望哈希是模块常量；此处将其替换为占位内容的真实哈希，
    使成功路径可用占位资源走通（生产常量由 verify_sidecar.py 端到端核查）。
    """
    base = tmp_path / "tokenizers" / "deepseek-v4.1"
    (base / "encoding").mkdir(parents=True)
    tokenizer_bytes = b"placeholder-tokenizer"
    encoding_bytes = b"def encode_messages(*args, **kwargs):\n    return ''\n"
    (base / "tokenizer.json").write_bytes(tokenizer_bytes)
    (base / "encoding" / "encoding.py").write_bytes(encoding_bytes)
    encoding_sha = hashlib.sha256(encoding_bytes).hexdigest()
    monkeypatch.setenv("AGENTCREW_TOKENIZER_DIR", str(tmp_path / "tokenizers"))
    monkeypatch.setattr(tokenizer, "ENCODING_SHA256", encoding_sha)
    return {
        "base": base,
        "tokenizer_sha256": hashlib.sha256(tokenizer_bytes).hexdigest(),
        "encoding_sha256": encoding_sha,
    }


def test_local_resources_verified(resource_dir):
    cfg = make_cfg(tokenizer_sha256=resource_dir["tokenizer_sha256"])
    paths = _local_resources(cfg)
    assert [p.name for p in paths] == ["tokenizer.json", "encoding.py"]
    assert paths[0] == resource_dir["base"] / "tokenizer.json"


def test_local_resources_missing_file(resource_dir, monkeypatch):
    (resource_dir["base"] / "encoding" / "encoding.py").unlink()
    cfg = make_cfg(tokenizer_sha256=resource_dir["tokenizer_sha256"])
    with pytest.raises(ContextBudgetError, match="官方编码文件缺失"):
        _local_resources(cfg)


def test_local_resources_sha_mismatch(resource_dir):
    cfg = make_cfg(tokenizer_sha256="0" * 64)
    with pytest.raises(ContextBudgetError, match="SHA 不一致：tokenizer.json"):
        _local_resources(cfg)


def test_local_resources_encoding_sha_pinned(tmp_path, monkeypatch):
    # 不替换常量：占位 encoding.py 与官方常量必然不一致 → 分支必须拒绝
    base = tmp_path / "tokenizers" / "deepseek-v4.1"
    (base / "encoding").mkdir(parents=True)
    (base / "tokenizer.json").write_bytes(b"placeholder-tokenizer")
    (base / "encoding" / "encoding.py").write_bytes(b"placeholder-encoding\n")
    monkeypatch.setenv("AGENTCREW_TOKENIZER_DIR", str(tmp_path / "tokenizers"))
    cfg = make_cfg(tokenizer_sha256=hashlib.sha256(b"placeholder-tokenizer").hexdigest())
    with pytest.raises(ContextBudgetError, match="SHA 不一致：encoding.py"):
        _local_resources(cfg)


def test_env_dir_takes_precedence_over_download(resource_dir, monkeypatch):
    calls = []

    def fail_download(cfg):
        calls.append(cfg)
        raise AssertionError("env 目录存在时不得走网络下载路径")

    monkeypatch.setattr(tokenizer, "_downloaded_resources", fail_download)
    cfg = make_cfg(tokenizer_sha256=resource_dir["tokenizer_sha256"])
    # 占位 tokenizer.json 不是合法 tokenizer：在 Tokenizer.from_file 处失败，
    # 但失败必须来自本地目录的资源本身，而不是网络下载路径
    with pytest.raises(Exception):
        load_counter.__wrapped__(cfg)
    assert not calls


def test_load_counter_rejects_unknown_model_without_env(monkeypatch):
    monkeypatch.delenv("AGENTCREW_TOKENIZER_DIR", raising=False)
    with pytest.raises(ContextBudgetError, match="TOKENIZER_UNAVAILABLE"):
        load_counter.__wrapped__(make_cfg(model="other-model"))


def test_load_counter_rejects_missing_window_without_env(monkeypatch):
    monkeypatch.delenv("AGENTCREW_TOKENIZER_DIR", raising=False)
    cfg = SlotConfig(model="deepseek-v4.1-flash", api_key="k",
                     base_url="https://opencode.ai/zen/go/v1",
                     provider="openai-compatible")
    with pytest.raises(ContextBudgetError, match="MODEL_WINDOW_UNKNOWN"):
        load_counter.__wrapped__(cfg)
