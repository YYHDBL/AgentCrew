"""员工提交计划，执行结果只读取已经真人批准的实际计划。"""

import json

from ...cron.models import ScheduleTaskInput
from ..metadata import ToolResult

SCHEDULE_TASK_SCHEMA = ScheduleTaskInput.model_json_schema()


async def _schedule_task(invocation, context):
    if context.schedule_proposal is None:
        raise RuntimeError("缺少计划提案服务")
    result = await context.schedule_proposal(invocation, context)
    return ToolResult(ok=True, output=json.dumps(result, ensure_ascii=False), details={"job_id": result["id"]})
