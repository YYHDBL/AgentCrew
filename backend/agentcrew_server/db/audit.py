"""审计哈希链：校验与链头快照（governance §3）。

- hash = sha256("seq|ts|actor_type|actor_id|action|resource_type|resource_id|detail|prev_hash")
  —— canonical 串以 | 连接，空值取空串；创世 prev_hash = 64 个 0。
- 写入方（C6 起）必须复用本模块的 compute_hash，保证链式一致。
- 校验两级：① 内部链全量重算（含 seq 连续性——删行会断链）；
  ② chain-head.txt 锚点比对（覆盖至最近一次快照）。
- 链头快照：每 100 条及每次正常退出原子写 data/chain-head.txt（tmp+rename）。
M0-C2 只建表 + 校验 + 退出快照（audit_log 尚无写入方，空链 = 通过）。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

GENESIS_PREV_HASH = "0" * 64

_CHAIN_COLUMNS = (
    "seq, ts, actor_type, actor_id, action, resource_type, resource_id,"
    " detail, prev_hash, hash"
)


def compute_hash(
    seq: int, ts: str, actor_type: str, actor_id: str, action: str,
    resource_type: str | None, resource_id: str | None, detail: str,
    prev_hash: str,
) -> str:
    canonical = "|".join(
        str(part) for part in (
            seq, ts, actor_type, actor_id, action,
            resource_type or "", resource_id or "", detail, prev_hash,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ChainVerification:
    ok: bool
    broken_at_seq: int | None = None
    reason: str | None = None
    checked_count: int = 0


def verify_internal(conn: sqlite3.Connection) -> ChainVerification:
    """① 内部链一致性：全量重算 + seq 连续（删行/改行都会断）。"""
    expected_seq = 1
    prev_hash = GENESIS_PREV_HASH
    count = 0
    for row in conn.execute(f"SELECT {_CHAIN_COLUMNS} FROM audit_log ORDER BY seq"):
        (seq, ts, actor_type, actor_id, action, resource_type, resource_id,
         detail, row_prev, row_hash) = row
        count += 1
        if seq != expected_seq:
            return ChainVerification(
                False, seq, f"seq 不连续：期望 {expected_seq}，实际 {seq}（疑似删行）", count
            )
        if row_prev != prev_hash:
            return ChainVerification(False, seq, f"prev_hash 与上一条 hash 不符", count)
        recomputed = compute_hash(
            seq, ts, actor_type, actor_id, action,
            resource_type, resource_id, detail, row_prev,
        )
        if recomputed != row_hash:
            return ChainVerification(False, seq, "hash 重算不一致（疑似篡改）", count)
        prev_hash = row_hash
        expected_seq += 1
    return ChainVerification(True, checked_count=count)


def read_chain_head(path: Path) -> tuple[int, str] | None:
    """链头快照文件 → (seq, hash)；文件不存在返回 None。"""
    if not path.exists():
        return None
    parts = path.read_text(encoding="utf-8").strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), parts[1]
    except ValueError:
        return None


def verify_with_anchor(
    conn: sqlite3.Connection, chain_head_path: Path
) -> ChainVerification:
    """两级校验：①内部链 → ②快照锚点（锚点 seq 处的 hash 必须与文件一致）。"""
    internal = verify_internal(conn)
    if not internal.ok:
        return internal
    anchor = read_chain_head(chain_head_path)
    if anchor is None:
        return internal  # 无快照文件：①已覆盖全部条目
    seq, expected_hash = anchor
    row = conn.execute(
        "SELECT hash FROM audit_log WHERE seq = ?", (seq,)
    ).fetchone()
    if row is None:
        return ChainVerification(False, seq, f"锚点 seq={seq} 在库中不存在", internal.checked_count)
    if row[0] != expected_hash:
        return ChainVerification(False, seq, f"锚点 hash 与链头快照不符", internal.checked_count)
    return internal


def snapshot_chain_head(conn: sqlite3.Connection, path: Path) -> int | None:
    """把链头 (seq, hash) 原子写库外文件；空链返回 None（不写文件）。"""
    row = conn.execute(
        "SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"{row[0]} {row[1]}\n", encoding="utf-8")
    os.replace(tmp, path)
    return int(row[0])
