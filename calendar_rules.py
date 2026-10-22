"""日期匹配：事件只支持一个日期/日期区间字符串。

支持格式：

- ``10-28``：每年 10 月 28 日
- ``2026-10-28``：仅 2026 年
- ``12-24..12-26``：每年 12 月 24 日到 26 日
- ``2026-12-24..2026-12-26``：指定年份区间
- 分隔符也支持 ``~``、``～``、``至``、``到``

```text
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import re

_ABS_RE = re.compile(r"^\s*(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*$")
_MMDD_RE = re.compile(r"^\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*$")
_RANGE_RE = re.compile(r"\s*(?:\.\.|~|～|—|至|到)\s*")


def parse_iso_date(value: str) -> date | None:
    """解析 ``YYYY-MM-DD``。"""

    text = str(value or "").strip()
    match = _ABS_RE.fullmatch(text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_absolute(value: str) -> date | None:
    return parse_iso_date(value)


def _parse_mmdd(value: str) -> tuple[int, int] | None:
    match = _MMDD_RE.fullmatch(str(value or ""))
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def _annual_range_contains(day: date, text: str) -> bool:
    parts = [part for part in _RANGE_RE.split(str(text or "").strip()) if part]
    if len(parts) != 2:
        return False
    start = _parse_mmdd(parts[0])
    end = _parse_mmdd(parts[1])
    if start is None or end is None:
        return False
    current = (day.month, day.day)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _absolute_range_contains(day: date, text: str) -> bool:
    parts = [part for part in _RANGE_RE.split(str(text or "").strip()) if part]
    if len(parts) != 2:
        return False
    start = _parse_absolute(parts[0])
    end = _parse_absolute(parts[1])
    if start is None or end is None:
        return False
    if start <= end:
        return start <= day <= end
    return day >= start or day <= end


def date_matches(text: str, day: date) -> bool:
    """判断事件日期字符串是否命中指定日期。"""

    normalized = str(text or "").strip()
    if not normalized:
        return False

    absolute = _parse_absolute(normalized)
    if absolute is not None:
        return absolute == day
    mmdd = _parse_mmdd(normalized)
    if mmdd is not None:
        return (day.month, day.day) == mmdd

    parts = [part for part in _RANGE_RE.split(normalized) if part]
    if len(parts) == 2:
        if _parse_absolute(parts[0]) is not None and _parse_absolute(parts[1]) is not None:
            return _absolute_range_contains(day, normalized)
        return _annual_range_contains(day, normalized)
    return False


def is_valid_date_expression(text: str) -> bool:
    """校验事件日期字符串格式是否可解析。"""

    normalized = str(text or "").strip()
    if not normalized:
        return False
    if _parse_absolute(normalized) is not None or _parse_mmdd(normalized) is not None:
        return True
    parts = [part for part in _RANGE_RE.split(normalized) if part]
    if len(parts) != 2:
        return False
    if _parse_absolute(parts[0]) is not None and _parse_absolute(parts[1]) is not None:
        return True
    return _parse_mmdd(parts[0]) is not None and _parse_mmdd(parts[1]) is not None


def active_events(events: Iterable[Any], day: date) -> list[Any]:
    """返回当天生效的事件。"""

    matched = [
        event
        for event in events
        if bool(getattr(event, "enabled", True)) and date_matches(getattr(event, "date", ""), day)
    ]
    matched.sort(key=lambda item: str(getattr(item, "id", "")))
    return matched
