"""管理分页的排他游标，绑定身份、范围及排序条件。"""

import hashlib
import json
import re


class MemoryCursorError(ValueError):
    pass


def memory_page(items, binding, limit, after, *, key):
    digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    start = 0
    if after is not None:
        match = re.fullmatch(r"([a-f0-9]{64}):([A-Za-z0-9_-]{1,128})", after)
        if match is None or match[1] != digest:
            raise MemoryCursorError("分页游标与当前身份、范围、过滤或修订不一致")
        position = next((index for index, item in enumerate(items) if str(item[key]) == match[2]), None)
        if position is None:
            raise MemoryCursorError("分页游标对应的记录已不存在")
        start = position + 1
    selected = items[start:start + limit]
    next_after = f"{digest}:{selected[-1][key]}" if selected and start + limit < len(items) else None
    return {"items": selected, "next_after": next_after}
