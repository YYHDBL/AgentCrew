"""成熟 croniter 解析及 ZoneInfo 时间计算，调用者显式提供当前时刻。"""

import re
from datetime import datetime, timezone, timedelta
from bisect import bisect_left, bisect_right
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


def missed_range(schedule, scheduled_ms, now_ms, anchor_ms):
    validate_schedule(schedule)
    if now_ms < scheduled_ms:
        raise ScheduleError("错过范围的结束时间早于发生时间")
    if schedule["kind"] == "at":
        return {"count": 1, "through": scheduled_ms, "next": None}
    if schedule["kind"] == "every":
        count = (now_ms - scheduled_ms) // schedule["every_ms"] + 1
        through = scheduled_ms + (count - 1) * schedule["every_ms"]
        return {"count": count, "through": through, "next": through + schedule["every_ms"]}
    zone = ZoneInfo(schedule["tz"])
    first = datetime.fromtimestamp(scheduled_ms / 1000, timezone.utc).astimezone(zone)
    last = datetime.fromtimestamp(now_ms / 1000, timezone.utc).astimezone(zone)
    fields = schedule["expr"].split()
    expanded = croniter(schedule["expr"], first.replace(tzinfo=None)).expanded
    minutes = list(range(60)) if expanded[0] == ["*"] else expanded[0]
    seconds = ([0] if len(fields) == 5 else list(range(60)) if expanded[5] == ["*"] else expanded[5])
    hour_expression = " ".join(["0", *fields[1:5]])
    base = first.replace(tzinfo=None, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    upper = last.replace(tzinfo=None, minute=0, second=0, microsecond=0)
    hours = croniter(hour_expression, base, max_years_between_matches=50)
    count, through = 0, None
    while True:
        hour = hours.get_next(datetime)
        if hour > upper:
            break
        for minute in minutes:
            wall = hour.replace(minute=minute)
            beginning = wall.replace(tzinfo=zone, fold=0)
            ending = (wall + timedelta(seconds=59)).replace(tzinfo=zone, fold=0)
            first_valid = beginning.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall
            last_valid = ending.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall + timedelta(seconds=59)
            if not first_valid and not last_valid:
                continue
            if first_valid and last_valid and beginning.utcoffset() == ending.utcoffset():
                stamp = int(beginning.timestamp() * 1000)
                left = bisect_left(seconds, max(0, (scheduled_ms - stamp + 999) // 1000))
                right = bisect_right(seconds, min(59, (now_ms - stamp) // 1000))
                if right > left:
                    count += right - left
                    through = max(through or 0, stamp + seconds[right - 1] * 1000)
            else:
                for second in seconds:
                    candidate = (wall + timedelta(seconds=second)).replace(tzinfo=zone, fold=0)
                    absolute = candidate.astimezone(timezone.utc)
                    if absolute.astimezone(zone).replace(tzinfo=None) != candidate.replace(tzinfo=None):
                        continue
                    stamp = int(absolute.timestamp() * 1000)
                    if scheduled_ms <= stamp <= now_ms:
                        count += 1
                        through = max(through or 0, stamp)
    if not count:
        raise ScheduleError("持久化的到期时间不属于该cron表达式")
    return {"count": count, "through": through, "next": next_run_at(schedule, now_ms, anchor_ms)}
