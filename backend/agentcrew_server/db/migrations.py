"""版本化迁移（backend-service.md §1）。

- schema_migrations 自举（CREATE IF NOT EXISTS，幂等）
- 目标版本 > 当前版本：先 VACUUM INTO 升级前快照（含 WAL 已提交事务——
  禁止裸复制主库文件），保留最近 3 份；再逐条迁移执行，**每条迁移单独事务**
  （SQLite DDL 可回滚）
- 目标版本 < 当前版本：MigrationConflictError（拒绝启动，报双版本号）
- 迁移中途失败：该事务回滚 + MigrationFailedError（调用方进入只读诊断模式，
  提示从 backups/ 还原）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .schema_v1 import V1_STATEMENTS
from .schema_v2 import V2_STATEMENTS
from .schema_v3 import V3_STATEMENTS
from .schema_v4 import V4_STATEMENTS
from .schema_v5 import V5_STATEMENTS
from .schema_v6 import V6_STATEMENTS
from .schema_v7 import V7_STATEMENTS
from .schema_v8 import V8_STATEMENTS
from .schema_v9 import V9_STATEMENTS
from .schema_v10 import V10_STATEMENTS
from .schema_v11 import V11_STATEMENTS

_SNAPSHOT_KEEP = 3
_BUSY_RETRIES = 3


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
    rebuild_foreign_keys: bool = False


MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="M0 初始建表", statements=V1_STATEMENTS),
    Migration(version=2, name="C7 客户端幂等键", statements=V2_STATEMENTS),
    Migration(version=3, name="M1 三库与记忆账本", statements=V3_STATEMENTS),
    Migration(version=4, name="M1 会话快照与注入统计", statements=V4_STATEMENTS),
    Migration(version=5, name="M1 trigram 检索与使用去重", statements=V5_STATEMENTS),
    Migration(version=6, name="M1 Skill 生命周期与读取修订", statements=V6_STATEMENTS),
    Migration(version=7, name="M1 辅助作业与唯一任务摘要", statements=V7_STATEMENTS),
    Migration(version=8, name="M1 上下文压缩检查点", statements=V8_STATEMENTS),
    Migration(version=9, name="M1 后台提炼与独立审批", statements=V9_STATEMENTS),
    Migration(version=10, name="M1 确定性遗忘治理", statements=V10_STATEMENTS, rebuild_foreign_keys=True),
    Migration(version=11, name="M2 治理资源与授权", statements=V11_STATEMENTS),
)


class MigrationConflictError(RuntimeError):
    """目标版本 < 当前版本——拒绝启动，报双版本号。"""

    def __init__(self, current: int, target: int):
        self.current = current
        self.target = target
        super().__init__(
            f"迁移版本冲突：数据库当前版本 v{current} 高于代码目标版本 v{target}"
            f"（库由更新版本写入，请用对应版本程序启动或回滚数据库）"
        )


class MigrationFailedError(RuntimeError):
    """迁移中途失败——该迁移事务已回滚；进入只读诊断模式。"""

    def __init__(self, version: int, statement_no: int, cause: str):
        self.version = version
        self.statement_no = statement_no
        super().__init__(
            f"迁移 v{version} 第 {statement_no} 条语句失败（已回滚该事务）：{cause}"
        )


@dataclass
class MigrationResult:
    status: str  # "up_to_date" | "applied" | "conflict" | "failed"
    current_version: int = 0
    target_version: int = 0
    applied_versions: list[int] = field(default_factory=list)
    snapshot_path: str | None = None
    error: Exception | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _bootstrap_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0])


def _snapshot_before_upgrade(conn: sqlite3.Connection, backups_dir: Path, current: int) -> str:
    """VACUUM INTO 自洽快照（含 WAL 已提交事务）；保留最近 3 份。"""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backups_dir / f"db-v{current}-{stamp}.sqlite"
    conn.execute("VACUUM INTO ?", (str(path),))
    snapshots = sorted(
        backups_dir.glob("db-v*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in snapshots[_SNAPSHOT_KEEP:]:
        stale.unlink()
    return str(path)


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """单条迁移一个事务；失败回滚并抛 MigrationFailedError。"""
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if migration.rebuild_foreign_keys and foreign_keys:
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for no, statement in enumerate(migration.statements, start=1):
            try:
                conn.execute(statement)
            except sqlite3.Error as e:
                raise MigrationFailedError(migration.version, no, str(e)) from None
        if migration.rebuild_foreign_keys:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationFailedError(migration.version, len(migration.statements), f"外键校验失败：{violations}")
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, _now()),
        )
        conn.execute("COMMIT")
    except MigrationFailedError:
        conn.execute("ROLLBACK")
        raise
    except sqlite3.OperationalError as e:
        conn.execute("ROLLBACK")
        raise MigrationFailedError(migration.version, 0, f"开启事务失败：{e}") from None
    finally:
        if migration.rebuild_foreign_keys and foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON")


def run_migrations(conn: sqlite3.Connection, backups_dir: Path) -> MigrationResult:
    """启动序列第 3-4 步：升级前快照 + 执行迁移。结果四态见 MigrationResult。"""
    target = MIGRATIONS[-1].version if MIGRATIONS else 0

    for attempt in range(_BUSY_RETRIES + 1):
        try:
            _bootstrap_version_table(conn)
            break
        except sqlite3.OperationalError as e:
            if not _is_busy(e) or attempt == _BUSY_RETRIES:
                raise
    current = _current_version(conn)

    if target < current:
        return MigrationResult(
            status="conflict", current_version=current, target_version=target,
            error=MigrationConflictError(current, target),
        )
    if target == current:
        return MigrationResult(
            status="up_to_date", current_version=current, target_version=target
        )

    result = MigrationResult(
        status="applied", current_version=current, target_version=target
    )
    try:
        result.snapshot_path = _snapshot_before_upgrade(conn, backups_dir, current)
    except sqlite3.OperationalError as e:
        if _is_busy(e):
            # 快照被并发读事务短暂阻塞：重试一次（PASSIVE 语义，不无限等待）
            try:
                result.snapshot_path = _snapshot_before_upgrade(conn, backups_dir, current)
            except sqlite3.Error as retry_err:
                raise MigrationFailedError(target, 0, f"升级前快照失败：{retry_err}") from None
        else:
            raise MigrationFailedError(target, 0, f"升级前快照失败：{e}") from None

    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        _apply_migration(conn, migration)
        result.applied_versions.append(migration.version)
    return result
