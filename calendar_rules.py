"""季节、农历与节日事件的日期匹配工具。

只依赖标准库；农历支持是可选的：安装了 ``lunardate`` 时自动启用，
未安装时农历规则会被跳过，其余功能不受影响。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

import re

SEASON_IDS: tuple[str, ...] = ("spring", "summer", "autumn", "winter")
SEASON_CN: dict[str, str] = {
    "spring": "春天",
    "summer": "夏天",
    "autumn": "秋天",
    "winter": "冬天",
}
WEEKDAY_CN: tuple[str, ...] = ("一", "二", "三", "四", "五", "六", "日")

_ANNUAL_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*$")
_ABSOLUTE_DATE_RE = re.compile(r"^\s*(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*$")
_RANGE_SPLIT_RE = re.compile(r"\s*(?:\.\.|~|～|—|至|到)\s*")

try:  # pragma: no cover - 取决于运行环境是否安装 lunardate
    from lunardate import LunarDate as _LunarDate
except Exception:  # noqa: BLE001 - 缺少可选依赖时静默降级
    _LunarDate = None


def has_lunar_support() -> bool:
    """返回当前环境是否可用农历转换。"""

    return _LunarDate is not None


def lunar_date_of(day: date) -> tuple[int, int, int, bool] | None:
    """把公历日期转换为 ``(农历年, 月, 日, 是否闰月)``。"""

    if _LunarDate is None:
        return None
    try:
        lunar = _LunarDate.from_solar_date(day.year, day.month, day.day)
    except Exception:  # noqa: BLE001 - 超出 1900-2099 时直接降级
        return None
    return int(lunar.year), int(lunar.month), int(lunar.day), bool(lunar.is_leap_month)


def season_of(
    day: date,
    *,
    hemisphere: str = "northern",
    ranges: Mapping[str, str] | None = None,
) -> str:
    """计算日期所属季节，返回 ``spring``/``summer``/``autumn``/``winter``。"""

    for season_id in SEASON_IDS:
        range_text = str((ranges or {}).get(season_id, "") or "").strip()
        if range_text and _annual_range_contains(day, range_text):
            return season_id

    month = day.month
    southern = str(hemisphere or "").strip().lower() == "southern"
    if southern:
        month = ((month + 5) % 12) + 1
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def season_label(season_id: str) -> str:
    """返回季节的中文展示名。"""

    return SEASON_CN.get(str(season_id or "").strip().lower(), str(season_id or ""))


def parse_iso_date(value: str) -> date | None:
    """解析 ``YYYY-MM-DD``；失败时返回 ``None``。"""

    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_mmdd(value: str) -> tuple[int, int] | None:
    match = _ANNUAL_DATE_RE.fullmatch(str(value or ""))
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def _parse_absolute(value: str) -> date | None:
    match = _ABSOLUTE_DATE_RE.fullmatch(str(value or ""))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _annual_range_contains(day: date, range_text: str) -> bool:
    """判断日期是否落在 ``MM-DD..MM-DD`` 区间内（支持跨年）。"""

    parts = [part for part in _RANGE_SPLIT_RE.split(str(range_text or "").strip()) if part]
    if len(parts) != 2:
        partial = _parse_mmdd(range_text)
        return bool(partial and (day.month, day.day) == partial)
    start = _parse_mmdd(parts[0])
    end = _parse_mmdd(parts[1])
    if start is None or end is None:
        return False
    current = (day.month, day.day)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _absolute_range_contains(day: date, range_text: str) -> bool:
    parts = [part for part in _RANGE_SPLIT_RE.split(str(range_text or "").strip()) if part]
    if len(parts) != 2:
        return False
    start = _parse_absolute(parts[0])
    end = _parse_absolute(parts[1])
    if start is None or end is None:
        return False
    if start <= end:
        return start <= day <= end
    return day >= start or day <= end


def _range_contains(day: date, range_text: str) -> bool:
    """同时支持公历绝对区间和每年重复的 ``MM-DD..MM-DD`` 区间。"""

    parts = [part for part in _RANGE_SPLIT_RE.split(str(range_text or "").strip()) if part]
    if len(parts) == 2 and _parse_absolute(parts[0]) is not None and _parse_absolute(parts[1]) is not None:
        return _absolute_range_contains(day, range_text)
    return _annual_range_contains(day, range_text)


def _match_date_selector(selector: Any, day: date) -> bool:
    text = str(selector or "").strip()
    absolute = _parse_absolute(text)
    if absolute is not None:
        return absolute == day
    mmdd = _parse_mmdd(text)
    if mmdd is not None:
        return (day.month, day.day) == mmdd
    if any(separator in text for separator in ("..", "~", "～", "至", "到")):
        return _range_contains(day, text)
    return False


def _match_lunar_selector(selector: Any, lunar: tuple[int, int, int, bool] | None) -> bool:
    if lunar is None:
        return False
    text = str(selector or "").strip()
    if not text:
        return False

    lunar_year, month, day, is_leap = lunar
    leap_marker = False
    if text.startswith(("闰", "L", "l")):
        leap_marker = True
        text = text[1:].strip()
    elif text.startswith("+"):
        leap_marker = False
        text = text[1:].strip()

    absolute_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if absolute_match:
        target_year = int(absolute_match.group(1))
        target_month = int(absolute_match.group(2))
        target_day = int(absolute_match.group(3))
        return (
            lunar_year == target_year
            and month == target_month
            and day == target_day
            and (is_leap if leap_marker else not is_leap)
        )

    mmdd = _parse_mmdd(text)
    if mmdd is None:
        return False
    target_month, target_day = mmdd
    return month == target_month and day == target_day and (is_leap if leap_marker else not is_leap)


def event_matches(event: Any, day: date, season_id: str) -> bool:
    """判断事件是否在指定日期生效。

    ``dates`` / ``ranges`` / ``lunar_dates`` 之间是“或”的关系；
    ``weekdays`` / ``months`` / ``seasons`` 是额外的“与”过滤条件。
    一个没有任何日期或过滤条件的事件不会生效，避免误伤。
    """

    if event is None or not bool(getattr(event, "enabled", True)):
        return False

    dates = list(getattr(event, "dates", []) or [])
    ranges = list(getattr(event, "ranges", []) or [])
    lunar_dates = list(getattr(event, "lunar_dates", []) or [])
    weekdays = list(getattr(event, "weekdays", []) or [])
    months = list(getattr(event, "months", []) or [])
    seasons = list(getattr(event, "seasons", []) or [])

    has_date_selector = bool(dates or ranges or lunar_dates)
    has_filter = bool(weekdays or months or seasons)
    if not has_date_selector and not has_filter:
        return False

    if has_date_selector:
        matched = any(_match_date_selector(item, day) for item in dates)
        if not matched:
            matched = any(_range_contains(day, str(item)) for item in ranges)
        if not matched and lunar_dates:
            lunar = lunar_date_of(day)
            matched = any(_match_lunar_selector(item, lunar) for item in lunar_dates)
        if not matched:
            return False

    if weekdays and day.isoweekday() not in {int(item) for item in weekdays if str(item).strip().isdigit()}:
        return False
    if months and day.month not in {int(item) for item in months if str(item).strip().isdigit()}:
        return False
    if seasons:
        normalized = {str(item).strip().lower() for item in seasons}
        if season_id not in normalized:
            return False
    return True


def active_events(events: Iterable[Any], day: date, season_id: str) -> list[Any]:
    """返回指定日期生效的全部事件，按优先级从高到低排序。"""

    matched = [event for event in events if event_matches(event, day, season_id)]
    matched.sort(key=lambda item: (-int(getattr(item, "priority", 0) or 0), str(getattr(item, "id", ""))))
    return matched


def upcoming_events(
    events: Iterable[Any],
    start: date,
    days: int,
    *,
    hemisphere: str = "northern",
    ranges: Mapping[str, str] | None = None,
) -> list[tuple[date, Any]]:
    """返回未来若干天内每个事件最早出现的日期。

    同一天匹配多个事件时全部保留，并按日期、优先级排序。
    """

    horizon = max(0, int(days))
    seen: set[str] = set()
    result: list[tuple[date, Any]] = []
    for offset in range(horizon + 1):
        current = date.fromordinal(start.toordinal() + offset)
        season_id = season_of(current, hemisphere=hemisphere, ranges=ranges)
        for event in active_events(events, current, season_id):
            event_id = str(getattr(event, "id", "") or id(event))
            if event_id in seen:
                continue
            seen.add(event_id)
            result.append((current, event))
    return result
