"""审计哈希链单测：compute_hash / 两级校验（篡改、删行、锚点）/ 链头快照。"""

from agentcrew_server.db.audit import (
    GENESIS_PREV_HASH,
    compute_hash,
    read_chain_head,
    snapshot_chain_head,
    verify_internal,
    verify_with_anchor,
)


def _build_chain(conn, n=3):
    prev = GENESIS_PREV_HASH
    for seq in range(1, n + 1):
        row_hash = compute_hash(
            seq, f"2026-01-01T00:00:{seq:02d}", "user", "u-owner",
            "permission.resolved", "tool_call", f"call-{seq}",
            '{"decision": "allow_once"}', prev,
        )
        conn.execute(
            "INSERT INTO audit_log (seq, ts, actor_type, actor_id, action,"
            " resource_type, resource_id, detail, prev_hash, hash)"
            " VALUES (?, ?, 'user', 'u-owner', 'permission.resolved', 'tool_call',"
            " ?, '{\"decision\": \"allow_once\"}', ?, ?)",
            (seq, f"2026-01-01T00:00:{seq:02d}", f"call-{seq}", prev, row_hash),
        )
        prev = row_hash
    return prev


def _make_conn(tmp_path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "audit.db"), isolation_level=None)
    conn.execute(
        "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY, ts TEXT, actor_type TEXT,"
        " actor_id TEXT, action TEXT, resource_type TEXT, resource_id TEXT,"
        " detail TEXT, prev_hash TEXT, hash TEXT)"
    )
    return conn


def test_hash_is_deterministic_and_chained():
    h1 = compute_hash(1, "t", "user", "u", "a", None, None, "{}", GENESIS_PREV_HASH)
    h2 = compute_hash(2, "t", "user", "u", "a", None, None, "{}", h1)
    assert h1 != h2 and len(h1) == 64


def test_verify_ok(tmp_path):
    conn = _make_conn(tmp_path)
    _build_chain(conn)
    result = verify_internal(conn)
    assert result.ok and result.checked_count == 3


def test_verify_detects_tamper(tmp_path):
    conn = _make_conn(tmp_path)
    _build_chain(conn)
    conn.execute("UPDATE audit_log SET action='hacked' WHERE seq=2")
    result = verify_internal(conn)
    assert not result.ok and result.broken_at_seq == 2


def test_verify_detects_deleted_row(tmp_path):
    conn = _make_conn(tmp_path)
    _build_chain(conn)
    conn.execute("DELETE FROM audit_log WHERE seq=2")
    result = verify_internal(conn)
    assert not result.ok and result.broken_at_seq == 3


def test_anchor_compare(tmp_path):
    conn = _make_conn(tmp_path)
    head_hash = _build_chain(conn)
    head_path = tmp_path / "chain-head.txt"
    assert snapshot_chain_head(conn, head_path) == 3
    assert read_chain_head(head_path) == (3, head_hash)

    # ① 内部一致 + ② 锚点一致 → 通过
    assert verify_with_anchor(conn, head_path).ok

    # 锚点文件被换成不匹配的 hash → 锚点级失败
    head_path.write_text(f"3 {'f' * 64}\n")
    result = verify_with_anchor(conn, head_path)
    assert not result.ok and result.broken_at_seq == 3


def test_corrupt_anchor_file_fails_closed(tmp_path):
    """外审回稿：损坏的 chain-head.txt ≠ 不存在——不得静默降级为无锚点校验。"""
    conn = _make_conn(tmp_path)
    _build_chain(conn)
    head_path = tmp_path / "chain-head.txt"
    head_path.write_text("garbage-not-a-pair\n")
    result = verify_with_anchor(conn, head_path)
    assert not result.ok and "损坏" in result.reason

    head_path.write_text("notanumber abcdef\n")
    result = verify_with_anchor(conn, head_path)
    assert not result.ok and "损坏" in result.reason

    # 真不存在的文件仍按"无锚点"通过（① 已覆盖全部条目）
    (tmp_path / "absent.txt").unlink(missing_ok=True)
    assert verify_with_anchor(conn, tmp_path / "absent.txt").ok


def test_snapshot_empty_chain_no_file(tmp_path):
    conn = _make_conn(tmp_path)
    head_path = tmp_path / "chain-head.txt"
    assert snapshot_chain_head(conn, head_path) is None
    assert not head_path.exists()
