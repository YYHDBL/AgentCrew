"""配置 API 服务（M0-C7，backend-service.md §4 版本化配置）。

- GET 永不回完整 key：api_key_configured + 尾 4 位 hint + 槽级
  effective_source（env|file——任一该槽字段被环境变量覆盖即为 env）；
- PATCH 语义：api_key 缺省 = 不变；清空须显式 api_key_clear；被 env 覆盖
  的字段 200 + ignored_fields（文件已存但非生效值）；只影响后续执行绑定
  的配置版本（运行中执行保持绑定版——C8 起 attempt 记 context_fingerprint）；
- 写序（§4）：先写文件（tmp+rename 原子替换）→ 成功才重载并切换内存生效
  配置；文件写失败 → SettingsWriteFailed（500 CONFIG_WRITE_FAILED），内存
  不变。
- 一切配置变更入审计链（外审回稿：无"用户可见契约豁免"）。跨文件 + 库无法
  单原子，做**可恢复的审计待办**：审计写失败时把完整审计记录落
  data/audit-pending.jsonl（响应 audit.status="pending"），下次成功入链时
  按原序先补待办再入新记录（同一事务），随后清空待办文件；待办连文件都
  落不了 → audit.status="failed"（如实告警，不谎报 ok）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULTS, Config, ConfigError, apply_env, load_config, merge_config
from .db.audit import SNAPSHOT_EVERY, append_audit, snapshot_chain_head
from .db.write_channel import WriteChannel

_log = logging.getLogger("agentcrew.settings")

_ALLOWED_TOP_KEYS = {"models", "gates", "limits", "api_key_clear"}
_ALLOWED_SLOT_FIELDS = {"provider", "model", "base_url", "api_key", "max_tokens"}
_ALLOWED_GATES = {"max_steps", "stall_seconds", "repeat_limit",
                  "global_concurrency", "token_budget"}
_ALLOWED_LIMITS = {"max_files", "max_file_mb", "max_folders"}
# 运行期构造、PATCH 仅落盘待重启的字段（backend-service §4）
_RESTART_REQUIRED_FIELDS = {"gates.global_concurrency"}


class SettingsWriteFailed(RuntimeError):
    """配置文件写入失败——内存与生效配置未变（§4）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettingsService:
    def __init__(self, config: Config, data_dir: Path, channel: WriteChannel,
                 chain_head_path: Path, env: dict[str, str] | None = None):
        self._config = config
        self._data_dir = data_dir
        self._path = data_dir / "config.json"
        self._channel = channel
        self._chain_head_path = chain_head_path
        self._pending_path = data_dir / "audit-pending.jsonl"
        # 审计补链串行（外审回稿）：读待办→入链→清待办 必须整体互斥，
        # 否则并发 PATCH 会把同一待办补两遍
        self._audit_lock = asyncio.Lock()
        # PATCH 后重载必须走同一环境（测试注入合成 env；缺省 os.environ）
        self._env = env if env is not None else os.environ

    @property
    def config(self) -> Config:
        return self._config

    # ── 读侧（脱敏）─────────────────────────────────────────────

    def _file_object(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}  # 文件损坏由启动链报错；GET 只呈现生效值
        return parsed if isinstance(parsed, dict) else {}

    def get_view(self) -> dict[str, Any]:
        cfg = self._config.values
        models: dict[str, Any] = {}
        for slot in ("main", "aux"):
            entry = cfg.get("models", {}).get(slot, {})
            key = entry.get("api_key", "") or ""
            env_sourced = any(p.startswith(f"models.{slot}.")
                              for p in self._config.env_fields)
            models[slot] = {
                "provider": entry.get("provider", ""),
                "model": entry.get("model", ""),
                "api_key_configured": bool(key),
                "api_key_hint": key[-4:] if key else "",
                "effective_source": "env" if env_sourced else "file",
            }
        return {
            "config_version": str(self._file_object().get("config_version", 0)),
            "models": models,
            "gates": dict(cfg.get("gates", {})),
            "limits": dict(cfg.get("limits", {})),
        }

    # ── 写侧（版本化 + 原子写 + 审计）───────────────────────────

    async def patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        touched = self._validate_shape(patch)
        file_obj = self._file_object()
        normalized = {k: v for k, v in patch.items() if k != "api_key_clear"}
        if isinstance(normalized.get("models"), dict):
            # api_key 缺省 = 不变；空串不当作清空（清空须显式 api_key_clear）
            normalized["models"] = {
                slot: {f: v for f, v in entry.items()
                       if not (f == "api_key" and v == "")}
                for slot, entry in normalized["models"].items()
                if isinstance(entry, dict)}
        new_file = merge_config(file_obj, normalized)
        cleared: list[str] = list(patch.get("api_key_clear") or [])
        for slot in cleared:
            new_file.setdefault("models", {}).setdefault(slot, {})
            new_file["models"][slot]["api_key"] = ""
        old_version = file_obj.get("config_version", 0)
        new_file["config_version"] = int(old_version) + 1

        # 校验在写之前（走真实配置链：默认值合并 + env 覆盖；失败即 422，
        # 文件与内存都不动）
        try:
            apply_env(merge_config(DEFAULTS, new_file), self._env)
        except ConfigError as e:
            raise ValueError(str(e)) from None

        try:
            self._atomic_write(new_file)
        except OSError as e:
            raise SettingsWriteFailed(
                f"配置文件写入失败（{self._path}）：{e}") from None

        # 文件已成新事实：重载生效配置（用构造时的同一环境，覆盖链一致）
        self._config = load_config(self._data_dir, self._env)
        ignored = sorted(p for p in self._config.env_fields if p in touched)
        audit_status = await self._audit(
            new_file["config_version"], touched, cleared)
        # 需重启生效的字段（外审二轮建议4）：信号量在进程启动期构造，
        # PATCH 该字段只落盘——响应明示，不谎报已生效
        restart_required = sorted(
            p for p in touched if p in _RESTART_REQUIRED_FIELDS)
        return {"settings": self.get_view(), "ignored_fields": ignored,
                "restart_required": restart_required,
                "audit": {"status": audit_status}}

    def _validate_shape(self, patch: dict[str, Any]) -> list[str]:
        """白名单校验 + 收集本次写入的字段路径（审计与 ignored_fields 共用）。"""
        touched: list[str] = []
        for key, value in patch.items():
            if key not in _ALLOWED_TOP_KEYS:
                raise ValueError(f"未知配置段：{key}")
            if key == "api_key_clear":
                if not isinstance(value, list) or \
                        any(s not in ("main", "aux") for s in value):
                    raise ValueError("api_key_clear 需要 [main|aux] 列表")
                touched += [f"models.{s}.api_key" for s in value]
            elif key == "models":
                if not isinstance(value, dict):
                    raise ValueError("models 需要对象")
                for slot, entry in value.items():
                    if slot not in ("main", "aux"):
                        raise ValueError(f"未知模型槽：{slot}")
                    if not isinstance(entry, dict):
                        raise ValueError(f"models.{slot} 需要对象")
                    for field in entry:
                        if field not in _ALLOWED_SLOT_FIELDS:
                            raise ValueError(f"未知字段：models.{slot}.{field}")
                        touched.append(f"models.{slot}.{field}")
            else:
                allowed = _ALLOWED_GATES if key == "gates" else _ALLOWED_LIMITS
                if not isinstance(value, dict):
                    raise ValueError(f"{key} 需要对象")
                for field in value:
                    if field not in allowed:
                        raise ValueError(f"未知字段：{key}.{field}")
                    touched.append(f"{key}.{field}")
        return touched

    def _atomic_write(self, obj: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(tmp, self._path)

    async def _audit(self, config_version: int, touched: list[str],
                     cleared: list[str]) -> str:
        """一切配置变更入审计链（外审回稿）。返回 "ok" | "pending" | "failed"。

        成功路径：待办（若有）按原序补链 + 本条新记录，同一事务；提交后清
        待办文件。失败路径：完整记录落 audit-pending.jsonl 待下次补链。"""
        record = {
            "ts": _now(), "actor_type": "user", "actor_id": "owner",
            "action": "settings.updated", "resource_type": "config",
            "resource_id": str(config_version),
            "detail": json.dumps({
                "config_version": config_version,
                "fields": touched,
                "api_key_cleared": cleared,  # 只记槽名，绝不记 key 值
            }, ensure_ascii=False, sort_keys=True),
        }

        def write(conn):
            conn.execute("BEGIN IMMEDIATE")
            try:
                seq = 0
                for pending in self._read_pending():
                    seq = append_audit(conn, **pending)
                seq = append_audit(conn, **record)
                conn.execute("COMMIT")
                return seq
            except Exception:
                conn.execute("ROLLBACK")
                raise

        async with self._audit_lock:
            try:
                seq = await self._channel.execute(write)
            except Exception:  # noqa: BLE001 —— 文件已是新值；如实落待办不谎报
                _log.exception(
                    "settings.audit_failed 配置已更新到 v%s 但审计入链失败，"
                    "已落待办等下次成功时补链", config_version)
                return self._record_pending(record)
            self._pending_path.unlink(missing_ok=True)
        if seq % SNAPSHOT_EVERY == 0:
            await self._channel.execute(
                lambda conn: snapshot_chain_head(conn, self._chain_head_path))
        return "ok"

    def _read_pending(self) -> list[dict]:
        if not self._pending_path.exists():
            return []
        lines = [ln for ln in
                 self._pending_path.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        # 解析失败直接抛出（按审计失败处理再落一次待办）——待办文件由本
        # 服务单行 JSON 追加，损坏意味着磁盘故障，静默跳过等于丢审计
        return [json.loads(ln) for ln in lines]

    def _record_pending(self, record: dict) -> str:
        try:
            with self._pending_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            _log.critical(
                "settings.audit_pending_unwritable 审计待办 %s 也无法落盘"
                "（v%s 的审计记录可能丢失）", self._pending_path,
                record["resource_id"])
            return "failed"
        return "pending"
