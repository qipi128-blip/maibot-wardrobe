"""衣橱数据模型、TOML 读写与每日选衣算法。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import hashlib
import json
import os
import random
import time
import tomllib

from .calendar_rules import SEASON_IDS, active_events, season_of

_DEFAULT_HEADER = """# 「她的衣橱」数据文件
#
# 这个文件由插件自动生成，也可以手动编辑；修改后无需重启，下一次读取时会自动生效。
#
# 一套衣服：
#   [[outfits]]
#   id = "spring_dress"          # 必填，只能使用字母、数字、下划线和短横线
#   name = "春日白裙"             # 展示名
#   description = "……"          # 给模型看的穿搭描述
#   tags = ["日常", "清新"]
#   seasons = ["spring"]         # 留空表示四季通用；可选 spring/summer/autumn/winter
#   weight = 1.0                 # 抽取权重
#   enabled = true
#   images = []                  # 相对 data/plugins/<id>/ 的图片路径，上传命令会自动维护
#   vision_description = ""      # 视觉模型自动生成的描述
#
# 一个特殊日子：
#   [[events]]
#   id = "birthday"
#   name = "生日"
#   priority = 200               # 数字越大优先级越高；同优先级的事件会合并
#   dates = ["10-28"]            # 每年重复的 MM-DD，或 YYYY-MM-DD
#   ranges = ["12-24..12-26"]    # 区间，支持跨年
#   lunar_dates = ["08-15"]      # 农历；需要安装 lunardate，闰月写成 "闰04-01"
#   weekdays = [5]               # 可选，ISO 星期：1=周一 … 7=周日
#   months = [10]                # 可选，限制月份
#   seasons = ["winter"]         # 可选，限制季节
#   outfits = ["winter_coat"]    # 指定几套衣服，从中随机抽
#   note = "……"                  # 写进提示词的说明
#   exclusive = true             # true 时只从 outfits 里选
#
# 提示：上传图片时可以直接用 /衣橱上传 <id> <名字> 自动创建一套衣服。
"""


class WardrobeError(ValueError):
    """衣橱文件格式错误。"""


def safe_outfit_id(raw: str) -> str:
    """校验并返回安全的衣服 ID；非法时抛出 ``ValueError``。"""

    value = str(raw or "").strip()
    if not value:
        raise ValueError("衣服 ID 不能为空")
    if not all(char.isascii() and (char.isalnum() or char in "_-") for char in value):
        raise ValueError("衣服 ID 只能使用英文字母、数字、下划线和短横线")
    if len(value) > 48:
        raise ValueError("衣服 ID 太长（最多 48 个字符）")
    return value


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in _normalize_str_list(value):
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


def _normalize_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number <= 0 or number != number:
        return default
    return number


def _normalize_images(value: Any) -> list[str]:
    images: list[str] = []
    for item in _normalize_str_list(value):
        normalized = item.replace("\\", "/").strip()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            continue
        if normalized not in images:
            images.append(normalized)
    return images


@dataclass
class Outfit:
    """一套衣服。"""

    id: str
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    weight: float = 1.0
    enabled: bool = True
    images: list[str] = field(default_factory=list)
    vision_description: str = ""
    prompt_extra: str = ""
    only_for_events: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Outfit":
        outfit_id = safe_outfit_id(str(raw.get("id", "")))
        seasons = [
            season
            for season in (str(item).strip().lower() for item in _normalize_str_list(raw.get("seasons")))
            if season in SEASON_IDS
        ]
        return cls(
            id=outfit_id,
            name=str(raw.get("name", "") or "").strip() or outfit_id,
            description=str(raw.get("description", "") or "").strip(),
            tags=_normalize_str_list(raw.get("tags")),
            seasons=list(dict.fromkeys(seasons)),
            weight=_normalize_float(raw.get("weight", 1.0), 1.0),
            enabled=bool(raw.get("enabled", True)),
            images=_normalize_images(raw.get("images")),
            vision_description=str(raw.get("vision_description", "") or "").strip(),
            prompt_extra=str(raw.get("prompt_extra", "") or "").strip(),
            only_for_events=bool(raw.get("only_for_events", False)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "seasons": list(self.seasons),
            "weight": self.weight,
            "enabled": self.enabled,
            "only_for_events": self.only_for_events,
            "images": list(self.images),
            "vision_description": self.vision_description,
            "prompt_extra": self.prompt_extra,
        }

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def season_text(self) -> str:
        if not self.seasons:
            return "四季"
        return "、".join(SEASON_IDS_LABELS.get(item, item) for item in self.seasons)

    def prompt_description(self) -> str:
        parts = [self.description, self.vision_description, self.prompt_extra]
        return "；".join(part.strip().rstrip("。；") for part in parts if part and part.strip())


SEASON_IDS_LABELS = {
    "spring": "春",
    "summer": "夏",
    "autumn": "秋",
    "winter": "冬",
}


@dataclass
class WardrobeEvent:
    """特殊日子或季节事件。"""

    id: str
    name: str = ""
    enabled: bool = True
    priority: int = 100
    dates: list[str] = field(default_factory=list)
    ranges: list[str] = field(default_factory=list)
    lunar_dates: list[str] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=list)
    months: list[int] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    outfits: list[str] = field(default_factory=list)
    note: str = ""
    exclusive: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WardrobeEvent":
        event_id = str(raw.get("id", "") or "").strip()
        if not event_id:
            raise WardrobeError("事件缺少 id")
        weekdays = [item for item in _normalize_int_list(raw.get("weekdays")) if 1 <= item <= 7]
        months = [item for item in _normalize_int_list(raw.get("months")) if 1 <= item <= 12]
        seasons = [
            season
            for season in (str(item).strip().lower() for item in _normalize_str_list(raw.get("seasons")))
            if season in SEASON_IDS
        ]
        try:
            priority = int(raw.get("priority", 100))
        except (TypeError, ValueError):
            priority = 100
        return cls(
            id=event_id,
            name=str(raw.get("name", "") or "").strip() or event_id,
            enabled=bool(raw.get("enabled", True)),
            priority=priority,
            dates=[str(item).strip() for item in _normalize_str_list(raw.get("dates")) if str(item).strip()],
            ranges=[str(item).strip() for item in _normalize_str_list(raw.get("ranges")) if str(item).strip()],
            lunar_dates=[
                str(item).strip() for item in _normalize_str_list(raw.get("lunar_dates")) if str(item).strip()
            ],
            weekdays=list(dict.fromkeys(weekdays)),
            months=list(dict.fromkeys(months)),
            seasons=list(dict.fromkeys(seasons)),
            outfits=[str(item).strip() for item in _normalize_str_list(raw.get("outfits")) if str(item).strip()],
            note=str(raw.get("note", "") or "").strip(),
            exclusive=bool(raw.get("exclusive", True)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "dates": list(self.dates),
            "ranges": list(self.ranges),
            "lunar_dates": list(self.lunar_dates),
            "weekdays": list(self.weekdays),
            "months": list(self.months),
            "seasons": list(self.seasons),
            "outfits": list(self.outfits),
            "note": self.note,
            "exclusive": self.exclusive,
        }


@dataclass
class Wardrobe:
    """整个衣橱。"""

    outfits: list[Outfit] = field(default_factory=list)
    events: list[WardrobeEvent] = field(default_factory=list)

    def get_outfit(self, outfit_id: str) -> Outfit | None:
        normalized = str(outfit_id or "").strip()
        for outfit in self.outfits:
            if outfit.id == normalized:
                return outfit
        return None

    def get_event(self, event_id: str) -> WardrobeEvent | None:
        normalized = str(event_id or "").strip()
        for event in self.events:
            if event.id == normalized:
                return event
        return None

    @property
    def enabled_outfits(self) -> list[Outfit]:
        return [outfit for outfit in self.outfits if outfit.enabled]

    @property
    def rotation_outfits(self) -> list[Outfit]:
        """参与日常轮换的衣服（排除只在特殊日子穿的衣服）。"""

        return [outfit for outfit in self.outfits if outfit.enabled and not outfit.only_for_events]

    def ensure_outfit(self, outfit_id: str, name: str = "", description: str = "") -> tuple[Outfit, bool]:
        """返回指定 ID 的衣服；不存在时创建。返回 ``(衣服, 是否新建)``。"""

        existing = self.get_outfit(outfit_id)
        if existing is not None:
            return existing, False
        outfit = Outfit(
            id=safe_outfit_id(outfit_id),
            name=name.strip() or outfit_id,
            description=description.strip(),
        )
        self.outfits.append(outfit)
        return outfit, True

    def remove_outfit(self, outfit_id: str) -> bool:
        before = len(self.outfits)
        self.outfits = [outfit for outfit in self.outfits if outfit.id != outfit_id]
        return len(self.outfits) != before

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Wardrobe":
        outfits: list[Outfit] = []
        events: list[WardrobeEvent] = []
        seen_ids: set[str] = set()
        for item in raw.get("outfits", []) or []:
            if not isinstance(item, Mapping):
                continue
            outfit = Outfit.from_mapping(item)
            if outfit.id in seen_ids:
                raise WardrobeError(f"衣服 ID 重复: {outfit.id}")
            seen_ids.add(outfit.id)
            outfits.append(outfit)

        seen_events: set[str] = set()
        for item in raw.get("events", []) or []:
            if not isinstance(item, Mapping):
                continue
            event = WardrobeEvent.from_mapping(item)
            if event.id in seen_events:
                raise WardrobeError(f"事件 ID 重复: {event.id}")
            seen_events.add(event.id)
            events.append(event)
        return cls(outfits=outfits, events=events)

    @classmethod
    def from_toml(cls, text: str) -> "Wardrobe":
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise WardrobeError(f"TOML 解析失败: {exc}") from exc
        if not isinstance(raw, dict):
            raise WardrobeError("衣橱文件格式不正确")
        return cls.from_mapping(raw)

    def to_toml(self) -> str:
        lines: list[str] = [_DEFAULT_HEADER.rstrip(), ""]
        for outfit in self.outfits:
            lines.append("[[outfits]]")
            for key, value in outfit.to_mapping().items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")

        for event in self.events:
            lines.append("[[events]]")
            for key, value in event.to_mapping().items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class WardrobeStore:
    """负责 ``wardrobe.toml`` 的加载、热更新与保存。"""

    def __init__(
        self,
        path: Path,
        *,
        template_path: Path | None = None,
        logger: Any = None,
    ) -> None:
        self.path = Path(path)
        self.template_path = Path(template_path) if template_path else None
        self.logger = logger
        self.wardrobe = Wardrobe()
        self.last_error = ""
        self._mtime_ns = -1
        self._size = -1
        self._loaded = False

    def ensure_loaded(self, *, force: bool = False) -> Wardrobe:
        try:
            stat = self.path.stat()
            current_mtime = stat.st_mtime_ns
            current_size = stat.st_size
        except FileNotFoundError:
            current_mtime = -1
            current_size = -1

        if not force and self._loaded and current_mtime == self._mtime_ns and current_size == self._size:
            return self.wardrobe

        if not self.path.exists():
            self._initialize_file()
            try:
                stat = self.path.stat()
                current_mtime = stat.st_mtime_ns
                current_size = stat.st_size
            except FileNotFoundError:
                current_mtime = -1
                current_size = -1

        try:
            text = self.path.read_text(encoding="utf-8")
            self.wardrobe = Wardrobe.from_toml(text)
            self.last_error = ""
            self._mtime_ns = current_mtime
            self._size = current_size
            self._loaded = True
            if self.logger is not None:
                self.logger.info(
                    "衣橱已加载：%d 套衣服，%d 个事件", len(self.wardrobe.outfits), len(self.wardrobe.events)
                )
        except Exception as exc:  # noqa: BLE001 - 用户文件错误不应中断聊天
            self.last_error = str(exc)
            if self.logger is not None:
                self.logger.warning("衣橱文件读取失败，将继续使用上一次成功加载的数据: %s", exc)
            if not self._loaded:
                self.wardrobe = Wardrobe()
        return self.wardrobe

    def save(self, wardrobe: Wardrobe | None = None) -> None:
        target = wardrobe if wardrobe is not None else self.wardrobe
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = target.to_toml()
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, self.path)
        self.wardrobe = target
        self._loaded = True
        try:
            stat = self.path.stat()
            self._mtime_ns = stat.st_mtime_ns
            self._size = stat.st_size
        except FileNotFoundError:
            self._mtime_ns = -1
            self._size = -1

    def _initialize_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.template_path is not None and self.template_path.is_file():
            text = self.template_path.read_text(encoding="utf-8")
        else:
            text = Wardrobe().to_toml()
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, self.path)


@dataclass
class Selection:
    """某一天的选衣结果。"""

    day: str
    outfit_id: str
    season: str
    source: str
    event_ids: list[str] = field(default_factory=list)
    note: str = ""
    picked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.day,
            "outfit_id": self.outfit_id,
            "season": self.season,
            "source": self.source,
            "event_ids": list(self.event_ids),
            "note": self.note,
            "picked_at": self.picked_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Selection | None":
        outfit_id = str(raw.get("outfit_id", "") or "").strip()
        day = str(raw.get("date", "") or "").strip()
        if not outfit_id or not day:
            return None
        return cls(
            day=day,
            outfit_id=outfit_id,
            season=str(raw.get("season", "") or ""),
            source=str(raw.get("source", "") or ""),
            event_ids=_normalize_str_list(raw.get("event_ids")),
            note=str(raw.get("note", "") or ""),
            picked_at=str(raw.get("picked_at", "") or ""),
        )


def _weighted_pick(pool: Sequence[Outfit], *, seed_text: str) -> Outfit:
    ids = sorted(outfit.id for outfit in pool)
    digest = hashlib.sha256(f"{seed_text}|{','.join(ids)}".encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    weights = [max(0.001, float(outfit.weight or 1.0)) for outfit in pool]
    return rng.choices(list(pool), weights=weights, k=1)[0]


def select_outfit(
    wardrobe: Wardrobe,
    day: date,
    *,
    season_id: str | None = None,
    events_enabled: bool = True,
    rotation_enabled: bool = True,
    avoid_ids: Iterable[str] = (),
    avoid_repeat_days: int = 7,
    prefer_season: bool = True,
    fallback_to_all: bool = True,
    min_pool_size: int = 1,
    seed: str = "",
    hemisphere: str = "northern",
    season_ranges: Mapping[str, str] | None = None,
) -> Selection | None:
    """为指定日期挑选一套衣服。

    优先级：特殊事件指定衣服 > 当季衣服 > 全部衣服。
    结果对同一天、同一候选池是稳定的，方便重启后保持一致。
    """

    current_season = season_id or season_of(day, hemisphere=hemisphere, ranges=season_ranges)
    enabled = wardrobe.enabled_outfits
    rotation_pool = wardrobe.rotation_outfits or enabled
    if not enabled:
        return None

    matched_events = active_events(wardrobe.events, day, current_season) if events_enabled else []
    event_ids = [event.id for event in matched_events]
    notes = "；".join(event.note for event in matched_events if event.note)

    pool: list[Outfit] = []
    source = "all"
    if matched_events:
        top_priority = max(int(event.priority) for event in matched_events)
        top_events = [event for event in matched_events if int(event.priority) == top_priority]
        requested_ids: list[str] = []
        exclusive = False
        for event in top_events:
            requested_ids.extend(event.outfits)
            exclusive = exclusive or event.exclusive
        for outfit_id in dict.fromkeys(requested_ids):
            outfit = wardrobe.get_outfit(outfit_id)
            if outfit is not None and outfit.enabled:
                pool.append(outfit)
        if pool:
            source = "event"
        elif exclusive:
            pool = []

    if not pool:
        if prefer_season:
            pool = [
                outfit
                for outfit in rotation_pool
                if not outfit.seasons or current_season in outfit.seasons
            ]
            source = "season"
        if not pool and fallback_to_all:
            pool = list(rotation_pool)
            source = "all"
        if not pool:
            pool = list(rotation_pool)
            source = "all"

    if not rotation_enabled:
        chosen = sorted(pool, key=lambda outfit: outfit.id)[0]
    else:
        avoid = {str(item) for item in avoid_ids if str(item)}
        if avoid_repeat_days > 0 and avoid:
            filtered = [outfit for outfit in pool if outfit.id not in avoid]
            if len(filtered) >= max(1, int(min_pool_size)):
                pool = filtered
        chosen = _weighted_pick(pool, seed_text=f"{seed}|{day.isoformat()}|{source}")

    return Selection(
        day=day.isoformat(),
        outfit_id=chosen.id,
        season=current_season,
        source=source,
        event_ids=event_ids,
        note=notes,
        picked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
