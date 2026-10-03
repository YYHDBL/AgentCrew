"""经源码核查并固定 SHA 的官方 tokenizer 和消息编码实现。"""

from __future__ import annotations

import hashlib
import importlib.util
from functools import lru_cache
from pathlib import Path

from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from agentcrew_core.memory.budget import ContextBudgetError, RequestCounter
from agentcrew_core.provider.glm_anthropic import SlotConfig

TOKENIZER_FIELDS = ("context_window", "context_window_source", "tokenizer_repository",
                    "tokenizer_revision", "tokenizer_sha256", "prompt_format")
DEEPSEEK_V41 = {
    "context_window": 1_000_000,
    "context_window_source": "https://models.dev/api.json#opencode-go/deepseek-v4.1-flash",
    "tokenizer_repository": "deepseek-ai/DeepSeek-V4.1-Flash",
    "tokenizer_revision": "2cba9e42aa026125f3ed06c6d98c1db82f7ca027",
    "tokenizer_sha256": "c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b",
    "prompt_format": "deepseek-v4.1",
}
ENCODING_SHA256 = "502bdaec8a3fd88ebc24c4721a7038fbe42f2063c664638127056107920035c1"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "tokenizers"


@lru_cache(maxsize=8)
def load_counter(cfg: SlotConfig) -> RequestCounter:
    if cfg.context_window <= 0 or not cfg.context_window_source:
        raise ContextBudgetError("MODEL_WINDOW_UNKNOWN：模型槽未配置实际窗口和来源")
    if cfg.provider != "openai-compatible" or cfg.model != "deepseek-v4.1-flash" \
            or cfg.base_url != "https://opencode.ai/zen/go/v1" \
            or any(getattr(cfg, key) != value for key, value in DEEPSEEK_V41.items()):
        raise ContextBudgetError("TOKENIZER_UNAVAILABLE：模型、服务或编码版本缺少经核查的计数依据")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, expected in (("tokenizer.json", cfg.tokenizer_sha256),
                           ("encoding/encoding.py", ENCODING_SHA256)):
        path = Path(hf_hub_download(repo_id=cfg.tokenizer_repository,
            filename=name, revision=cfg.tokenizer_revision, cache_dir=CACHE_DIR, token=False))
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ContextBudgetError(f"TOKENIZER_UNAVAILABLE：官方编码文件 SHA 不一致：{name}")
        paths.append(path)
    spec = importlib.util.spec_from_file_location("agentcrew_deepseek_v41_encoding", paths[1])
    if spec is None or spec.loader is None:
        raise ContextBudgetError("TOKENIZER_UNAVAILABLE：官方编码实现无法加载")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return RequestCounter(Tokenizer.from_file(str(paths[0])), module.encode_messages)
