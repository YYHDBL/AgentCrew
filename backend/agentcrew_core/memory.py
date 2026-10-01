"""记忆条目计算：Markdown 边界、身份、配额和状态迁移。"""

from __future__ import annotations

import copy
import hashlib
import re
import uuid

from markdown_it import MarkdownIt

QUOTAS = {"user": 1400, "workspace": 2200, "soul": 2700}
DELIMITER = "\n\n§\n\n"
_MARKDOWN = MarkdownIt("commonmark")
_HIGH_RISK = re.compile(r"财务|银行|账号|账户|金额|支付|法律|合同|诉讼|凭据|密码|token|api.?key", re.I)
_CREDENTIAL = re.compile(r"-----BEGIN .*PRIVATE KEY-----|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}|(?:密码|password|api.?key|token)\s*[:=：]\s*\S+", re.I)


def failure(code: str, message: str, **details) -> dict:
    return {"error": code, "message": message, "details": details}


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_entries(text: str) -> list[str]:
    """根据 Markdown 顶层段落的行映射分隔，保留代码块和正文空格。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(keepends=True)
    boundaries = [t.map for t in _MARKDOWN.parse(text)
                  if t.type == "inline" and t.level == 1 and t.content == "§"
                  and t.map and lines[t.map[0]].rstrip("\n") == "§"]
    entries = []
    start = 0
    for first, last in boundaries:
        entry = "".join(lines[start:first]).strip("\n")
        if entry.strip():
            entries.append(entry)
        start = last
    entry = "".join(lines[start:]).strip("\n")
    if entry.strip():
        entries.append(entry)
    return entries


def entry_hash(text: str) -> str:
    return sha256(next(line for line in text.splitlines() if line.strip()).encode("utf-8"))


def render_entries(entries: list[dict]) -> str:
    return DELIMITER.join(e["text"] for e in entries if e["state"] != "archived")


def transform(entries: list[dict], operations: list[dict], source: dict,
              basis: str, now: str) -> list[dict] | dict:
    working = copy.deepcopy(entries)
    for op in operations:
        action = op.get("action")
        if action not in {"add", "edit", "archive", "restore", "pin", "unpin"}:
            return failure("VALIDATION_ERROR", "记忆动作无效")
        if action == "add":
            item = {"entry_id": uuid.uuid4().hex, "state": "active", "hits": 0,
                    "last_hit_at": None, "created_at": now, "approved_by": None,
                    "approved_at": None}
            working.append(item)
        else:
            matched = [e for e in working if e["entry_hash"] == op.get("entry_hash")]
            if not matched:
                return failure("NOT_FOUND", "条目哈希不存在")
            item = matched[0]
        if action in {"add", "edit"}:
            text = op.get("text")
            if not isinstance(text, str) or not text.strip() or len(parse_entries(text)) != 1:
                return failure("VALIDATION_ERROR", "正文必须包含一个非空 Markdown 条目")
            text = parse_entries(text)[0]
            if _CREDENTIAL.search(text):
                return failure("CREDENTIAL_REJECTED", "记忆正文包含凭据")
            item.update(text=text, entry_hash=entry_hash(text), source=source,
                        basis=basis, needs_review=bool(_HIGH_RISK.search(text)),
                        approved_by=None, approved_at=None)
        elif action == "archive":
            if item["state"] == "archived":
                return failure("INVALID_TRANSITION", "条目已经归档")
            item["previous_state"] = item["state"]
            item["state"] = "archived"
            item["archived_at"] = now
        elif action == "restore":
            if item["state"] != "archived":
                return failure("INVALID_TRANSITION", "条目尚未归档")
            item["state"] = item.pop("previous_state", "active")
            item.pop("archived_at", None)
        elif action == "pin":
            if item["state"] == "archived":
                return failure("INVALID_TRANSITION", "归档条目需要先恢复")
            item["state"] = "pinned"
        elif action == "unpin":
            if item["state"] != "pinned":
                return failure("INVALID_TRANSITION", "条目尚未固定")
            item["state"] = "active"
        if len({e["entry_hash"] for e in working}) != len(working):
            return failure("ENTRY_HASH_CONFLICT", "同库条目的首行哈希冲突")
    return working
