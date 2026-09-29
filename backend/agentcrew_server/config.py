"""配置链：环境变量 > data/config.json > 代码默认值（backend-service.md §4）。

合并与覆盖逻辑（merge_config / apply_env）是纯函数，参数化单测见
tests/test_config.py；文件读取与错误包装在 load_config。
C7 的 settings API 在此之上做版本化、脱敏与 ignored_fields。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

VALID_LOG_LEVELS = ("debug", "info", "warning", "error")

# 守门参数默认值来源：harness-session.md §6.3 守门参数总表；
# 材料限额来源：backend-service.md §5；models 槽位 C4 填充真实端点。
DEFAULTS: dict[str, Any] = {
    "log_level": "info",
    "gates": {
        "max_steps": 40,
        "stall_seconds": 600,
        "repeat_limit": 3,
        "global_concurrency": 8,
    },
    "limits": {
        "max_files": 20,
        "max_file_mb": 50,
        "max_folders": 5,
    },
    "models": {
        "main": {"provider": "glm", "model": "", "base_url": "", "api_key": ""},
        "aux": {"provider": "glm", "model": "", "base_url": "", "api_key": ""},
    },
}

# 环境变量名 → 配置点路径（__ 表示层级）。同字段 AGENTCREW_ 前缀优先于裸名
# （裸 LOG_LEVEL 为 backend-service.md §7 提及的通用名，保留兼容）。
_ENV_FIELDS: tuple[tuple[str, str], ...] = (
    ("LOG_LEVEL", "log_level"),
    ("AGENTCREW_LOG_LEVEL", "log_level"),
    ("AGENTCREW_GATES__MAX_STEPS", "gates.max_steps"),
    ("AGENTCREW_GATES__STALL_SECONDS", "gates.stall_seconds"),
    ("AGENTCREW_GATES__REPEAT_LIMIT", "gates.repeat_limit"),
    ("AGENTCREW_GATES__GLOBAL_CONCURRENCY", "gates.global_concurrency"),
    ("AGENTCREW_LIMITS__MAX_FILES", "limits.max_files"),
    ("AGENTCREW_LIMITS__MAX_FILE_MB", "limits.max_file_mb"),
    ("AGENTCREW_LIMITS__MAX_FOLDERS", "limits.max_folders"),
    ("AGENTCREW_MAIN__PROVIDER", "models.main.provider"),
    ("AGENTCREW_MAIN__MODEL", "models.main.model"),
    ("AGENTCREW_MAIN__BASE_URL", "models.main.base_url"),
    ("AGENTCREW_MAIN__API_KEY", "models.main.api_key"),
    ("AGENTCREW_AUX__PROVIDER", "models.aux.provider"),
    ("AGENTCREW_AUX__MODEL", "models.aux.model"),
    ("AGENTCREW_AUX__BASE_URL", "models.aux.base_url"),
    ("AGENTCREW_AUX__API_KEY", "models.aux.api_key"),
)


class ConfigError(RuntimeError):
    """配置不可用（文件损坏 / 值非法）——启动失败，明确报错非崩溃。"""


def merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """深合并：override 逐层覆盖 base（dict 递归，其余整体替换）。不修改入参。"""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get_path(source: Mapping[str, Any], dotted: str) -> Any:
    node: Any = source
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node: dict[str, Any] = target
    for part in parts[:-1]:
        existing = node.get(part)
        node = node.setdefault(part, {}) if not isinstance(existing, dict) else existing
    node[parts[-1]] = value


def _coerce(raw: str, reference: Any, dotted: str) -> Any:
    """按默认值类型转换环境变量值；不合法抛 ConfigError（fail-closed）。"""
    if isinstance(reference, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        try:
            return int(raw.strip())
        except ValueError:
            raise ConfigError(
                f"环境变量值非法：{dotted} 需要整数，得到 {raw!r}"
            ) from None
    return raw


def apply_env(
    merged: Mapping[str, Any], env: Mapping[str, str]
) -> tuple[dict[str, Any], list[str]]:
    """把环境变量覆盖进 merged，返回（生效配置, 被 env 覆盖的字段路径列表）。

    字段路径列表供 C7 settings API 的 ignored_fields / effective_source 使用。
    值非法抛 ConfigError。同字段后注册的变量名（AGENTCREW_ 前缀）胜出。
    """
    effective = copy.deepcopy(dict(merged))
    applied: dict[str, str] = {}
    for name, dotted in _ENV_FIELDS:
        raw = env.get(name)
        if raw is None or raw == "":
            continue
        applied[dotted] = raw
    for dotted, raw in applied.items():
        _set_path(effective, dotted, _coerce(raw, _get_path(DEFAULTS, dotted), dotted))
    validate(effective)
    return effective, sorted(applied)


def validate(cfg: Mapping[str, Any]) -> None:
    level = cfg.get("log_level")
    if level not in VALID_LOG_LEVELS:
        raise ConfigError(f"log_level 非法：{level!r}，允许值 {VALID_LOG_LEVELS}")


class Config:
    """生效配置（env > file > defaults 合并结果）。"""

    def __init__(self, values: dict[str, Any], env_fields: list[str], file_path: Path | None):
        self.values = values
        self.env_fields = env_fields
        self.file_path = file_path

    @property
    def log_level(self) -> str:
        return self.values["log_level"]

    def api_keys(self) -> list[str]:
        models = self.values.get("models", {})
        return [slot.get("api_key", "") for slot in models.values() if isinstance(slot, dict)]


def load_config(data_dir: Path, env: Mapping[str, str] | None = None) -> Config:
    """读 data/config.json 并套配置链。文件损坏 / 值非法 → ConfigError。"""
    path = data_dir / "config.json"
    file_obj: dict[str, Any] = {}
    exists = path.exists()
    if exists:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ConfigError(f"配置文件不可读（{path}）：{e}") from None
        if not isinstance(parsed, dict):
            raise ConfigError(f"配置文件顶层必须是 JSON 对象（{path}）")
        file_obj = parsed
    effective, env_fields = apply_env(
        merge_config(DEFAULTS, file_obj), env if env is not None else os.environ
    )
    return Config(effective, env_fields, path if exists else None)
