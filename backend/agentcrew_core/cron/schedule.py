"""成熟 croniter 解析及 ZoneInfo 时间计算，调用者显式提供当前时刻。"""

import re
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from croniter import croniter

MAX_TIMESTAMP_MS = 253402300799999


class ScheduleError(ValueError):
    """计划参数或计算结果不能在支持的日期范围内表示。"""


@lru_cache(maxsize=1)
def timezones():
    return frozenset(available_timezones()) - {"localtime", "posixrules"}


def validate_schedule(schedule):
    if schedule["tz"] not in timezones():
        raise ScheduleError("计划时区必须为有效 IANA 名称")
    if schedule["kind"] == "every":
        if type(schedule["every_ms"]) is not int or not 1000 <= schedule["every_ms"] <= MAX_TIMESTAMP_MS:
            raise ScheduleError("every 间隔超出支持的毫秒范围")
    elif schedule["kind"] == "at":
        if type(schedule["at_ms"]) is not int or not 0 <= schedule["at_ms"] <= MAX_TIMESTAMP_MS:
            raise ScheduleError("at 必须为有效 UTC Unix毫秒")
    elif schedule["kind"] == "cron":
        expression = schedule["expr"]
        if len(expression.split()) not in {5, 6} or re.search(r"(?i)(?:^|[\s,])(?:H|R)(?:$|[\s,/(\-])", expression):
            raise ScheduleError("cron 仅接受确定性的五字段或末尾秒字段表达式")
        if not croniter.is_valid(expression, strict=True):
            raise ScheduleError("cron 表达式无效或无法对应实际日期")
    else:
        raise ScheduleError("计划类型无效")


def next_run_at(schedule, now_ms, anchor_ms):
    validate_schedule(schedule)
    if schedule["kind"] == "at":
        return schedule["at_ms"]
    if schedule["kind"] == "every":
        interval = schedule["every_ms"]
        result = anchor_ms + max(1, (now_ms - anchor_ms) // interval + 1) * interval
        if result > MAX_TIMESTAMP_MS:
            raise ScheduleError("下一次运行超过支持的日期范围")
        return result
    zone = ZoneInfo(schedule["tz"])
    local = datetime.fromtimestamp(now_ms / 1000, timezone.utc).astimezone(zone)
    iterator = croniter(schedule["expr"], local.replace(tzinfo=None), max_years_between_matches=50)
    while True:
        wall = iterator.get_next(datetime)
        candidate = wall.replace(tzinfo=zone, fold=0)
        absolute = candidate.astimezone(timezone.utc)
        if absolute.astimezone(zone).replace(tzinfo=None) != wall:
            continue
        stamp = int(absolute.timestamp() * 1000)
        if stamp > now_ms:
            return stamp
