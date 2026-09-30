"""审计哈希链：校验与链头快照（governance §3）。

- hash = sha256("v2|" + JSON 数组 [seq, ts, actor_type, actor_id, action,
  resource_type, resource_id, detail, prev_hash])（ensure_ascii=False、
  紧凑分隔符）——v2（外审回稿 S10）：v1 用 "|" 直接拼接存在字段边界歧义
  （actor_id 含 "|" 时可与相邻字段互换而哈希不变），JSON 数组无歧义；
  M0 未发布，直接切换不保留 v1 校验，创世 prev_hash = 64 个 0。
- 写入方（C6 起）必须复用本模块的 compute_hash，保证链式一致。
- 校验两级：① 内部链全量重算（含 seq 连续性——删行会断链）；
  ② chain-head.txt 锚点比对（覆盖至最近一次快照）。
- 链头快照：每 100 条及每次正常退出原子写 data/chain-head.txt（tmp+rename）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

GENESIS_PREV_HASH = "0" * 64
HASH_ALGO_VERSION = "v2"

_CHAIN_COLUMNS = (
    "seq, ts, actor_type, actor_id, action, resource_type, resource_id,"
    " detail, prev_hash, hash"
)


def compute_hash(
    seq: int, ts: str, actor_type: str, actor_id: str, action: str,
    resource_type: str | None, resource_id: str | None, detail: str,
    prev_hash: str,
) -> str:
    canonical = HASH_ALGO_VERSION + "|" + json.dumps(
        [seq, ts, actor_type, actor_id, action,
         resource_type or "", resource_id or "", detail, prev_hash],
        ensure_ascii=False, separators=(",", ":"),
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


class CorruptChainHeadError(RuntimeError):
    """chain-head.txt 存在但无法解析——按 fail-closed 处理，不当作"无快照"。"""


def read_chain_head(path: Path) -> tuple[int, str] | None:
    """链头快照文件 → (seq, hash)；文件不存在返回 None，损坏抛 CorruptChainHeadError。"""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        raise CorruptChainHeadError(f"链头快照文件损坏（无法解析）：{path}")
    try:
        seq = int(parts[0])
    except ValueError:
        raise CorruptChainHeadError(f"链头快照文件损坏（seq 非整数）：{path}") from None
    return seq, parts[1]


def verify_with_anchor(
    conn: sqlite3.Connection, chain_head_path: Path
) -> ChainVerification:
    """两级校验：①内部链 → ②快照锚点（锚点 seq 处的 hash 必须与文件一致）。

    锚点文件损坏 ≠ 文件不存在：快照由本进程原子写入，损坏意味着篡改或磁盘
    故障——fail-closed 判定校验失败（进只读诊断模式），不得静默降级为无锚点。
    """
    internal = verify_internal(conn)
    if not internal.ok:
        return internal
    try:
        anchor = read_chain_head(chain_head_path)
    except CorruptChainHeadError as e:
        return ChainVerification(False, None, str(e), internal.checked_count)
    if anchor is None:
        return internal  # 无快照文件（从未写过）：①已覆盖全部条目
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


SNAPSHOT_EVERY = 100  # 每追加 100 条快照链头（governance §3）


def append_audit(
    conn: sqlite3.Connection,
    *,
    ts: str,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: str = "{}",
) -> int:
    """哈希链追加一条审计记录（在写通道事务内调用）；返回 seq。

    链头快照（每 100 条）由调用方在提交后按需触发——见 chain_head_path 用法。
    """
    last = conn.execute(
        "SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq = (last[0] + 1) if last else 1
    prev_hash = last[1] if last else GENESIS_PREV_HASH
    row_hash = compute_hash(
        seq, ts, actor_type, actor_id, action,
        resource_type, resource_id, detail, prev_hash,
    )
    conn.execute(
        "INSERT INTO audit_log (seq, ts, actor_type, actor_id, action,"
        " resource_type, resource_id, detail, prev_hash, hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (seq, ts, actor_type, actor_id, action,
         resource_type, resource_id, detail, prev_hash, row_hash),
    )
    return seq
