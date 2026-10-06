"""按当前权限只读真实任务、事件及调用投影。"""

import json
from ..metadata import ToolResult

TRACE_READ_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"task_run_id": {"type": "string", "minLength": 1},
        "after_seq": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        "through_global_seq": {"type": "integer", "minimum": 1}}, "required": ["task_run_id"]}


async def _trace_read(invocation, context):
    if context.trace_reader is None:
        raise RuntimeError("缺少真实轨迹读取服务")
    result = await context.trace_reader(invocation, context)
    return ToolResult(ok=True, output=json.dumps(result, ensure_ascii=False), details={"source_global_seq": result["at_global_seq"]})
